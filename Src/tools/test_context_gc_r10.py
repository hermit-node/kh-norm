"""Context garbage collection never deletes authoritative Redis/PG data."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.live_log import RedisTaskLog
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.prompt_queue import PromptJob

giant = "OBSOLETE_TASK_DATA_" * 16000
assert len(giant)>250000
class FakeRedis:
    def __init__(self):self.data=[("20-0", {"event":"step_completed","step_id":"step-01",
        "summary":giant,"verification":"Verified output at C:/Norm/exports/result.pdf"})]
    def xrevrange(self,k,count=100):return self.data[:count]
store=FakeRedis()
live=RedisTaskLog.__new__(RedisTaskLog)
live.client=store
live.state=lambda task: {"plan":giant,"title":"Test","status":"running",
             "current_step":"step-02","step_name":"Finish output"}
live._stream_key=lambda task: "test:"+task
brief=live.working_memory_compact("task",max_chars=6500)
assert len(brief)<6500
assert "OBSOLETE_TASK_DATA" not in brief
assert "step-01" in brief
assert len(store.data[0][1]["summary"])==len(giant)
print("PASS task Redis 300K raw history remains, model event view is bounded")

job=SimpleNamespace(context="checkpoint start: important\\n"+giant+
                   "\\nNEXT ACTION: verify existing output",
                    metadata={"original_user_prompt":"Create PDF"},
                    prompt="Finish output")
bounded=PromptWorker._bounded_model_checkpoint(job)
assert len(bounded)<=12000 and "NEXT ACTION" in bounded
assert len(job.context)>250000
print("PASS existing 300K task checkpoint survives intact, model only sees 12K")

raw=[{"role":"user","content":"begin-"+giant},
     {"role":"tool","tool_name":"read_file","content":"C:/Norm/test.pdf::"+giant},
     {"role":"assistant","content":"next verify C:/Norm/test.pdf"}]
model=PromptWorker._bounded_distillation_messages(raw)
assert sum(len(x["content"]) for x in model)<17000
assert len(raw[0]["content"])>250000
print("PASS model distillation receives bounded references, raw messages untouched")

class PG:
    def __init__(self):self.archives=[]
    def record_context_slice(self,task,step,messages,**kw):
        self.archives.append(list(messages))
        return len(self.archives)
    def context_slice(self,task,step,idx):
        return {"messages":self.archives[idx-1]}
class Gate:
    def __init__(self):self.seen=None
    def distill_continuation_checkpoint(self,**kw):
        self.seen=kw
        assert len(kw["archived_messages"][0]["content"])<2500
        return "Goal: Create PDF\\nCompleted: source archived\\nNext action: verify output"
class Chunks:
    def retained_summary(self):return "C:/Norm/test.pdf"
    def snapshot_streams(self):return {}
w=PromptWorker.__new__(PromptWorker)
w.durable=PG();w.n1_gatekeeper=Gate()
w._runtime_config=lambda:{"context_budget":{"checkpoint_archive_messages":8}}
j=SimpleNamespace(task_id="task",step_id="step-02",context=giant,
                  prompt="Finish output",metadata={"task_user_prompt":"Create PDF"})
checkpoint,meta=w._distilled_continuation_checkpoint(
 j, "Finish output",raw,reason="context_headroom_checkpoint",
 headroom={"remaining_fraction":0.08},chunk_manager=Chunks(),
 step_rounds=18,task_rounds=25)
assert len(w.durable.archives[0][0]["content"])>250000
assert len(checkpoint)<12000
assert meta["postgres_context_slice_index"]==1
assert meta["compact_resume_context"] is True
assert "C:/Norm/test.pdf" in checkpoint
print("PASS 300K raw slice PostgreSQL archived, next N2 checkpoint compact and indexed")
