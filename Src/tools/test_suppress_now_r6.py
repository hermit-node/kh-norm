"""r6 emergency suppression regression test, entirely mocked/disposable."""
from __future__ import annotations
import sys,tempfile,json
from pathlib import Path
from threading import Lock
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.prompt_worker import PromptWorker
from norm_runtime.ollama_client import OllamaClient
class Durable:
    def __init__(self,events):
        self.events=events
        self.status="running"
    def root_task_id(self,tid):return tid
    def task_tree(self,tid):return [{"task_id":tid,"status":self.status,"task_kind":"root","task_depth":0}]
    def task_status(self,tid):return self.status
    def is_task_tree_suppressed(self,tid):return self.status=="suppressed"
    def suppress_task_tree(self,root,ids,reason,payload):
        assert payload["tasks"][0]["queue"]["jobs"][0]["job"]["context"]=="saved work"
        self.status="suppressed"
        self.events.append("postgres")
        return {"title":"test","task_id":root}
    def record_maintenance_note(self,*args,**kwargs):self.events.append("note")
class Queue:
    def __init__(self,events):self.events=events
    def snapshot_task_jobs(self,tid):
        return {"jobs":[{"job":{"context":"saved work"}}]}
    def cleanup_task(self,tid):
        self.events.append("queue-cleanup")
        return {}
    def oldest_task_id(self):return None
class Live:
    def __init__(self,events):self.events=events
    def cleanup(self,tid):self.events.append("live-cleanup")
class Job:
    task_id="chat-operator-now"
    step_id="step-03"
    project_id="default"
    attempt=0
def make(tmp,active=True):
    events=[]
    worker=PromptWorker.__new__(PromptWorker)
    worker.durable=Durable(events);worker.queue=Queue(events);worker.live=Live(events)
    worker._state_lock=Lock()
    worker._active_task_id=Job.task_id if active else ""
    worker._suppress_requested=set()
    worker._suppress_now_requested=set()
    worker._soft_delete_task_assets=lambda *a,**k:events.append("soft-delete")
    worker._runtime_root=lambda:Path(tmp)
    worker._maintenance_status=lambda:{}
    return worker,events
with tempfile.TemporaryDirectory(prefix="norm-suppress-now-") as tmp:
    w,events=make(tmp)
    with patch.object(OllamaClient,"cancel_active",side_effect=lambda:events.append("cancel-model") or 1):
        ret=w.request_suppress_task(reason="operator now",immediate=True)
        assert ret["suppressed"] is True and ret["immediate"] is True,ret
        assert events[:3]==["postgres","cancel-model","queue-cleanup"],events
        assert "soft-delete" not in events
        assert w.durable.status=="suppressed"
        assert w._suppression_now_requested(Job.task_id)
        w._handle_suppressed("redis",Job(),"stopped during generation")
    assert events[-2:]==["queue-cleanup","live-cleanup"],events
    assert "soft-delete" not in events
    assert not w._suppression_now_requested(Job.task_id)
    sos=Path(tmp)/"state/suppressed-sos/chat-operator-now/SOS.md"
    assert sos.exists(),sos
    content=sos.read_text("utf8")
    assert content.splitlines()[0] == '# Norm emergency suppression'
    assert "automatic recovery prohibited" in content
    assert "Step ID: step-03" in content
    assert w.durable.status=="suppressed"
    print("PASS now: durable snapshot -> immediate cancel -> queue cleanup; no soft-delete/recovery; SOS preserved")
    w,events=make(tmp)
    with patch.object(OllamaClient,"cancel_active",side_effect=lambda:events.append("cancel-model") or 1):
        normal=w.request_suppress_task(reason="operator normal")
    assert normal["immediate"] is False
    assert events.index("soft-delete")<events.index("cancel-model")
    print("PASS regular suppression behavior unchanged")
from norm_runtime import prompt_worker
from pathlib import Path
tool_root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(tool_root/"tools"))
from norm_plugin_help import get_help
helptext=get_help(tool_root)
assert "/suppress-task now" in helptext
assert "/flush-failed" in helptext
assert "/help plugins" in helptext
print("PASS /help shows complete operator guide")
