"""Read-only on-demand task context records; full history stays in storage.

Task access is derived solely from the active job, not model arguments.
The model receives a SMALL window of the specific archived record it asks for.
Only a content hash, lookup target, and outcome should remain in tool evidence.
"""
from __future__ import annotations
from hashlib import sha256
import json
from typing import Any
from .secret_redaction import redact


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_task_record",
        "description": "Look up a missing or questionable prior task record ON DEMAND, without importing its entire history into context. Use only if the compact progress index/evidence cursor is insufficient. N1 monitors duplicate lookups.",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type":"string","enum":["current","recovery_parent"],"description":"Current task, or recovery parent only when assigned by runtime."},
                "kind": {"type":"string","enum":["plan_index","step_result","context_slice","result_ledger"],"description":"Exact category of stored evidence required."},
                "step_id":{"type":"string","description":"Step whose result/archive is being examined; required for step_result and context_slice."},
                "slice_index":{"type":"integer","minimum":1,"description":"Context slice index; omit for latest slice."},
                "message_index":{"type":"integer","minimum":0,"description":"One message in a context_slice; prevents loading the entire model transcript."},
                "start_char":{"type":"integer","minimum":0,"description":"Character offset in the chosen record for paged inspection."},
                "max_chars":{"type":"integer","minimum":1,"maximum":5000},
                "n1_need":{"type":"string","description":"Brief reason for this lookup; cite missing/contradictory context."},
            },
            "required":["scope","kind"],
        },
    },
}


def fetch(job: Any, durable: Any, live: Any, args: dict) -> dict:
    scope=str(args.get("scope") or "current")
    if scope not in {"current","recovery_parent"}:
        raise ValueError("Invalid task record scope")
    metadata=dict(job.metadata or {})
    if scope=="current":
        task_id=str(job.task_id)
    else:
        if not metadata.get("recovery_mode"):
            raise PermissionError("Parent record lookup is only for an authorized recovery task")
        task_id=str(metadata.get("parent_task_id") or "").strip()
        if not task_id:
            raise PermissionError("Recovery parent task not recorded by Norm")
    kind=str(args.get("kind") or "")
    step_id=str(args.get("step_id") or "").strip()
    if len(step_id)>200:
        raise ValueError("Step identifier too long")
    if kind not in {"plan_index","step_result","context_slice","result_ledger"}:
        raise ValueError("Unsupported task record kind")
    if kind in {"context_slice","step_result"} and not step_id:
        raise ValueError("Step id required for this lookup")
    limit=max(1,min(5000,int(args.get("max_chars") or 3500)))
    offset=max(0,int(args.get("start_char") or 0))
    if offset>2_000_000_000:
        raise ValueError("Lookup offset out of bounds")

    if kind=="plan_index":
        state=durable.task_plan_snapshot(task_id)
        if not isinstance(state,dict):
            return {"ok":False,"found":False,"kind":kind,"task_id":task_id}
        obj={"task_id":task_id,"steps":state.get("steps",[]),
             "status":state.get("status"),"title":state.get("title")}
    elif kind=="step_result":
        records=durable.terminal_step_records(task_id,excluded_step_id="")
        matches=[v for v in records if str(v.get("id") or "")==step_id]
        if not matches:
            return {"ok":False,"found":False,"kind":kind,"task_id":task_id,"step_id":step_id}
        obj=matches[0]
    elif kind=="context_slice":
        requested=args.get("slice_index")
        archive=(durable.context_slice(task_id,step_id,int(requested))
                 if requested is not None else durable.latest_context_slice(task_id,step_id))
        if not isinstance(archive,dict):
            return {"ok":False,"found":False,"kind":kind,"task_id":task_id,"step_id":step_id}
        messages=archive.get("messages") or []
        if not isinstance(messages,list):
            return {"ok":False,"found":False,"kind":kind,"task_id":task_id,"step_id":step_id}
        index=args.get("message_index")
        if index is None:
            # Index only. Never implicitly load the 300k message transcript.
            obj={"slice_index":archive.get("slice_index"),
                 "reason":archive.get("reason"),"message_count":len(messages),
                 "messages":[{"index":i,"role":str(msg.get("role") or "")[:40],
                              "chars":len(str(msg.get("content") or "")),
                              "kind":str(msg.get("kind") or "")[:80]}
                             for i,msg in enumerate(messages) if isinstance(msg,dict)][:120]}
        else:
            i=int(index)
            if i<0 or i>=len(messages):
                return {"ok":False,"found":False,"kind":kind,"task_id":task_id,
                        "step_id":step_id,"message_index":i}
            value=messages[i]
            obj={"slice_index":archive.get("slice_index"),
                 "message_index":i,"role":str(value.get("role") or "")[:40],
                 "content":str(value.get("content") or "")}
    else:
        if live is None or not hasattr(live,"result_ledger"):
            raise RuntimeError("Redis result ledger is unavailable")
        data=live.result_ledger(task_id,limit=40) or []
        # Do not import the full tool return into the prompt by default.
        obj=[{"step_id":str(x.get("step_id") or "")[:120],
              "tool":str(x.get("tool") or "")[:140],
              "result":{key:value for key,value in (x.get("result") or {}).items()
                        if key in {"ok","path","pdf","sha256","size","file_size","dispatch_id","status"}}}
             for x in data if isinstance(x,dict) and
             (not step_id or str(x.get("step_id") or "")==step_id)]
    text=json.dumps(obj,ensure_ascii=False,default=str)
    digest=sha256(text.encode("utf8")).hexdigest()
    part=text[offset:offset+limit]
    result={
        "ok":True,"found":True,"task_id":task_id,
        "kind":kind,"step_id":step_id,
        "record_sha256":digest,"total_chars":len(text),
        "start_char":offset,"next_char":offset+len(part),
        "truncated":offset+len(part)<len(text),
        "content":redact(part),
    }
    if kind=="context_slice" and isinstance(obj,dict):
        result["slice_index"]=obj.get("slice_index")
    if kind=="context_slice" and args.get("message_index") is not None:
        result["message_index"]=int(args["message_index"])
    return result


def receipt(result: dict) -> dict:
    """Retain lookup identity/outcome for N1; never the recovered raw contents."""
    return {k:v for k,v in result.items() if k!="content"}
