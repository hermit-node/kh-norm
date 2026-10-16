"""Norm 0.55.10 non-destructive window, startup, shutdown and font tests."""
from __future__ import annotations
import configparser,importlib.util,os,subprocess,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"core"))
sys.path.insert(0,str(ROOT/"tools/workspace"))
import workspace_settings as settings

def load(name,rel):
    spec=importlib.util.spec_from_file_location(name,ROOT/rel)
    mod=importlib.util.module_from_spec(spec)
    sys.modules[name]=mod
    spec.loader.exec_module(mod)
    return mod

# Font validation + all unrelated configuration remain bit-identical.
original="[project]\nname = Norm\n\n[postgres]\ndatabase = postgres\n# KEEP\n"
patched=settings.patch_workspace_section(original,{
 "runtime_font_face":"Cascadia Code","runtime_font_size":"17","runtime_font_weight":"semi-bold"})
assert patched.startswith(original)
assert "[workspace]" in patched
assert settings.parse_fonts({"runtime_font_size":"17"})["runtime"]["size"]==17
for invalid in (5,49):
    try:settings.validate_font("ui","Consolas",invalid,"normal")
    except ValueError:pass
    else:raise AssertionError("Font bounds not enforced")
with tempfile.TemporaryDirectory(prefix="norm-05510-font-test-") as td:
    ini=Path(td)/"settings.ini";ini.write_text(patched,encoding="utf-8")
    settings.persist_ini_workspace(ini,{"prompt_font_face":"JetBrains Mono","prompt_font_size":19})
    cfg=configparser.ConfigParser(interpolation=None)
    cfg.read(ini,encoding="utf8")
    assert cfg["project"]["name"]=="Norm"
    assert cfg["postgres"]["database"]=="postgres"
    assert cfg["workspace"]["prompt_font_face"]=="JetBrains Mono"
    assert ini.read_text().count("[workspace]")==1
print("PASS persistent fonts; unrelated settings preserved")

operator=load("operator_launcher_v05510","tools/start_operator_consoles.py")
# Use actual interpreter/host paths for precondition validation, but never launch.
operator.PYTHON=Path("C:/Norm/.venv/Scripts/python.exe")
operator.HOST=Path("C:/Norm/tools/operator_console_host.py")
from contextlib import nullcontext
all_roles={"runtime":[101],"replies":[102],"prompt":[103]}
with patch.object(operator,"_launch_lock",return_value=nullcontext()),\
     patch.object(operator,"_owned_host_rows",return_value=all_roles),\
     patch.object(operator,"load_path_settings",return_value={"state_root":Path(tempfile.gettempdir())}),\
     patch.object(operator,"_launch",side_effect=AssertionError("duplicate operator launched")):
    with patch.object(sys,"argv",["launcher.py"]):
        assert operator.main()==0
print("PASS three existing operator roles -> zero new windows")

from types import SimpleNamespace
with patch.dict(sys.modules,{"norm_gui_common":SimpleNamespace(
        norm_process_running=lambda:False,
        start_norm_detached=lambda:None)}):
    startup=load("service_launcher_v05510","tools/start_norm_service.py")
with patch.object(startup,"startup_lock",return_value=nullcontext()),\
     patch.object(startup,"norm_process_running",return_value=True),\
     patch.object(startup,"start_norm_detached",side_effect=AssertionError("duplicate service launched")):
    assert startup.main()==2
print("PASS existing service is not restarted")

source=(ROOT/"tools/safe_shutdown_norm.py").read_text(encoding="utf8")
for required in ("stop_owned_celery_workers", "_owned_host_rows", "browser_ready", "running_norm_pids",
                 "PostgreSQL SERVER left running"):
    assert required in source
assert "pg_terminate_backend" not in source
assert "net stop postgresql" not in source.lower()
assert "Stop-Service" not in source
print("PASS shutdown audits owned processes only; database server left untouched")

service=(ROOT/"core/norm_main.py").read_text(encoding="utf8")
assert "close_postgres_pool(root)" in service
assert "redis_client.close()" in service
print("PASS Norm service PostgreSQL and Redis pool shutdown paths present")
print("ALL 0.55.10 NON-DESTRUCTIVE REGRESSIONS PASSED")
