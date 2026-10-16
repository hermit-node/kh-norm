"""Regression tests running in isolated Qt offscreen session, never launch Norm."""
from pathlib import Path
import ctypes, importlib.util, json, os, tempfile, time
os.environ["QT_QPA_PLATFORM"]="offscreen"
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
src=Path(__file__).resolve().with_name("app.py")
spec=importlib.util.spec_from_file_location("qt_focus_under_test",src)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
app=QApplication([])
app.setStyle("Fusion")
with tempfile.TemporaryDirectory(prefix="qt_focus_test_") as d:
    folder=Path(d)
    (folder/"NormWorkspace-Qt.json").write_text(json.dumps({
        "workspace":{"active_role":"replies","x":130,"y":130,"width":980,"height":720}
    }),encoding="utf-8")
    mod.__file__=str(folder/"app.py")
    w=mod.Workspace(mod.Settings(),attach_only=True)
    w.startup=lambda: None
    assert w.active_role=="replies",w.active_role
    print("PERSISTED_SELECTION_READ_PASS",w.active_role)
    runtime=w.cards["runtime"]
    # Qt offscreen exposes non-Win32 pseudo winIds. Create an actual hidden
    # harmless Win32 STATIC hwnd for the callback routing test instead.
    from ctypes import wintypes
    create=mod.user32.CreateWindowExW
    create.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
       ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,
       wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
    create.restype=wintypes.HWND
    native=int(create(0,'STATIC','test-only hwnd',0x80000000,0,0,150,150,None,None,None,None) or 0)
    assert native and mod.user32.IsWindow(native),native
    runtime.console=native
    w.attached["runtime"]=native
    original_hit=mod.user32.WindowFromPoint
    original_next=mod.user32.CallNextHookEx
    try:
        mod.user32.WindowFromPoint=lambda pt:native
        mod.user32.CallNextHookEx=lambda *args:0
        packet=mod.MSLLHOOKSTRUCT()
        packet.pt=mod.POINT(50,50)
        print('DEBUG_HIT',w._role_for_native_hwnd(native), 'NATIVE',native, 'HOOK',w.mouse_hook,'ACTIVE',w.active_role)
        w._on_native_mouse(0,mod.WM_LBUTTONDOWN,ctypes.addressof(packet))
        for _ in range(5):
            app.processEvents()
        assert w.active_role=="runtime",w.active_role
        assert runtime.z==max(c.z for c in w.cards.values())
        print("NATIVE_BODY_CLICK_RAISE_PASS",w.active_role,runtime.z)
        # Even if source was already marked active, but layer order differs,
        # a click must bring the card forward.
        w.cards["prompt"].z=runtime.z+3
        w.zcounter=w.cards["prompt"].z
        w._on_native_mouse(0,mod.WM_LBUTTONDOWN,ctypes.addressof(packet))
        for _ in range(5): app.processEvents()
        assert runtime.z==max(c.z for c in w.cards.values())
        print("ALREADY_ACTIVE_REAR_CLICK_RAISE_PASS",runtime.z)
        previous_focus=mod.user32.SetFocus
        previous_focused=mod.user32.GetFocus
        previous_foreground=mod.user32.GetForegroundWindow
        previous_isactive=w.isActiveWindow
        attempted=[]
        try:
            w.isActiveWindow=lambda: True
            mod.user32.GetForegroundWindow=lambda: 0
            mod.user32.SetFocus=lambda hwnd: attempted.append(int(hwnd)) or 0
            mod.user32.GetFocus=lambda: native
            w.last_focus_hwnd['runtime']=native
            w._restore_active_focus()
            assert attempted==[native],attempted
            print('RESTORE_SAVED_FOCUS_REQUEST_PASS',attempted)
        finally:
            w.isActiveWindow=previous_isactive
            mod.user32.SetFocus=previous_focus
            mod.user32.GetFocus=previous_focused
            mod.user32.GetForegroundWindow=previous_foreground
    finally:
        mod.user32.WindowFromPoint=original_hit
        mod.user32.CallNextHookEx=original_next
    # Don't release a Qt canvas HWND as though it were a foreign console.
    runtime.console=None
    w.attached.clear()
    w.persist()
    saved=json.loads((folder/"NormWorkspace-Qt.json").read_text())
    assert saved["workspace"]["active_role"]=="runtime",saved
    print("PERSISTED_SELECTION_SAVE_PASS",saved["workspace"]["active_role"])
    w.close()
    for _ in range(5):app.processEvents()
    w2=mod.Workspace(mod.Settings(),attach_only=True)
    w2.startup=lambda:None
    assert w2.active_role=="runtime"
    print("PERSISTED_SELECTION_REOPEN_PASS",w2.active_role)
    w2.close()
    for _ in range(5):app.processEvents()
print("ALL_QT_FOCUS_REGRESSION_TESTS_PASS")
