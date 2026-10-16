"""Shutdown helpers for this Norm installation, never all Python apps."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import subprocess
from urllib import request


def signal_operator_consoles(root: Path) -> Path:
    from norm_runtime.settings import load_path_settings
    state=load_path_settings(root)["state_root"]
    state.mkdir(parents=True,exist_ok=True)
    path=state/"operator-console-shutdown.json"
    payload={
        "schema":1,"reason":"normal-service-exit",
        "created_at":datetime.now(timezone.utc).isoformat(),
        "other_delay_seconds":1,"prompt_delay_seconds":2,
    }
    path.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf8")
    return path


def stop_browser(root: Path) -> str:
    token=root/"state/shared_browser_token"
    if not token.is_file():
        return "not-launched"
    try:
        key=token.read_text("ascii").strip()
        if len(key)!=64:
            return "token-invalid"
        payload=json.dumps({"action":"quit","args":{}}).encode("utf8")
        req=request.Request("http://127.0.0.1:18767/rpc",payload,
            method="POST",headers={"Authorization":"Bearer "+key,
            "Content-Type":"application/json"})
        with request.urlopen(req,timeout=3) as response:
            data=json.load(response)
        return "requested" if data.get("ok") else "response-error"
    except Exception as exc:
        return f"unavailable:{type(exc).__name__}"


def stop_desktop_gui_task(root: Path) -> str:
    """Only stop scheduled task if it points to THIS Norm install."""
    name="Norm Desktop GUI Bridge"
    script=str(root/"tools"/"desktop_gui_bridge.py")
    executable=str(root/".venv"/"Scripts"/"pythonw.exe")
    query=(
        "$task=Get-ScheduledTask -TaskName 'Norm Desktop GUI Bridge' -ErrorAction SilentlyContinue;"
        "if ($task) {"
        "$action=$task.Actions|Select-Object -First 1;"
        "if ($action.Execute -ieq "+_ps_literal(executable)+" -and "
        "$action.Arguments.Contains("+_ps_literal(script)+")) {"
        "Stop-ScheduledTask -TaskName 'Norm Desktop GUI Bridge' -ErrorAction Stop;"
        "'STOPPED'"
        "} else {'UNRELATED'}"
        "} else {'NOT_FOUND'}"
    )
    try:
        done=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive",
                 "-Command",query],capture_output=True,text=True,timeout=12,
                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if done.returncode!=0:
            return "task-stop-failed"
        return (done.stdout or "").strip()[-150:] or "not-found"
    except (OSError,subprocess.TimeoutExpired):
        return "task-inspection-failed"


def _ps_literal(value:str)->str:
    return "'"+str(value).replace("'","''")+"'"


def stop_owned_auxiliaries(root: str | Path) -> dict:
    root=Path(root).resolve()
    result={}
    try:
        result["console_signal"]=str(signal_operator_consoles(root))
    except Exception as exc:
        logging.exception("Cannot signal Norm operator consoles")
        result["console_error"]=type(exc).__name__
    try:result["desktop_gui"]=stop_desktop_gui_task(root)
    except Exception as exc:result["desktop_gui"]=type(exc).__name__
    try:result["shared_browser"]=stop_browser(root)
    except Exception as exc:result["shared_browser"]=type(exc).__name__
    logging.info("Norm auxiliary shutdown: %s",result)
    return result
