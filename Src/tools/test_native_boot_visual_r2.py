"""0.55.10r2 native workspace, current bootstrap and browser-session guards.
All checks are non-destructive. No service, console or browser is started.
"""
from __future__ import annotations
import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/workspace'))
sys.path.insert(0,str(ROOT/'tools/shared_browser'))
from workspace_settings import patch_workspace_section
fn=ROOT/'tools/workspace/native/start_native_roles.py'
spec=importlib.util.spec_from_file_location('native_roles_r2',fn)
mod=importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)
values=mod.native_font_updates(ROOT/'tools/workspace/native/NormWorkspace.json')
for r in ('runtime','replies','prompt','ui'):
    assert values[f'{r}_font_face'] and values[f'{r}_font_size']
assert values['profiled_launch']=='true'
original='[postgres]\npass = KEEP_ME\n\n[workspace]\nui_font_size = 9\n\n[other]\nvariable = UNCHANGED\n'
patched=patch_workspace_section(original,values)
assert '[postgres]\npass = KEEP_ME' in patched
assert '[other]\nvariable = UNCHANGED' in patched
assert 'runtime_font_face' in patched
assert original!=patched
assert patch_workspace_section(patched,values)==patched
print('PASS native JSON preferences propagate only to workspace font entries, idempotently')
bat=(ROOT/'Run-Norm.bat').read_text('utf8')
assert bat.index('if /I "%~1"=="--service-only"') < bat.index('if exist "%ROOT%tools\\workspace\\native\\NormWorkspace.exe"')
assert 'goto :backend_boot' in bat
assert 'start "" "%ROOT%tools\\workspace\\native\\NormWorkspace.exe"' in bat
assert 'tools\\start_workspace.py' in bat
cpp=(ROOT/'tools/workspace/native/NormWorkspace.cpp').read_text('utf8')
for x in ['CreateProcessW','start_native_roles.py','safe_shutdown_norm.py','NewNormalShutdownSignal',
          'ChooseFontW','CF_SCREENFONTS','Local\\\\NormWorkspace-Native-05510','RequestNormQuit',
          'WS_CLIPCHILDREN']:
    assert x in cpp,x
assert 'PySide6' not in cpp and 'QWidget' not in cpp
assert (ROOT/'tools/workspace/native/NormWorkspace.exe').stat().st_size>200_000
print('PASS native C++ launcher is default; service-only avoids recursion; verified native font/quit hooks and executable present')
from visual_capture import VisualCapture,PREPARE_JS
assert 'document.querySelectorAll' in PREPARE_JS
browser=SimpleNamespace(page=SimpleNamespace(runJavaScript=lambda js,callback:called.append(js)))
capture=VisualCapture(browser,ROOT/'tools')
called=[]
capture.job={'id':'test','url':'https://chatgpt.com/c/test','prepare_attempts':0}
capture.cancelled=False
capture._prepare()
assert called==[PREPARE_JS]
with patch('visual_capture.QTimer.singleShot') as timer:
    capture._prepared(None)
    assert capture.job is not None and capture.job['prepare_attempts']==1
    assert timer.call_count==1
assert capture.status()['state']=='capturing'
capture.job['prepare_attempts']=9
capture._prepared(None)
assert capture.status()['state']=='failed'
assert 'browser remains open' in capture.status()['error']
assert not hasattr(browser,'hide')
app=(ROOT/'tools/shared_browser/app.py').read_text('utf8')
assert 'if self.manual or self.visual.job is not None' in app
assert 'if self.visual.job is not None:\n            event.ignore()' in app
assert 'self.browser.showNormal()' not in (ROOT/'tools/shared_browser/visual_capture.py').read_text('utf8')
print('PASS visual export top-first retries unavailable DOM, failure preserves browser, manual RPC hide blocked')
# Not strictly an integration test of the real ChatGPT DOM: remains pending.
for rel in ['tools/workspace/native/start_native_roles.py','tools/start_workspace.py',
            'tools/shared_browser/visual_capture.py','tools/shared_browser/app.py']:
    ast.parse((ROOT/rel).read_text('utf8'))
print('ALL 0.55.10r2 OFFLINE REGRESSIONS PASSED')
