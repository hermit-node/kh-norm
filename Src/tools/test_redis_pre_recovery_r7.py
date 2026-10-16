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
    def approve_recovery_steps(self,**kw):
        self.seen=kw
        assert kw["unresolved_steps"][0]["id"]=="step-02"
        assert "step-01" in kw["completed_step_ids"]
        assert kw["artifact_references"][0]["path"]==PATH
        return {"decision":"accept","reason":"N2's proposed remainder is justified","retry_instruction":""}
class N2:
    def __init__(self):self.generated=[]
    def generate(self,prompt,**kw):
        self.generated.append((prompt,kw))
        return json.dumps({"steps":[{"name":"Finish report","instruction":f"Use already captured screenshot at {PATH}; do not recapture.","verify":"Report uses observed file metadata","source_step_ids":["step-02"]}]})
j=PromptJob(message_id="parent-node",task_id="parent",step_id="step-02",
    prompt="Finish report",project_id="default",context="N2 claimed image location unknown",
    created_at=0,attempt=0,metadata={"original_user_prompt":PROMPT,"task_user_prompt":PROMPT},
    chain_id="c",previous_prompt_id="",next_prompt_id="",chain_index=0,
    recovery_attempted=True,request_type="step")
w=PromptWorker.__new__(PromptWorker)
w.durable=Durable();w.live=Live();w.queue=Queue();w.n1_gatekeeper=Gate();w.client=N2()
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
assert len(w.queue.saved[0].context)<12000
assert "Redis result_ledger:parent" in w.queue.saved[0].context
assert "returned_results" not in w.queue.saved[0].context
assert "approved_remaining_steps" not in w.queue.saved[0].context
assert "N2 claimed image location unknown" not in w.queue.saved[0].context
assert w.n1_gatekeeper.seen["original_request"]==PROMPT
assert w.client.generated and "You are N2 reconstructing ONLY" in w.client.generated[0][0]
assert w.client.generated[0][1]["num_predict"]>6500
assert w.n1_gatekeeper.seen["candidate_steps"][0]["source_step_ids"]==["step-02"]
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


# N1 approves N2's proposal; deterministic source coverage stays in worker.
from norm_runtime.n1_gatekeeper import N1Gatekeeper
from types import SimpleNamespace
n1=N1Gatekeeper.__new__(N1Gatekeeper)
n1.enabled=True;n1.profiles={};n1.client=SimpleNamespace(timeout_seconds=120)
out={"decision":"accept","reason":"Valid coverage","retry_instruction":""}
n1._review_generate=lambda *a,**kw:json.dumps(out)
args={"original_request":PROMPT,"failed_step_id":"step-02","failure":"lost path",
      "completed_step_ids":["step-01"],
      "unresolved_steps":[{"id":"step-02"}],
      "candidate_steps":[{"name":"Finish","instruction":"Use screenshot",
                          "verify":"Verify report","source_step_ids":["step-02"]}],
      "artifact_references":[{"path":PATH}],"pending_dispatches":[]}
assert n1.approve_recovery_steps(**args)["decision"]=="accept"
out["decision"]="retry";out["retry_instruction"]="Include missing requirement"
assert n1.approve_recovery_steps(**args)["decision"]=="retry"
out["retry_instruction"]=""
try:n1.approve_recovery_steps(**args)
except RuntimeError as exc:assert "corrective" in str(exc)
else:raise AssertionError("N1 returned retry with no correction")
print("PASS N1 reviews proposals without generating steps")

# No successor may be created when the original execution list disagrees.
mismatch=Live()
mismatch.plan["steps"].append({"id":"rogue-step","description":"unplanned","verify":""})
wbad=PromptWorker.__new__(PromptWorker)
wbad.durable=Durable()
wbad.live=mismatch
wbad.queue=Queue()
wbad.n1_gatekeeper=Gate();wbad.client=N2()
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
