"""Restart only the Norm shared-browser helper, keeping the current conversation URL."""
from __future__ import annotations
import argparse,json,sys,time
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from start_shared_browser import _interactive_console,main as start_browser
sys.path.insert(0,str(ROOT/"core"))
from norm_runtime.plugin_identity import verify_identity

TOKEN=ROOT/"state/shared_browser_token"

def rpc(action,**kwargs):
    if not TOKEN.is_file():return None
    token=TOKEN.read_text("ascii").strip()
    req=Request("http://127.0.0.1:18767/rpc",
       data=json.dumps({"action":action,"args":kwargs}).encode("utf8"),
       headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    try:
        with urlopen(req,timeout=5) as response:
            value=json.load(response)
        if not value.get("ok"):
            raise RuntimeError(str(value.get("error") or "Request failed"))
        return value.get("result") or {}
    except (OSError,TimeoutError):return None

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    args=parser.parse_args()
    for item in ("visual_capture.py","visual_pdf_worker.py","speaker_pdf.py"):
        if not (ROOT/"tools/shared_browser"/item).is_file():
            raise RuntimeError(f"Missing visual export component: {item}")
    verify_identity(ROOT/"plugins/shared_browser")
    old=rpc("status")
    if args.check:
        print("Visual PDF installation verified; helper running:",old is not None)
        print("Current helper must restart to load the new toolbar code.")
        return 0
    if not _interactive_console():
        raise RuntimeError("Run this from the logged-in Windows desktop, not SSH/service session.")
    url=(old or {}).get("url","")
    manual=bool((old or {}).get("manual_control"))
    if old is not None:
        print("Closing only the shared Chromium helper...")
        rpc("quit")
        for _ in range(60):
            if rpc("status") is None:break
            time.sleep(.25)
        else:raise RuntimeError("Old browser did not shut down; refusing second instance")
    print("Starting updated browser helper...")
    start_browser()
    for _ in range(40):
        if rpc("status") is not None:break
        time.sleep(.25)
    else:raise RuntimeError("Updated browser did not start; inspect logs/shared-browser.log")
    if url.startswith(("https://chatgpt.com/","https://chat.openai.com/")):
        rpc("open",url=url)
    rpc("show")
    if manual:rpc("take_control")
    print("Shared browser reopened. Visual PDF and Cancel PDF controls are available.")
    print("Cookies/profile retained; current scroll position and tab history are not retained.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
