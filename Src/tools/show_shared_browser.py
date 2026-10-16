"""Reveal existing Norm shared Chromium browser from Windows Explorer.

Never launches a second browser if the authenticated shared-browser service
is already running. If absent, start the normal hidden service first.
Visibility changes do NOT claim manual control.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib import request

from start_shared_browser import ROOT,TOKEN,ready,main as start_browser


def show_existing() -> dict:
    """Authenticated loopback request to the already-running Qt instance."""
    if not ready():
        raise RuntimeError("Shared browser RPC unavailable; cannot show the window")
    token=TOKEN.read_text("ascii").strip()
    payload=json.dumps({"action":"show","args":{}}).encode("utf8")
    req=request.Request(
        "http://127.0.0.1:18767/rpc",data=payload,method="POST",
        headers={"Authorization":"Bearer "+token,
                 "Content-Type":"application/json"})
    with request.urlopen(req,timeout=10) as response:
        result=json.load(response)
    if not isinstance(result,dict) or not result.get("ok"):
        raise RuntimeError("Show request was rejected by the existing Chromium helper")
    state=result.get("result") or {}
    if not state.get("visible"):
        raise RuntimeError("Qt browser did not confirm the window became visible")
    return state


def main() -> int:
    if not ready():
        print("Starting Norm's shared Chromium browser...",flush=True)
        start_browser()
    state=show_existing()
    if state.get("manual_control"):
        print("Shared browser shown; human still has control.",flush=True)
    else:
        print("Shared browser shown; Norm still has control.",flush=True)
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Cannot show shared browser: {type(exc).__name__}: {exc}",flush=True)
        raise SystemExit(1)
