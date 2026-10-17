from pathlib import Path
import sys,json,io
from types import SimpleNamespace
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/"core"),str(root/"tools"),str(root/"plugins/nabler/src")]
from main import render_report
from norm_runtime.live_log import RedisTaskLog
from norm_runtime.rich_console import NormConsole
import types
# Avoid resolving installed C:\Norm settings while testing an extracted archive.
fake_common=types.ModuleType("norm_gui_common")
fake_common.endpoints=lambda: {}
sys.modules["norm_gui_common"]=fake_common
from norm_gui_stream import _render
from rich.console import Console
class Pipeline:
 def __init__(self):self.data={}
 def rpush(self,key,*v):self.data[key]=v;return self
 def ltrim(self,*a):return self
 def execute(self):return []
class Client:
 def __init__(self):self.pipe=Pipeline()
 def pipeline(self,transaction=True):return self.pipe
class Record:
 _bounded_evidence_value=staticmethod(RedisTaskLog._bounded_evidence_value)
 def __init__(self):self.client=Client();self.events=[]
 def _emit(self,task,event,**kw):self.events.append(kw)
 def _result_ledger_key(self,task):return "norm:"+task+":ledger"
rep=render_report("Checks",[{"name":"README","value":"PASS","status":"PASS"},{"name":"Config","value":"PASS","status":"PASS"}],mode="table",output="markdown")
obj=Record()
RedisTaskLog.record_evidence(obj,"t","s",[{"tool":"plugin_nabler__main__render_report","arguments":{},"result":{"ok":True,**rep}}])
saved=obj.events[0]["evidence"][0]["result"]
assert saved["rows"]==rep["rows"] and saved["rendered"]==rep["rendered"]
assert json.loads(obj.client.pipe.data["norm:t:ledger"][0])["result"]["rendered"]==rep["rendered"]
proposal=json.dumps({"function":{"name":"plugin_nabler__main__render_report","arguments":{"rows":[{"name":"HIDDEN_ARGS"}]}}})
for which in ("stream","integrated"):
 buf=io.StringIO()
 console=Console(file=buf,width=120,force_terminal=False,highlight=False)
 events=[{"channel":"ollama","type":"tool_call","text":proposal},{"channel":"ollama","type":"nabler_report","text":rep["rendered"],"output":"markdown"}]
 if which=="stream":
  for e in events:_render(console,e)
 else:
  view=SimpleNamespace(console=console,_is_muted=lambda _:False)
  for e in events:NormConsole._render(view,e)
 s=buf.getvalue()
 assert "Nabler report" in s and "README" in s and "HIDDEN_ARGS" not in s
cfg=json.loads((root/"config/runtime.json").read_text(encoding="utf-8"))
assert "intermediate steps" in cfg["agents"]["n2"]["persistent_instructions"][2]
for name in ("core/norm_runtime/live_log.py","core/norm_runtime/prompt_worker.py","core/norm_runtime/rich_console.py","tools/norm_gui_stream.py"):
 compile((root/name).read_bytes(),name,"exec")
print("PASS r24 Nabler JSON envelope, Redis evidence, both console renderers, policy and Python source")
