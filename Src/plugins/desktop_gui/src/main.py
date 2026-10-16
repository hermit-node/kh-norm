"""Norm desktop GUI adapter. Windows UI APIs run only in the session-1 helper."""
from __future__ import annotations
import base64,configparser,hashlib,json,os,secrets
from pathlib import Path
from urllib.request import Request,urlopen

def _root():
    return Path(__file__).resolve().parents[3]

def _request(action: str, **arguments) -> dict:
    root=_root()
    config=configparser.ConfigParser(interpolation=None)
    config.read(root/"config"/"settings.ini",encoding="utf-8-sig")
    port=config.getint("desktop_gui","port",fallback=18765)
    if not 1<=port<=65535:raise ValueError("Invalid local desktop GUI port")
    location=config.get("desktop_gui","token_file",
                         fallback=str(root/"state"/"desktop_gui_token"))
    token_file=Path(os.path.expandvars(location)).expanduser()
    if not token_file.is_absolute():token_file=root/token_file
    token=token_file.read_text(encoding="ascii").strip()
    if len(token)!=64:raise PermissionError("GUI bridge token is missing or invalid")
    payload=json.dumps({"action":action,"args":arguments}).encode("utf-8")
    req=Request(f"http://127.0.0.1:{port}/rpc",payload,method="POST",
                headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    with urlopen(req,timeout=25) as response:
        data=json.load(response)
    if not data.get("ok"):raise RuntimeError(str(data.get("error") or "GUI bridge error"))
    return data["result"]

def desktop_windows(limit: int = 50) -> dict:
    """List visible local Windows application windows (no input or focus changes)."""
    result=_request("windows")
    return {"windows":result["windows"][:max(1,min(200,int(limit)))],
            "total_windows":result["count"]}

def desktop_controls(handle: int, limit: int = 50) -> dict:
    """Read selected window UI Automation control names/types; excludes field values."""
    return _request("controls",handle=int(handle),limit=max(1,min(150,int(limit))))

def desktop_screenshot(monitor: int = 1, quality: int = 65) -> dict:
    """Save an MSS desktop JPEG in Norm workspace and return path for vision tools."""
    info=_request("screenshot",monitor=int(monitor),quality=int(quality))
    content=base64.b64decode(info.pop("image_base64"),validate=True)
    if len(content)>20_000_000:raise ValueError("Screenshot exceeds 20MB limit")
    dest=_root()/"workspace"/"images"/"desktop"
    dest.mkdir(parents=True,exist_ok=True)
    path=dest/("desktop-"+secrets.token_hex(8)+".jpg")
    with path.open("xb") as output:output.write(content)
    return {**info,"path":str(path),"bytes":len(content),
            "sha256":hashlib.sha256(content).hexdigest()}

def desktop_click(handle: int, control_title: str = "") -> dict:
    """Click an existing window/control when the desktop helper explicitly permits input."""
    return _request("click",handle=int(handle),control_title=str(control_title))

def desktop_type(handle: int, text: str) -> dict:
    """Type into selected window if interactive input is explicitly enabled."""
    return _request("type",handle=int(handle),text=str(text))

def desktop_hotkey(keys: str) -> dict:
    """Send a Windows hotkey only when the desktop helper enables input."""
    return _request("hotkey",keys=str(keys))