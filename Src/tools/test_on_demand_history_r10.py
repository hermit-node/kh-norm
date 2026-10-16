"""Prior context lookup: scoped/on-demand, evicted after use, N1 loop guarded."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.task_record_lookup import fetch, receipt, TOOL_SCHEMA
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.n1_gatekeeper import N1Gatekeeper
from test_n1_gatekeeper import FakeClient, FakeLive

BIG="OLDER_RAW_DOCUMENT_"*17000
assert len(BIG)>290000
class PG:
 def task_plan_snapshot(self,task):
  return {"task_id":task,"status":"running","title":"Create PDF",
          "steps":[{"id":"1","name":"Gather","status":"completed","summary":BIG},
                   {"id":"2","name":"Format","status":"running","summary":BIG}]}
 def terminal_step_records(self,task,excluded_step_id=""):
  return [{"id":"1","name":"Gather","summary":BIG,"evidence":[]}]
 def context_slice(self,task,step,index):
  return {"slice_index":index,"reason":"checkpoint","messages":[
   {"role":"user","content":"hello"},
   {"role":"tool","content":BIG}]}
 def latest_context_slice(self,task,step):
  return self.context_slice(task,step,12)
class Live:
 def result_ledger(self,task,limit=40):
  return [{"step_id":"1","tool":"read_file",
           "result":{"ok":True,"path":"C:/result.pdf","sha256":"a"*64,
                     "raw":BIG}}]
current=SimpleNamespace(task_id="t",step_id="2",metadata={})
pg=PG()
w=PromptWorker.__new__(PromptWorker);w.durable=pg
summary=w._prior_step_index(current)
assert len(summary)<9500 and BIG not in summary
assert "1" in summary
print("PASS next step gets only prior step IDs/status/record pointers")
result=fetch(current,pg,Live(),{"scope":"current","kind":"context_slice",
       "step_id":"1","message_index":1,"max_chars":3000})
assert result["ok"] and result["truncated"]
assert len(result["content"])<=3000
assert result["total_chars"]>290000 and result["record_sha256"]
assert BIG not in str(receipt(result))
assert not "content" in receipt(result)
print("PASS 300k stored message, one 3k page delivered; receipt contains no text")
msgs=[{"role":"tool","tool_name":"read_task_record",
        "content":json.dumps(result)},
      {"role":"tool","tool_name":"read_file","content":"other ordinary tool result"}]
assert w._evict_lookup_tool_text(msgs)==1
assert "OLDER_RAW_DOCUMENT_" not in msgs[0]["content"]
assert "record_sha256" in msgs[0]["content"]
assert msgs[1]["content"]=="other ordinary tool result"
print("PASS lookup memory evicted from active conversation after N2 sees it")
index=fetch(current,pg,Live(),{"scope":"current","kind":"context_slice","step_id":"1"})
assert "message_count" in index["content"]
assert BIG not in index["content"]
print("PASS no archived transcript implicitly transferred during index read")
index2=fetch(current,pg,Live(),{"scope":"current","kind":"plan_index"})
assert BIG not in index2["content"]
assert "Gather" in index2["content"]
print("PASS prior plan index has no raw summaries")
recovery=SimpleNamespace(task_id="child",step_id="work",
                         metadata={"recovery_mode":True,"parent_task_id":"t"})
assert fetch(recovery,pg,Live(),{"scope":"recovery_parent","kind":"context_slice",
            "step_id":"1","message_index":1,"max_chars":400})["task_id"]=="t"
try:
 fetch(current,pg,Live(),{"scope":"recovery_parent","kind":"plan_index"})
except PermissionError:pass
else:raise AssertionError("Unauthorized parent read accepted")
print("PASS parent access requires runtime authorized recovery linkage")
gate=N1Gatekeeper(FakeClient(),FakeLive(),loop_repeat_threshold=3)
query={"scope":"current","kind":"context_slice","step_id":"1",
       "slice_index":12,"message_index":1,"start_char":0,"max_chars":3000,
       "n1_need":"Verify missing reference"}
a=gate.before_tool(task_id="t",step_id="2",name="read_task_record",arguments=query)
b=gate.before_tool(task_id="t",step_id="2",name="read_task_record",arguments=query)
c=gate.before_tool(task_id="t",step_id="2",name="read_task_record",arguments=query)
assert a.execute and b.execute
assert not c.execute and c.result["n1_loop_blocked"]
different={**query,"start_char":3000}
assert gate.before_tool(task_id="t",step_id="2",
                        name="read_task_record",arguments=different).execute
print("PASS N1 blocks third identical lookup; new record offset remains permissible")
