"""Optional session-1 GUI bridge startup; no window input enabled in this release."""
from __future__ import annotations
import base64
import configparser
import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]

def _interactive_user() -> str:
    from ctypes import wintypes
    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    if sid == 0xFFFFFFFF:
        raise RuntimeError("no active Windows console session")
    def field(kind: int) -> str:
        ptr = ctypes.c_void_p()
        size = wintypes.DWORD()
        if not wts.WTSQuerySessionInformationW(None, sid, kind,
                                                 ctypes.byref(ptr), ctypes.byref(size)):
            raise OSError("cannot discover interactive user")
        try:
            return ctypes.wstring_at(ptr).strip()
        finally:
            wts.WTSFreeMemory(ptr)
    user = field(5)
    if not user:
        raise RuntimeError("interactive console has no logged-in user")
    domain = field(7)
    return (domain + "\\" if domain else "") + user

def _authenticated_ready(port: int) -> bool:
    tokenfile = ROOT / "state" / "desktop_gui_token"
    if not tokenfile.is_file():
        return False
    try:
        token = tokenfile.read_text("ascii").strip()
        payload = json.dumps({"action": "windows", "args": {}}).encode()
        req = Request(f"http://127.0.0.1:{port}/rpc", payload,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=3) as response:
            result = json.load(response)
        return bool(result.get("ok"))
    except Exception:
        return False

def main() -> int:
    config = configparser.ConfigParser(interpolation=None)
    config.read(ROOT / "config" / "settings.ini", encoding="utf-8-sig")
    if not config.getboolean("desktop_gui", "enabled", fallback=False):
        return 0
    port = config.getint("desktop_gui", "port", fallback=18766)
    if not 1 <= port <= 65535:
        raise ValueError("invalid desktop GUI port")
    if _authenticated_ready(port):
        print("Desktop GUI helper already connected.")
        return 0
    user = _interactive_user()
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    helper = ROOT / "tools" / "desktop_gui_bridge.py"
    if not pythonw.is_file() or not helper.is_file():
        raise FileNotFoundError("GUI helper/runtime Python missing")
    # Only a matching task is reused; never silently replace an operator's task.
    script = """
$ErrorActionPreference = 'Stop'
$name = 'Norm Desktop GUI Bridge'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    $action = $existing.Actions | Select-Object -First 1
    if ($action.Execute -ne '{python}' -or $action.Arguments -ne '{args}') {
        throw 'Existing desktop GUI scheduled task differs; refusing overwrite.'
    }
} else {
    $action = New-ScheduledTaskAction -Execute '{python}' -Argument '{args}' -WorkingDirectory '{root}'
    $principal = New-ScheduledTaskPrincipal -UserId '{user}' -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Description 'Local Norm GUI bridge in the interactive user session' | Out-Null
}
Start-ScheduledTask -TaskName $name
""".format(python=str(pythonw).replace("'", "''"),
           args=('-B "' + str(helper) + '"').replace("'", "''"),
           root=str(ROOT).replace("'", "''"), user=user.replace("'", "''"))
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                          "-EncodedCommand", encoded], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=30)
    if out.returncode:
        raise RuntimeError("GUI scheduled task failed: " + out.stderr[-800:])
    for _ in range(20):
        if _authenticated_ready(port):
            print("Desktop GUI helper ready in the active console session.")
            return 0
        time.sleep(0.5)
    raise RuntimeError("GUI helper did not become authenticated and ready")

if __name__ == "__main__":
    raise SystemExit(main())
