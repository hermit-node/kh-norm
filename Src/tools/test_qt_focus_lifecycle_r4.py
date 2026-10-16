"""Qt Focus r4 integration regressions. No Norm or Windows Terminal processes started."""
from __future__ import annotations
import importlib.util,json,sys,tempfile,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT/'tools/workspace'
sys.path.insert(0,str(WORKSPACE))
spec=importlib.util.spec_from_file_location('qt_focus_r4',WORKSPACE/'app.py')
app=importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(app)
assert '—' in (WORKSPACE/'app.py').read_text('utf8')
assert 'NormWorkspace Qt Focus' in (WORKSPACE/'qt_focus_original_source.py').read_text('utf8')
class Fake:
 def __init__(self,root):
  self.settings=SimpleNamespace(norm_root=lambda:root,root=root)
  self.demo=False;self.attach_only=False;self._closing=False
  self._shutdown_started=False;self._shutdown_complete=False
  self._shutdown_proc=None;self.launcher=None
  self.started=False;self.attached={'runtime': 12};self.capture_enabled=False
  self.calls=[];self.status=[]
  self._external_shutdown_signal=root/'state/operator-console-shutdown.json'
  self._external_shutdown_baseline=0
 def attach_existing(self):self.calls.append('attach_existing')
 def set_status(self,msg):self.status.append(msg)
 def close(self):self.calls.append('close')
 def _external_normal_exit(self):return app.Workspace._external_normal_exit(self)
 def _poll_attach_only(self):self.calls.append('poll_existing')

with tempfile.TemporaryDirectory(prefix='norm_r4_focus_') as t:
 root=Path(t)
 (root/'.venv/Scripts').mkdir(parents=True)
 (root/'tools/workspace').mkdir(parents=True)
 for rel in ('.venv/Scripts/python.exe','tools/workspace/start_roles.py','tools/safe_shutdown_norm.py'):
  (root/rel).write_text('test',encoding='utf8')
 fake=Fake(root)
 # Launch must proceed with a registered existing Runtime and missing Replies/Prompt.
 # The current start_operator_consoles.py owns duplicate detection and missing roles.
 process=SimpleNamespace(poll=lambda:None,returncode=None)
 with patch.object(app.subprocess,'Popen',return_value=process) as launcher:
  app.Workspace.start_norm(fake)
  assert launcher.call_count==1
  arguments=launcher.call_args.args[0]
  assert str(root/'tools/workspace/start_roles.py') in arguments
  assert not any('Run-Norm.bat' in x for x in arguments)
  assert fake.started is True
  assert 'existing sessions will be reused' in fake.status[-1]
  app.Workspace.start_norm(fake)
  assert launcher.call_count==1,'duplicate launcher should not start while previous is active'
 print('PASS partial existing roles are not startup blockers; idempotent Norm role launcher only once')
 # Verified shutdown helper called once, and signal does not close until process returns 0.
 fake=Fake(root)
 proc=SimpleNamespace(poll=lambda:None,returncode=None)
 with patch.object(app.subprocess,'Popen',return_value=proc) as launcher:
  app.Workspace.quit_norm(fake)
  assert launcher.call_count==1
  assert str(root/'tools/safe_shutdown_norm.py') in launcher.call_args.args[0]
  app.Workspace.quit_norm(fake)
  assert launcher.call_count==1,'shutdown must never be initiated twice'
 fake._shutdown_proc=SimpleNamespace(poll=lambda:7)
 app.Workspace.poll(fake)
 assert not fake._shutdown_started and 'close' not in fake.calls
 assert 'exit 7' in fake.status[-1]
 fake._shutdown_started=True
 fake._shutdown_proc=SimpleNamespace(poll=lambda:0)
 app.Workspace.poll(fake)
 assert fake._shutdown_complete and fake.calls[-1]=='close'
 print('PASS shutdown error keeps workspace open; success closes exactly once')
 fake=Fake(root)
 signal=fake._external_shutdown_signal
 signal.parent.mkdir(parents=True)
 signal.write_text(json.dumps({'reason':'normal-service-exit'}),encoding='utf8')
 fake._external_shutdown_baseline=signal.stat().st_mtime_ns
 assert not fake._external_normal_exit()
 fake._external_shutdown_baseline=0
 assert fake._external_normal_exit()
 print('PASS BAT/service shutdown detection ignores stale signals and accepts fresh normal exit')
 bat=(ROOT/'Run-Norm.bat').read_text('utf8')
 assert 'if /I "%~1"=="--service-only" goto :backend_boot' in bat
 assert '.venv\\Scripts\\python.exe' in bat
 assert 'start_workspace.py' in bat
 assert 'NormWorkspace.exe' not in bat.split(':backend_boot',1)[0]
 assert (ROOT/'tools/workspace/start_roles.py').read_text('utf8').count('"--service-only"')==1
 qt=(ROOT/'tools/workspace/app.py').read_text('utf8')
 assert 'QFontDialog.getFont' in qt and 'settings.set_fonts' in qt
 assert 'self.buttons["Quit Norm"]' not in qt  # template driven, no hardcoded legacy IDs
 assert 'Local\\\\NormWorkspace-Qt-Focus-05510' in qt
 print('PASS Qt Focus original source is the normal entrypoint; no BAT recursion; font editor and single-instance guard present')
print('ALL QT FOCUS r4 OFFLINE LIFECYCLE REGRESSIONS PASS')
