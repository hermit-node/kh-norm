"""r7 offline reconstruction contract: Redis evidence corroborates step list;
the failing parent builds remaining steps BEFORE successor creation.
"""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.live_log import RedisTaskLog

PROMPT="Produce a report including the required screenshot."
PATH=r"C:\\Users\\KHzz\\Documents\\Norm\\temp\\desktop-screenshot-recovery.jpg"
STEP_IDS=["plan-breakdown","verify-plan","step-01","step-02","final-verify"]
class Redis:
    def __init__(self):self.data={}
    def get(self,k):return self.data.get(k)
    def setex(self,k,ttl,v):self.data[k]=v
class Queue:
    def __init__(self):self.r=Redis();self.saved=[]
    def contains_message_id(self,v):return any(j.message_id==v for j in self.saved)
    def enqueue_chain(self,jobs):self.saved.extend(jobs)
class Durable:
    def __init__(self):self.children={};self.notes=[];self.checkpoints=[]
    def task_lineage(self,t):return {"task_depth":0}
    def task_plan_snapshot(self,t):return {"original_request":PROMPT,"steps":[
        {"id":v,"name":v,"status":"completed" if v in ("plan-breakdown","verify-plan","step-01") else "failed" if v=="step-02" else "pending"} for v in STEP_IDS]}
    def task_status(self,t):return "running" if t in self.children else None
    def start_child_task(self,plan,parent,step,**kwargs):
        assert parent=="parent" and kwargs["task_kind"]=="recovery"
        self.children[plan.task_id]=plan
    def checkpoint_step(self,*args):self.checkpoints.append(args)
    def record_maintenance_note(self,*args,**kw):self.notes.append((args,kw))
class Live:
    def __init__(self):
        self.started=[]
        self.plan={"steps":[
            {"id":v,"name":v,"description":"Take screenshot" if v=="step-01" else "Write report referencing screenshot" if v=="step-02" else v,
             "verify":"Evidence verified"} for v in STEP_IDS]}
    def state(self,tid):assert tid=="parent";return {"plan":json.dumps(self.plan)}
    def events(self,tid,count=100):
        return list(reversed([
            ("1",{"event":"step_started","step_id":"step-01"}),
            ("2",{"event":"step_completed","step_id":"step-01"}),
            ("3",{"event":"step_started","step_id":"step-02"}),
            ("4",{"event":"step_failed","step_id":"step-02"}),
        ]))
    def task_evidence(self,tid):return [{"tool":"desktop_screenshot","result":{"ok":True,"path":PATH,"width":5760,"height":1080,"file_size":493187}}]
    def result_ledger(self,tid,limit=64):return [{"step_id":"step-01","tool":"desktop_screenshot","result":{"ok":True,"path":PATH,"width":5760,"height":1080,"file_size":493187}}]
    def start_task(self,tid,title,plan):self.started.append((tid,plan))
    def step_started(self,*args):pass
    def step_completed(self,*args):pass
class Gate:
    def __init__(self):self.seen=None
    def reconstruct_remaining_recovery_steps(self,**kw):
        self.seen=kw
        assert kw["unresolved_steps"][0]["id"]=="step-02"
        assert kw["completed_steps"][0]["id"]=="step-01"
        assert kw["redis_results"][0]["result"]["path"]==PATH
        return [{"name":"Finish report","instruction":f"Use already captured screenshot at {PATH}; do not recapture.","verify":"Report uses observed file metadata","source_step_ids":["step-02"]}]
j=PromptJob(message_id="parent-node",task_id="parent",step_id="step-02",
    prompt="Finish report",project_id="default",context="N2 claimed image location unknown",
    created_at=0,attempt=0,metadata={"original_user_prompt":PROMPT,"task_user_prompt":PROMPT},
    chain_id="c",previous_prompt_id="",next_prompt_id="",chain_index=0,
    recovery_attempted=True,request_type="step")
w=PromptWorker.__new__(PromptWorker)
w.durable=Durable();w.live=Live();w.queue=Queue();w.n1_gatekeeper=Gate()
w._task_suppressed=lambda _:False
w._runtime_config=lambda:{"worker":{"max_subtask_depth":4}}
child=w._handoff_failed_task_to_recovery(j,stage="verifier",error="N2 lost path",checkpoint="missing path claimed")
plan=w.durable.children[child]
assert [s.name for s in plan.steps][2:-1]==["Finish report","N2 recovery conclusion"]
assert len(w.queue.saved)==2
assert w.queue.saved[0].task_id==child
assert w.queue.saved[0].metadata["original_user_prompt"]==PROMPT
assert PATH in w.queue.saved[0].prompt
assert "returned_results" not in w.queue.saved[0].context
assert "ledger" not in w.queue.saved[0].context.lower() or "parent ledger stays with the parent" in w.queue.saved[0].context
assert w.n1_gatekeeper.seen["original_request"]==PROMPT
assert w._handoff_failed_task_to_recovery(j,stage="verifier",error="same",checkpoint="same")==child
assert len(w.queue.saved)==2
assert "ORIGINAL USER PROMPT: REFERENCE ONLY" in PromptWorker._tool_slice_initial_messages(w.queue.saved[0])[1]["content"]
print("PASS parent-side reconstruction, independent successor, original prompt, no duplicate")
# The old Redis recorder populates both evidence and additional result layers.
class Pipeline:
    def __init__(self,store):self.store=store;self.ops=[]
    def xadd(self,k,fields,**kwargs):self.ops.append(("xadd",k,fields))
    def rpush(self,k,*rows):self.ops.append(("rpush",k,rows))
    def ltrim(self,k,a,b):self.ops.append(("ltrim",k,(a,b)))
    def execute(self):
        for op,k,val in self.ops:
            if op=="xadd":self.store.events.append((k,val))
            if op=="rpush":self.store.rows.setdefault(k,[]).extend(val)
            if op=="ltrim":
                a,b=val
                self.store.rows[k]=self.store.rows[k][a:b+1 if b != -1 else None]
class FakeRedis:
    def __init__(self):self.rows={};self.events=[]
    def xadd(self,k,fields,**kwargs):
        self.events.append((k,fields))
        return "1-0"
    def pipeline(self,transaction=True):return Pipeline(self)
    def lrange(self,k,a,b):return self.rows.get(k,[])[a:b+1 if b!=-1 else None]
r=FakeRedis();log=RedisTaskLog(r)
log.record_evidence("parent","step-01",[{"tool":"desktop_screenshot","result":{"ok":True,"path":PATH,"width":5760,"height":1080,"file_size":493187}}])
saved=log.result_ledger("parent")
assert saved[0]["result"]["path"]==PATH
assert saved[0]["result"]["file_size"]==493187
assert len(r.events)==1
print("PASS old Redis evidence recorder feeds both evidence stream and extra reconstruction ledger")


# N1 must not drop unresolved work or smuggle completed steps into the successor.
from norm_runtime.n1_gatekeeper import N1Gatekeeper
from types import SimpleNamespace
n1=N1Gatekeeper.__new__(N1Gatekeeper)
n1.enabled=True
n1.profiles={}
n1.client=SimpleNamespace(timeout_seconds=120)
payload={"steps":[{"name":"Finish report","instruction":"Reuse the exact screenshot location",
                  "verify":"Check observed image metadata",
                  "source_step_ids":["step-02"]}]}
n1._review_generate=lambda *args,**kw:json.dumps(payload)
args={"original_request":PROMPT,"failed_step_id":"step-02","failure":"lost path",
      "checkpoint":"path unknown","unresolved_steps":[{"id":"step-02"}],
      "completed_steps":[{"id":"step-01"}],
      "redis_results":log.result_ledger("parent"),
      "pending_dispatches":[]}
assert n1.reconstruct_remaining_recovery_steps(**args)[0]["source_step_ids"]==["step-02"]
payload["steps"][0]["source_step_ids"]=["step-01"]
try:
    n1.reconstruct_remaining_recovery_steps(**args)
except RuntimeError as exc:
    assert "unassigned" in str(exc)
else:
    raise AssertionError("N1 recovery guard allowed completed work to return")
payload["steps"][0]["source_step_ids"]=[]
try:
    n1.reconstruct_remaining_recovery_steps(**args)
except RuntimeError:
    pass
else:
    raise AssertionError("N1 recovery guard permitted no source step coverage")
print("PASS N1 remaining-work coverage gate rejects omissions and completed-step repetition")

# No successor may be created when the original execution list disagrees.
mismatch=Live()
mismatch.plan["steps"].append({"id":"rogue-step","description":"unplanned","verify":""})
wbad=PromptWorker.__new__(PromptWorker)
wbad.durable=Durable()
wbad.live=mismatch
wbad.queue=Queue()
wbad.n1_gatekeeper=Gate()
wbad._runtime_config=w._runtime_config
wbad._task_suppressed=lambda _:False
try:
    wbad._handoff_failed_task_to_recovery(j,stage="verifier",error="test mismatch")
except RuntimeError as exc:
    assert "disagree" in str(exc)
else:
    raise AssertionError("Mismatched Redis execution list permitted recovery")
assert not wbad.durable.children
print("PASS divergent Redis plan blocks successor creation before handoff")
