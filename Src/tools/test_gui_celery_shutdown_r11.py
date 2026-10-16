"""GUI PowerShell brace rendering and Celery identity/shutdown routing.

No scheduled task is created, no worker terminated, no live pool touched.
"""
from __future__ import annotations
import ast
import base64
import importlib.util
import json
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"core"))
spec=importlib.util.spec_from_file_location("gui_start_r11",root/"tools/start_desktop_gui.py")
gui=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=gui
spec.loader.exec_module(gui)

with TemporaryDirectory(prefix="norm-r11-gui-shutdown-") as td:
    stage=Path(td)
    (stage/"config").mkdir()
    (stage/"config/settings.ini").write_text(
        "[desktop_gui]\nenabled = true\nport = 18766\n",encoding="utf8")
    (stage/".venv/Scripts").mkdir(parents=True)
    (stage/".venv/Scripts/pythonw.exe").write_bytes(b"test only")
    (stage/"tools").mkdir()
    (stage/"tools/desktop_gui_bridge.py").write_text("# mocked",encoding="utf8")
    gui.ROOT=stage
    gui._interactive_user=lambda: "ACME\\Operator{One}"
    count=[0]
    def check(port):
        count[0]+=1
        return count[0]>1
    gui._authenticated_ready=check
    captured=[]
    def subprocess_stub(args,**kw):
        captured.append((args,kw))
        return SimpleNamespace(returncode=0,stdout="",stderr="")
    with patch.object(gui.subprocess,"run",side_effect=subprocess_stub):
        assert gui.main()==0
    assert len(captured)==1
    encoded=captured[0][0][-1]
    source=base64.b64decode(encoded).decode("utf-16le")
    assert "if ($existing) {" in source
    assert "} else {" in source
    assert "Start-ScheduledTask -TaskName $name" in source
    assert "Operator{One}" in source
    assert str(stage) in source
    assert "{python}" not in source and "{args}" not in source
    assert "{root}" not in source and "{user}" not in source
    assert source.count("{")==source.count("}")
    print("PASS desktop GUI PowerShell blocks render without brace formatting error")

from norm_runtime import celery_shutdown
stage=Path(r"C:\Norm")
interp=str(stage/".venv/Scripts/pythonw.exe")
script=str(stage/"tools/celery_tool_worker.py")
records=[
    {"ProcessId":100+i,"ParentProcessId":10,
     "CommandLine":f'"{interp}" -B "{script}" --id {i}'}
    for i in range(1,5)
]
records.extend([
 {"ProcessId":900,"CommandLine":f'"{interp}" -B "C:\\OtherNorm\\tools\\celery_tool_worker.py" --id 1'},
 {"ProcessId":901,"CommandLine":f'"C:\\OtherPython\\pythonw.exe" -B "{script}" --id 3'},
 {"ProcessId":902,"CommandLine":None},
])
def query_stub(*args,**kwargs):
    return SimpleNamespace(returncode=0,stdout=json.dumps(records),stderr="")
with patch.object(celery_shutdown.subprocess,"run",side_effect=query_stub):
    workers=celery_shutdown._owned_workers(stage)
assert [w["number"] for w in workers]==[1,2,3,4],workers
assert [w["pid"] for w in workers]==[101,102,103,104],workers
print("PASS worker ownership filter and correct Celery --id extraction")

# Both graceful routing and PID-scoped fallback are exercised with mocks.
# Never sends a real Celery broadcast, kills a process, or closes a pool.
import socket
from types import ModuleType
fake_module=ModuleType("norm_runtime.celery_tool_executor")
class FakeControl:
    def __init__(self):self.calls=[]
    def broadcast(self,signal,destination,timeout):
        self.calls.append((signal,destination,timeout))
fake_control=FakeControl()
fake_module.app=SimpleNamespace(control=fake_control)
with patch.dict(sys.modules,{"norm_runtime.celery_tool_executor":fake_module}):
    with patch.object(celery_shutdown,"_owned_workers",side_effect=[
        [{"pid":601,"number":1},{"pid":602,"number":2}],[],[],[]]):
        graceful=celery_shutdown.stop_owned_celery_workers(stage,timeout_seconds=1)
    expected=[f"norm-async-{i}@{socket.gethostname()}" for i in (1,2)]
    assert fake_control.calls==[("shutdown",expected,3)],fake_control.calls
    assert graceful["gracefully_stopped"]==2 and graceful["remaining"]==0,graceful
    killed=[]
    alive=[{"pid":777,"number":2}]
    def current(_):return list(alive)
    def taskkill(command,**kw):
        assert command[:3]==["taskkill.exe","/PID","777"],command
        killed.append(777)
        alive.clear()
        return SimpleNamespace(returncode=0,stdout="",stderr="")
    with patch.object(celery_shutdown,"_owned_workers",side_effect=current):
        with patch.object(celery_shutdown.subprocess,"run",side_effect=taskkill):
            fallback=celery_shutdown.stop_owned_celery_workers(stage,timeout_seconds=0)
    assert killed==[777] and fallback["force_stopped"]==1
    assert fallback["remaining"]==0 and not fallback["errors"],fallback
print("PASS graceful Celery IDs and final verified PID-scoped fallback")

# Static contracts for both stop spellings. This inspects exactly the staged
# source; never invokes the live /stop-all HTTP endpoint.
rich=(root/"core/norm_runtime/rich_console.py").read_text("utf8")
main=(root/"core/norm_main.py").read_text("utf8")
assert 'elif parts in (["/stop-all"], ["/stop", "all"]):' in rich
assert 'elif parts in (["/stop-all", "now"], ["/stop-all", "-now"]' in rich
assert 'self._stop_all(immediate=False)' in rich
assert 'self._stop_all(immediate=True)' in rich
assert 'stop_owned_celery_workers(root)' in main
assert 'close_postgres_pool(root)' in main
assert 'stop_owned_auxiliaries(root)' in main
assert 'def request_stop_all(immediate: bool)' in main
assert 'mode = "stop-all-now" if stop_all_now.is_set() else "stop-all-after-step"' in main
print("PASS both stop-all routes converge on shared cleanup path")
