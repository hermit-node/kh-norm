"""Offline regressions for r11r6 Workspace input, polling, and geometry. No GUI or Norm starts."""
from __future__ import annotations
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ctypes
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/workspace"))
spec = importlib.util.spec_from_file_location("norm_workspace_r11r6", ROOT / "tools/workspace/app.py")
app = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(app)

class Timer:
    def __init__(self, active=True):
        self.active = active
        self.starts = self.stops = 0
    def isActive(self): return self.active
    def start(self): self.active = True; self.starts += 1
    def stop(self): self.active = False; self.stops += 1

state = SimpleNamespace(_closing=False, _shutdown_started=False, capture_enabled=True,
                        attached={"runtime": 1, "replies": 2, "prompt": 3}, poll_timer=Timer())
app.Workspace._update_poll_timer(state)
assert state.poll_timer.stops == 1 and not state.poll_timer.active
state.attached.pop("prompt")
app.Workspace._update_poll_timer(state)
assert state.poll_timer.starts == 1 and state.poll_timer.active
state.capture_enabled = False
app.Workspace._update_poll_timer(state)
assert not state.poll_timer.active
print("PASS: discovery timer stops at 3/3, resumes for missing role, respects Release")

state = SimpleNamespace(attached={}, settings=SimpleNamespace(data={"capture": app.DEFAULTS["capture"]}))
with patch.object(app, "top_windows", return_value=[11, 22, 33]) as listing:
    with patch.object(app, "wintext", side_effect=lambda h: {11:"Norm Runtime",22:"Norm Replies",33:"Norm Prompt"}[h]):
        found = app.Workspace._windows_by_role(state, ("runtime", "replies", "prompt"))
        assert all(len(x) == 1 for x in found.values()) and listing.call_count == 1
print("PASS: one EnumWindows traversal for missing consoles")

sample = app.MSLLHOOKSTRUCT()
sample.pt.x, sample.pt.y = 14, 28
state = SimpleNamespace(mouse_hook=1, _closing=False, attached={"runtime": 11},
                        _native_click_queued=False, _pending_native_click=None,
                        _dispatch_native_click=lambda: None)
deferred = []
with patch.object(app.QTimer, "singleShot", side_effect=lambda ms, cb: deferred.append(cb)):
    with patch.object(app.user32, "CallNextHookEx", return_value=0):
        with patch.object(app.user32, "WindowFromPoint", side_effect=AssertionError("blocking WindowFromPoint in low-level hook")):
            assert app.Workspace._on_native_mouse(state, 0, app.WM_LBUTTONDOWN, ctypes.addressof(sample)) == 0
            assert state._pending_native_click == (14, 28) and len(deferred) == 1
            assert app.Workspace._on_native_mouse(state, 0, app.WM_LBUTTONDOWN, ctypes.addressof(sample)) == 0
            assert len(deferred) == 1
print("PASS: hook queues one deferred native click without blocking Win32 lookup")

class Canvas:
    def __init__(self):
        self.rect = (0,0,0,0)
        self.calls = 0
    def geometry(self):
        return SimpleNamespace(getRect=lambda: self.rect)
    def setGeometry(self, *rect):
        self.rect = rect
        self.calls += 1
canvas = Canvas()
card = SimpleNamespace(width=lambda: 800, height=lambda: 500, canvas=canvas,
                       console=77, _last_console_layout=None)
with patch.object(app.user32, "IsWindow", return_value=True):
    with patch.object(app.user32, "SetWindowPos", return_value=1) as setpos:
        app.TerminalCard.sync_console(card)
        app.TerminalCard.sync_console(card)
        assert setpos.call_count == 1 and canvas.calls == 1
        flags = setpos.call_args.args[-1]
        assert flags & app.SWP_NOZORDER and flags & app.SWP_NOACTIVATE
        assert not (flags & app.SWP_FRAMECHANGED)
        card.console = 88
        app.TerminalCard.sync_console(card)
        assert setpos.call_count == 2
print("PASS: SetWindowPos deduplicated, proper child flags, no repeated frame change")

qt = (ROOT/"tools/workspace/app.py").read_text(encoding="utf-8")
assert "QFileSystemWatcher" in qt and "_on_shutdown_file_event" in qt
assert "self.shutdown_timer.start()" in qt
assert "self.shutdown_timer.stop()" in qt
print("PASS: shutdown marker uses Qt watcher; explicit shutdown retains bounded timer")
print("ALL WORKSPACE r11r6 OFFLINE REGRESSIONS PASS")

