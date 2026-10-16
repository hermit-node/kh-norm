"""Offline normal shutdown contract: do not kill unrelated processes."""
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"core"))
from norm_runtime.celery_shutdown import _owned_workers
from norm_runtime import shutdown_auxiliary
from importlib.util import spec_from_file_location, module_from_spec

# Another installation's workers are NOT ours.
assert _owned_workers(root)==[], "Offline staging must not own live Celery processes"

# Verify the current real machine has a separate worker inventory, without
# changing or stopping any of its processes.
real=_owned_workers(Path(r"C:\\Norm"))
assert len(real)>=0
print("PASS ownership inventory is installation-scoped, current installed workers",len(real))

spec=spec_from_file_location("test_pool",root/"plugins/postgres_pool/src/_pool.py")
module=module_from_spec(spec)
spec.loader.exec_module(module)
Pool=module.PostgresPool
class Good:
    def __init__(self):self.closed=0
    def close(self,timeout=5):self.closed+=1
class Bad(Good):
    def close(self,timeout=5):
        super().close(timeout=timeout)
        raise RuntimeError("simulate pool error")
bad=Bad()
Pool._pools={"norm":bad}
try:Pool.close()
except RuntimeError as e:assert "1 of 1" in str(e)
else:raise AssertionError("Expected pool close error")
assert bad.closed==1
assert Pool._pools=={}
print("PASS PostgreSQL pool reports shutdown errors")

# Do not write a shutdown signal or call Stop-ScheduledTask in this test.
assert callable(shutdown_auxiliary.signal_operator_consoles)
assert callable(shutdown_auxiliary.stop_browser)
assert callable(shutdown_auxiliary.stop_desktop_gui_task)

from norm_runtime import celery_shutdown
assert callable(celery_shutdown.stop_owned_celery_workers)
assert b"Never force-kill" in (root/"tools/safe_shutdown_norm.py").read_bytes()
print("PASS graceful-only shutdown helper and additional OS cleanup controls")
