"""Same-task delivery of N1-approved asynchronous results to N2."""
from __future__ import annotations
import json,logging,time
from norm_runtime.n1_gatekeeper import GateOutcome

class N1AsyncBridge:
    def __init__(self, owner, job, tools):
        self.owner,self.job,self.tools=owner,job,tools
        self.store=owner.queue.r
        self.key=f"norm:async-link:{job.task_id}:{job.step_id}"
        self.pending=[]
        self.broker=None
        try:
            from norm_runtime.celery_tool_executor import Broker,CFG
            self.broker=Broker()
            saved=self.store.get(self.key)
            if saved:
                raw=json.loads(saved)
                self.pending=raw if isinstance(raw,list) else []
            self.enabled=CFG["enabled"] and self.broker.available()
        except Exception:
            logging.exception("Celery adapter unavailable; existing synchronous tools retained")
            self.enabled=False
    def persist(self):
        if self.pending:
            self.store.set(self.key,json.dumps(self.pending,ensure_ascii=False),ex=172800)
        else:
            self.store.delete(self.key)
    def receipt(self, name,arguments,outcome):
        # Only operations already classified by N1 as information/inspection
        # can run in the initial asynchronous rollout. Mutations still use
        # the existing synchronous N1-gated path until cancellation/rollback
        # semantics are explicitly implemented and tested.
        if (not self.enabled or name == "read_file" or
                not self.owner.n1_gatekeeper.information_tool(name)):
            return None
        signature=json.dumps({"name":name,"args":arguments},sort_keys=True,default=str)
        for item in self.pending:
            if item["signature"]==signature:
                return {"ok":True,"tool":name,"async_pending":True,
                        "status":"pending","dispatch_id":item["id"],"deduplicated":True}
        first=(outcome.candidates or [None])[0]
        candidate=[]
        if isinstance(first,dict):
            candidate=[{k:first.get(k) for k in ("record_id","scope","description_match")}]
        result=self.broker.submit(self.job.task_id,self.job.step_id,name,arguments)
        self.pending.append({"id":result,"signature":signature,"name":name,
            "arguments":arguments,"outcome":{
                "execute":True,"arguments":arguments,"target":outcome.target,
                "need":outcome.need,"scope":outcome.scope,
                "streamed_read":outcome.streamed_read,
                "signature":outcome.signature,"candidates":candidate}})
        self.persist()
        return {"ok":True,"tool":name,"status":"pending",
                "async_pending":True,"dispatch_id":result}
    def deliver(self,messages,evidence):
        if not self.pending or self.broker is None:return 0
        completed=0
        for item in list(self.pending):
            try:result=self.broker.poll(item["id"])
            except Exception:
                logging.exception("Async result fetch error for %s",item["id"])
                continue
            if result is None:continue
            outcome=GateOutcome(**item["outcome"])
            meta=self.owner.n1_gatekeeper.after_tool(
                task_id=self.job.task_id,step_id=self.job.step_id,
                name=item["name"],outcome=outcome,result=result)
            item_evidence={"tool":item["name"],"arguments":item["arguments"],
                "result":result,"n1_gatekeeper":meta,
                "async_dispatch_id":item["id"]}
            from norm_runtime.secret_redaction import redact
            item_evidence=redact(item_evidence)
            evidence.append(item_evidence)
            self.owner._persist_evidence_now(self.job,[item_evidence])
            messages.append({"role":"system","content":
                "[N1 ASYNC TOOL RESULT; NOT USER-AUTHORED]\n"
                +json.dumps({"tool":item["name"],"dispatch_id":item["id"],
                             "result":redact(result)},ensure_ascii=False,default=str)
                +"\nUse this observed result for the current task. Do not repeat completed work."})
            self.pending.remove(item)
            completed+=1
            self.persist()
        return completed
    def wait_for_one(self,messages,evidence,seconds=90):
        end=time.monotonic()+seconds
        while self.pending and time.monotonic()<end:
            if self.owner._drain.is_set() or self.owner._suppression_requested(self.job.task_id):
                return False
            if self.deliver(messages,evidence):return True
            time.sleep(0.6)
        return False
    def inject_receipts(self,messages):
        if self.pending:
            messages.append({"role":"system","content":
                "[N1 PENDING TOOL WORK]\n"
                +json.dumps([{"tool":x["name"],"dispatch_id":x["id"]} for x in self.pending])
                +"\nThese have already been dispatched. Continue independent work, do not repeat them, "
                "and do not give a final answer that depends on them until N1 delivers results."})