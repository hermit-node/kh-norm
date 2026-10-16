"""Verify selective failed-task flush stays scoped and bulk remains available."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
import norm_runtime.prompt_worker as mod
from norm_runtime.prompt_worker import PromptWorker

class Durable:
    def __init__(self):
        self.tasks=[{"task_id":"chat-alpha-111","title":"first","failure_stage":"work"},
                    {"task_id":"chat-beta-222","title":"second","failure_stage":"final"}]
        self.calls=[]
    def recent_failed_tasks(self,limit=100):
        return list(self.tasks)[:limit]
    def purge_raw_thinking(self,tid):
        self.calls.append(("thinking",tid))
    def purge_task_maintenance_notes(self,ids,**kw):
        self.calls.append(("notes",tuple(ids)));return len(ids)
    def delete_failed_task_rows(self,ids):
        self.calls.append(("delete",tuple(ids)))
        original=len(self.tasks)
        self.tasks=[x for x in self.tasks if x["task_id"] not in ids]
        return original-len(self.tasks)
    def purge_failed_task_history(self,ids):
        self.calls.append(("history",tuple(ids)));return len(ids)
    def invalidate_background_snapshots(self):
        self.calls.append(("global_snapshots",));return 5
    def record_maintenance_note(self,*args,**kwargs):
        self.calls.append(("lesson",));return True
class Queue:
    def __init__(self):self.cleaned=[]
    def cleanup_task(self,tid):
        self.cleaned.append(tid)
        return {"work":0,"retry":0,"escalation":0,"dead":0,"round_key":0,"budget_key":0}
class Live:
    def __init__(self):self.cleaned=[]
    def cleanup(self,tid):self.cleaned.append(tid)
class Client:
    def __init__(self):self.calls=0
    def generate(self,*a,**k):
        self.calls+=1
        return "Keep recoverable checkpoints."

def make():
    q=Queue();dur=Durable();live=Live();client=Client()
    w=PromptWorker(q,client,live,dur)
    w._soft_delete_task_assets=lambda task_id,**kw: None
    w._runtime_root=lambda: Path("Z:/disposable")
    w._runtime_config=lambda: {"task_storage":{}}
    w._worker_tools=lambda: None
    return w,q,dur,live,client

with patch.object(mod,"load_path_settings",return_value={"workspace_root":"Z:/disposable",
                   "temp_root":"Z:/disposable"}),patch.object(mod,"cleanup_task_temp",return_value={}):
    w,q,d,live,c=make()
    ans=w.flush_failed("0")
    assert ans["scope"]=="single" and ans["deleted"]==1 and ans["task_ids"]==["chat-alpha-111"],ans
    assert [x["task_id"] for x in d.tasks]==["chat-beta-222"]
    assert q.cleaned==["chat-alpha-111"] and live.cleaned==["chat-alpha-111"]
    assert not any(x[0] in ("history","global_snapshots","lesson") for x in d.calls),d.calls
    assert c.calls==0
    ans=w.flush_failed("chat-beta")
    assert ans["scope"]=="single" and ans["deleted"]==1
    assert d.tasks==[]
    w,q,d,live,c=make()
    for selector in ("chat-nope","99"):
        try:w.flush_failed(selector)
        except ValueError:pass
        else:raise AssertionError("invalid selector did not fail")
    assert d.calls==[] and q.cleaned==[]
    ans=w.flush_failed("")
    assert ans["scope"]=="all" and ans["deleted"]==2,ans
    assert c.calls==1
    assert any(x[0]=="history" for x in d.calls)
    assert any(x[0]=="global_snapshots" for x in d.calls)
    assert q.cleaned==["chat-alpha-111","chat-beta-222"]
print("PASS selective flush by index and id, no shared effects; bulk preserved")