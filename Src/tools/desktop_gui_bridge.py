"""Authenticated local Windows GUI bridge. Must run inside the user's desktop session."""
from __future__ import annotations
import base64,ctypes,hmac,io,json,os,secrets,sys,threading,traceback
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

SELF=Path(__file__).resolve()
ROOT=SELF.parents[1] if SELF.parent.name=="tools" else SELF.parent
DEPS=SELF.parent/"deps"
if DEPS.exists():
    sys.path[:0]=[str(DEPS),str(DEPS/"win32"),str(DEPS/"win32"/"lib"),str(DEPS/"Pythonwin")]
    if (DEPS/"pywin32_system32").exists():os.add_dll_directory(str(DEPS/"pywin32_system32"))
from PIL import Image
from pywinauto import Desktop,keyboard
import mss

import configparser
INI = configparser.ConfigParser(interpolation=None)
INI.read(ROOT / "config" / "settings.ini", encoding="utf-8-sig")
PORT = INI.getint("desktop_gui", "port", fallback=18766)
if not 1 <= PORT <= 65535:
    raise ValueError("invalid desktop GUI loopback port")
STATE=ROOT/"state"
STATE.mkdir(parents=True,exist_ok=True)
TOKEN_FILE=STATE/"desktop_gui_token"
if not TOKEN_FILE.exists():
    try:
        with TOKEN_FILE.open("x",encoding="ascii") as f:f.write(secrets.token_hex(32))
    except FileExistsError:pass
TOKEN=TOKEN_FILE.read_text("ascii").strip()
if len(TOKEN)!=64:raise RuntimeError("Malformed desktop GUI authorization token")
INPUT_ENABLED=False
INPUT_LOCK=threading.RLock()

def dimensions():
    with mss.mss() as scope:
        return [dict(x) for x in scope.monitors]

def window_data(w):
    rect=w.rectangle()
    return {"handle":int(w.handle),"title":str(w.window_text())[:250],
            "class_name":str(w.class_name())[:120],
            "pid":int(w.process_id()),"rect":{"left":int(rect.left),
            "top":int(rect.top),"right":int(rect.right),"bottom":int(rect.bottom)}}

def rpc(cmd,args):
    if cmd=="windows":
        items=Desktop(backend="win32").windows(visible_only=True)
        return {"windows":[window_data(x) for x in items[:200]],"count":len(items)}
    if cmd=="screenshot":
        monitor=int(args.get("monitor",1))
        quality=max(40,min(90,int(args.get("quality",65))))
        with mss.mss() as scope:
            if monitor<0 or monitor>=len(scope.monitors):raise ValueError("invalid monitor")
            sample=scope.grab(scope.monitors[monitor])
            img=Image.frombytes("RGB",sample.size,sample.rgb)
            out=io.BytesIO()
            img.save(out,format="JPEG",quality=quality,optimize=True)
            payload=out.getvalue()
        return {"width":img.width,"height":img.height,"format":"jpeg",
                "image_base64":base64.b64encode(payload).decode("ascii")}
    if cmd=="controls":
        hwnd=int(args["handle"])
        limit=max(1,min(150,int(args.get("limit",50))))
        w=Desktop(backend="uia").window(handle=hwnd)
        items=[]
        for item in w.descendants()[:limit]:
            try:
                info=item.element_info
                items.append({"name":str(info.name or "")[:180],"type":str(info.control_type),
                              "automation_id":str(info.automation_id or "")[:160]})
            except Exception:continue
        return {"handle":hwnd,"controls":items}
    if cmd in ("click","type","hotkey"):
        if not INPUT_ENABLED:raise PermissionError("GUI input disabled in this preview")
        with INPUT_LOCK:
            if cmd=="click":
                hwnd=int(args["handle"])
                w=Desktop(backend="uia").window(handle=hwnd)
                selector=str(args.get("control_title") or "").strip()
                control=w.child_window(title=selector) if selector else w
                control.click_input()
            elif cmd=="type":
                hwnd=int(args["handle"])
                w=Desktop(backend="win32").window(handle=hwnd)
                w.set_focus()
                keyboard.send_keys(str(args["text"])[:5000],with_spaces=True)
            elif cmd=="hotkey":
                keyboard.send_keys(str(args["keys"])[:120])
        return {"performed":cmd}
    raise ValueError("unknown GUI action")

class Handler(BaseHTTPRequestHandler):
    def send(self,status,obj):
        raw=json.dumps(obj,ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path!="/health":return self.send(404,{"ok":False})
        sid=ctypes.c_ulong()
        ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(sid))
        self.send(200,{"ok":True,"session":sid.value,"screens":len(dimensions())-1,
                       "input_enabled":INPUT_ENABLED})
    def do_POST(self):
        if self.path!="/rpc":return self.send(404,{"ok":False})
        bearer=self.headers.get("Authorization","")
        if not hmac.compare_digest(bearer,"Bearer "+TOKEN):return self.send(403,{"ok":False,"error":"forbidden"})
        length=int(self.headers.get("Content-Length") or 0)
        if length<1 or length>16384:return self.send(413,{"ok":False,"error":"request too large"})
        try:
            request=json.loads(self.rfile.read(length))
            result=rpc(str(request.get("action") or ""),request.get("args") or {})
            self.send(200,{"ok":True,"result":result})
        except Exception as exc:
            self.send(400,{"ok":False,"error":type(exc).__name__+": "+str(exc)[:300]})

if __name__=="__main__":
    ThreadingHTTPServer(("127.0.0.1",PORT),Handler).serve_forever()