"""Retired async transport compatibility: new N1-approved tools execute directly.

Old Celery receipts are never replayed. They become explicit N1-observed
unresolved outcomes when a parked or pre-migration task resumes.
"""
from __future__ import annotations
import hashlib
import json
import logging
import time
from norm_runtime.n1_gatekeeper import GateOutcome
from norm_runtime.secret_redaction import redact

class N1AsyncBridge:
    def __init__(self, owner, job, tools):
        self.owner,self.job,self.tools=owner,job,tools
        self.store=owner.queue.r
        self.key=f"norm:async-link:{job.task_id}:{job.step_id}"
        self.enabled=False
        self.broker=None
        self.pending=[]
        try:
            saved=self.store.get(self.key)
            rows=json.loads(saved) if saved else []
            self.pending=rows if isinstance(rows,list) else []
        except Exception:
            logging.exception("Could not read legacy asynchronous receipts")
            raise
    def persist(self):
        if self.pending:
            self.store.set(self.key,json.dumps(self.pending,ensure_ascii=False),ex=7200)
        else:self.store.delete(self.key)
    @staticmethod
    def _signature(name,args):
        return json.dumps({"name":name,"args":args},sort_keys=True,default=str)
    def _unresolved_key(self,signature):
        return "norm:async-unresolved:"+self.job.task_id+":"+hashlib.sha256(signature.encode("utf8")).hexdigest()
    def receipt(self,name,arguments,outcome):
        # Direct execution remains N1-gated in prompt_worker. A historical
        # unresolved identical dispatch must not execute twice automatically.
        signature=self._signature(name,arguments)
        if any(row.get("signature")==signature for row in self.pending):
            return {"ok":False,"tool":name,"status":"legacy_async_unresolved",
                    "error":"Previous transport dispatch requires N1 reconciliation; no automatic replay."}
        old=self.store.get(self._unresolved_key(signature))
        if old:
            return {"ok":False,"tool":name,"status":"legacy_async_unresolved",
                    "error":"Prior dispatch outcome unknown; no automatic duplicate execution."}
        return None
    def next_due(self):
        return time.time() if self.pending else 0
    def inject_receipts(self,messages):
        if self.pending:
            messages.append({"role":"system","content":
              "[LEGACY ASYNC RECEIPTS PENDING N1 RECONCILIATION; NOT USER-AUTHORED] "
              "Never redispatch historical tool operations merely because Celery was removed."})
    def deliver(self,messages,evidence,*,now=None):
        count=0
        for item in list(self.pending):
            dispatch=str(item.get("id") or "unknown")
            name=str(item.get("name") or "unknown")
            result={"ok":False,"tool":name,"status":"legacy_async_unresolved",
                    "async_unresolved":True,"dispatch_id":dispatch,
                    "error":"Celery transport removed. Previous execution outcome unconfirmed; do not automatically retry."}
            meta={}
            try:
                outcome=GateOutcome(**item["outcome"])
                meta=self.owner.n1_gatekeeper.after_tool(
                    task_id=self.job.task_id,step_id=self.job.step_id,
                    name=name,outcome=outcome,result=result)
            except Exception:
                logging.exception("Legacy receipt needs manual N1 reconciliation dispatch=%s",dispatch)
                meta={"status":"review_required","reason":"legacy receipt not verifiable"}
            observed=redact({"tool":name,"arguments":item.get("arguments") or {},
                "result":result,"n1_gatekeeper":meta,"async_dispatch_id":dispatch})
            self.owner._persist_evidence_now(self.job,[observed])
            evidence.append(observed)
            messages.append({"role":"system","content":
                "[N1 LEGACY TOOL RECEIPT; NOT USER-AUTHORED]\n"+
                json.dumps(redact(result),ensure_ascii=False)+
                "\nOutcome unknown. Do not automatically repeat the operation."})
            signature=item.get("signature") or self._signature(name,item.get("arguments") or {})
            self.store.set(self._unresolved_key(signature),dispatch,ex=7*86400)
            self.pending.remove(item);self.persist();count+=1
        return count
