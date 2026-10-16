"""Replay the October 12 verifier/recovery failures without live Redis/PG."""
from pathlib import Path
import json,sys
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.protocol import (
 reconcile_step_verification_verdict,validate_step_verification)
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.prompt_queue import PromptJob
from norm_runtime.n1_gatekeeper import N1Gatekeeper

# The real verifier repeatedly said ACCEPT while claiming incomplete work.
broken={"schema_version":2,"kind":"step_verification","verdict":"accept",
        "requirements_complete":False,"evidence_supported":True,
        "issues":[],"repair_scope":"none","fulfilled_injection_ids":[]}
repaired=validate_step_verification(reconcile_step_verification_verdict(broken))
assert repaired["verdict"]=="revise" and repaired["repair_scope"]=="requirements"
assert repaired["issues"] and not repaired["fulfilled_injection_ids"]
broken_evidence={**broken,"requirements_complete":True,"evidence_supported":False}
negative=validate_step_verification(reconcile_step_verification_verdict(broken_evidence))
assert negative["verdict"]=="revise" and negative["repair_scope"]=="evidence"
proper={**broken,"requirements_complete":True}
assert validate_step_verification(reconcile_step_verification_verdict(proper))["verdict"]=="accept"
print("PASS contradictory accept becomes actionable revise; valid accept stays accept")

class VerifierModel:
 def __init__(self):self.calls=0
 def generate(self,*args,**kw):self.calls+=1;return json.dumps(broken)
 def parse_json(self,value):return json.loads(value)
v=PromptWorker.__new__(PromptWorker)
v.client=VerifierModel()
v._verifier_prompt=lambda *a:"Test original evidence"
accepted,issues,report=v._request_verdict("Produce PDF","I have no PDF",[])
assert not accepted and len(issues)>=1 and v.client.calls==1
assert json.loads(report)["verdict"]=="revise"
print("PASS historical inconsistent output does not consume 3 identical verifier retries")

class RefusingGate:
 def __init__(self):self.calls=0
 def approve_recovery_steps(self,**kwargs):
  self.calls+=1
  return {"decision":"retry","reason":"Sentence styling change",
          "retry_instruction":"Replace a sentence with an equivalent sentence"}
class PlanModel:
 def __init__(self):self.calls=0
 def generate(self,*args,**kwargs):
  self.calls+=1
  return json.dumps({"steps":[{
   "name":"Retry observed work",
   "instruction":"Use actual step-01 evidence and exclude unverified blocker notes. Attempt "+str(self.calls),
   "verify":"Require real source evidence for output",
   "source_step_ids":["step-02"]
  }]})
w=PromptWorker.__new__(PromptWorker)
w.client=PlanModel();w.n1_gatekeeper=RefusingGate()
try:
 w._n2_propose_recovery_steps(original="Print PDF",failed_step_id="step-02",
   error="verifier inconsistent",checkpoint="",unresolved=[
   {"id":"step-02","name":"Print","description":"PDF task","verify":"PDF complete"}],
   completed=[{"id":"step-01"}],artifact_refs=[],pending=[])
except RuntimeError as exc:
 assert "did not approve" in str(exc) and "retain" in str(exc)
else:raise AssertionError("Rejected recovery accidentally approved")
assert w.n1_gatekeeper.calls==3
assert w.client.calls==3
print("PASS N1 remains authority; three rejections cannot create a successor")

class Durable:
 def __init__(self):self.calls=[];self.status="running"
 def checkpoint_step(self,*args):self.calls.append("checkpoint")
 def finalize_task(self,*args):self.calls.append("finalize");self.status="failed"
 def terminal_summary_verified(self,*args):return True
 def record_task_failure(self,*args,**kwargs):self.calls.append("record_failure")
 def task_status(self,task):return self.status
class Queue:
 def __init__(self):self.cleared=0
 def task_rounds(self,*args):return 3
 def cleanup_task(self,*args):self.cleared+=1;return {"work":2}
class Live:
 def __init__(self):self.events=[]
 def step_failed(self,*args):self.events.append("failed")
 def cleanup(self,*args):raise AssertionError("CRITICAL: task Redis evidence was deleted")
j=PromptJob(message_id="node",task_id="task",step_id="step-02",
 prompt="Export PDF",project_id="default",context="",created_at=0,
 attempt=0,metadata={},chain_id="chain",previous_prompt_id="",
 next_prompt_id="",chain_index=0,recovery_attempted=False,request_type="step")
w=PromptWorker.__new__(PromptWorker);w.durable=Durable();w.queue=Queue();w.live=Live()
w._task_suppressed=lambda _:False
sos=[]
w._write_sos=lambda *a:sos.append("written")
w._handoff_failed_task_to_recovery=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError("N1 refuses"))
w._clear_terminal_redis=lambda *a:(_ for _ in ()).throw(AssertionError("CRITICAL: terminal Redis purge"))
w._handle_pathological_failure("delivery",j,"Export PDF",
    datetime.now(timezone.utc),"verifier","invalid verdict")
assert w.durable.status=="failed"
assert "record_failure" in w.durable.calls
assert w.queue.cleared==1
assert w.live.events==["failed"]
assert sos==["written"]
print("PASS failed N1 handoff retains parent SOS, Redis evidence, temp, durable failure")
