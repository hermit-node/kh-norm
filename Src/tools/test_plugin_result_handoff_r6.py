"""Norm r6 plugin result and suppression boundary regressions, no live mutations."""
from pathlib import Path
from unittest.mock import patch
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.file_tool_executor import FileToolExecutor
from norm_runtime.prompt_worker import PromptWorker

class Plugin:
    def has_tool(self,name):
        return name.startswith("plugin_mock__")
    def execute(self,name,args):
        if name.endswith("__fail"):
            return {"result":{"ok":False,"error":"Cannot capture"},"stdout":"","stderr":"","plugin":{"name":"mock"}}
        return {"result":{"path":r"C:\Norm\workspace\images\desktop\desktop-abc.jpg",
                          "width":5760,"height":1080,"bytes":550312,"sha256":"abcd"},
                "plugin":{"name":"mock"},"stdout":"","stderr":"","plugin_fingerprint":"xyz"}
obj=FileToolExecutor.__new__(FileToolExecutor)
obj.plugin_manager=Plugin()
obj._storage_context_for_call=None
obj.authority_config={"required":False}
obj._audit=lambda *a,**k:None
r=obj.execute("plugin_mock__main__desktop_screenshot",{"monitor":1,"quality":75})
assert r["ok"] is True and r["width"]==5760 and r["bytes"]==550312
assert r["path"].endswith("desktop-abc.jpg") and "result" not in r and "stdout" not in r
evidence=[{"tool":"plugin_mock__main__desktop_screenshot",
           "arguments":{"monitor":1,"quality":75},
           "result":r}]
shown=json.loads(PromptWorker._evidence_text(evidence))[0]
assert shown["arguments"]=={"monitor":1,"quality":75},shown
assert all(k in shown["result"] for k in ("path","width","height","bytes","sha256")),shown
assert not shown["result"].get("stdout")
old={"ok":True,"tool":"plugin_mock__main__desktop_screenshot",
     "result":{"path":"C:/archive.jpg","width":3840,"height":1080,"bytes":12345},
     "stdout":"","stderr":"","plugin":{"name":"mock"}}
shown=json.loads(PromptWorker._evidence_text(
 [{"tool":"plugin_mock__main__desktop_screenshot","arguments":{"monitor":1,"quality":75},"result":old}]))[0]
assert shown["result"]["path"]=="C:/archive.jpg" and shown["result"]["bytes"]==12345,shown
fail=obj.execute("plugin_mock__main__fail",{})
assert fail["ok"] is False and fail["error"]=="Cannot capture"
print("PASS plugin structured response prioritized, legacy evidence supported, failure status preserved")

class FakeJob:
    task_id="chat-suppressed"
    step_id="step-01"
class SuppressionProbe:
    _task_suppressed=lambda self,tid:True
    _handle_suppressed=lambda self,*args,**kw:self.calls.append(("suppressed",kw["reason"]))
    _write_sos=lambda self,*args,**kw:self.calls.append(("sos",args))
    def __init__(self):self.calls=[]
probe=SuppressionProbe()
# Verify no SOS or handoff if suppression already active at the pathological handler.
with patch.object(PromptWorker,"_handle_suppressed",lambda self,*a,**k:None):
    pass
try:
    PromptWorker._handle_pathological_failure(
        probe,"redis-id",FakeJob(),"step",None,"verifier","bad",resume_checkpoint="")
except AttributeError as exc:
    raise AssertionError("Suppression should return before accessing queue/durable state") from exc
assert len(probe.calls)==1 and probe.calls[0][0]=="suppressed",probe.calls
print("PASS suppressed failure handled without creating SOS or recovery task")