"""Non-destructive regression: external BAT shutdown signal, no second shutdown."""
from __future__ import annotations
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
os.environ["QT_QPA_PLATFORM"]="offscreen"
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/workspace'))
spec=importlib.util.spec_from_file_location('r1_workspace',ROOT/'tools/workspace/app.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
calls=[]
with tempfile.TemporaryDirectory() as td:
    signal=Path(td)/'operator-console-shutdown.json'
    signal.write_text('{"reason":"normal-service-exit"}',encoding='utf8')
    fake=SimpleNamespace(
        _test_mode=False,_shutdown_started=False,
        _external_shutdown_signal=signal,
        _external_shutdown_baseline=signal.stat().st_mtime_ns,
        _shutdown_complete=False,
        set_status=lambda message:calls.append(message),
        close=lambda:calls.append('closed'))
    check=mod.Workspace._check_external_shutdown
    assert not check(fake) and not calls, 'must ignore stale signal'
    signal.write_text('{"reason":"not-shutdown"}',encoding='utf8')
    fake._external_shutdown_baseline=0
    assert not check(fake) and not calls, 'must ignore unrelated signal'
    signal.write_text('{"reason":"normal-service-exit"}',encoding='utf8')
    assert check(fake) and fake._shutdown_complete and calls[-1]=='closed'
    calls.clear()
    fake._shutdown_started=True
    assert not check(fake) and not calls, 'must not interrupt workspace-owned shutdown'
print('PASS stale ignored, unrelated ignored, external normal exit closes workspace; own shutdown preserved')
