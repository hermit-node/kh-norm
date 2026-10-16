"""NormWorkspace Qt: freeform PySide6 shell around *real* Windows console HWNDs.

No pseudo-terminal widgets; actual operator processes remain unmodified.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QWidget, QPushButton, QLabel, QDialog, QVBoxLayout,
    QFormLayout, QHBoxLayout, QFontComboBox, QSpinBox, QComboBox, QDialogButtonBox, QMessageBox)

from workspace_settings import Settings, ROLES, VALID_WEIGHTS

if os.name != "nt":
    raise SystemExit("NormWorkspace Qt requires Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
GWLP_STYLE, GWLP_EXSTYLE = -16, -20
WS_CHILD, WS_VISIBLE, WS_CLIPSIBLINGS = 0x40000000, 0x10000000, 0x04000000
WS_POPUP, WS_CAPTION, WS_THICKFRAME = 0x80000000, 0x00C00000, 0x00040000
WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00080000, 0x00020000, 0x00010000
WS_EX_APPWINDOW, WS_EX_WINDOWEDGE, WS_EX_CLIENTEDGE = 0x00040000, 0x00000100, 0x00000200
SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW, SWP_NOMOVE, SWP_NOSIZE = 0x0010, 0x0020, 0x0040, 0x0002, 0x0001
SW_HIDE, SW_SHOW = 0, 5
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SMTO_ABORTIFHUNG, SMTO_BLOCK = 0x0002, 0x0001
WM_COMMAND = 0x0111
OLD_RELEASE_ID = 102
LEFT_RAIL = 112
OUTER_BORDER = 9
GRIP = 23
HEADER = 36
RT = wintypes.LPARAM

# A low-level mouse observer reports clicks delivered directly to the foreign
# Windows Terminal HWND. It does not intercept or consume the mouse event.
WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT), ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetFocus.argtypes = []
user32.GetFocus.restype = wintypes.HWND
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.EnumWindows.argtypes = [ctypes.c_void_p, RT]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.SetParent.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_size_t
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

def wintext(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value

def winclass(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(200)
    user32.GetClassNameW(hwnd, buf, 200)
    return buf.value

def top_windows() -> list[int]:
    found: list[int] = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, RT)
    def callback(hwnd: int, _arg: int) -> bool:
        if user32.IsWindowVisible(hwnd) and not user32.GetParent(hwnd):
            found.append(int(hwnd))
        return True
    user32.EnumWindows(callback, 0)
    return found

def is_live_process(pid: int) -> bool:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
        name = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(name))
        if not kernel32.QueryFullProcessImageNameW(h, 0, name, ctypes.byref(length)):
            return False
        return Path(name.value).name.casefold() in ("python.exe", "pythonw.exe")
    finally:
        kernel32.CloseHandle(h)

class TerminalCard(QWidget):
    changed = Signal()
    selected = Signal(str)

    def __init__(self, role: str, label: str, pos: dict, parent: QWidget) -> None:
        super().__init__(parent)
        self.role = role
        self.label = label
        self.z = int(pos.get("z", 1))
        self.console: int | None = None
        self.original_style = 0
        self.original_exstyle = 0
        self.original_parent = 0
        self.original_rect = (100, 100, 900, 650)
        self.drag_kind: str | None = None
        self.mouse_start = QPoint()
        self.geometry_start = None
        self.active = False
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setMinimumSize(260, 160)
        self.setGeometry(int(pos["x"]), int(pos["y"]), max(260, int(pos["width"])), max(160, int(pos["height"])))
        self.canvas = QWidget(self)
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.canvas.setStyleSheet("background: #091422;")
        self.canvas.installEventFilter(self)
        self.canvas.show()
        self.show()
        self.sync_console()

    def canvas_hwnd(self) -> int:
        return int(self.canvas.winId())

    def eventFilter(self, watched, event):
        # Native child console clicks don't normally become Qt mouse events.
        # Qt header still handles moving, WinEvent focus listener handles
        # click-to-front for terminal contents.
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        self.sync_console()
        super().resizeEvent(event)

    def sync_console(self) -> None:
        inner_w = max(30, self.width() - 4)
        inner_h = max(30, self.height() - HEADER - GRIP - 2)
        self.canvas.setGeometry(2, HEADER, inner_w, inner_h)
        if self.console and user32.IsWindow(self.console):
            user32.SetWindowPos(self.console, 0, 0, 0, inner_w, inner_h,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)

    def attach(self, hwnd: int) -> bool:
        if self.console:
            return False
        if not user32.IsWindow(hwnd) or user32.GetParent(hwnd):
            return False
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        self.original_rect = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        self.original_style = user32.GetWindowLongPtrW(hwnd, GWLP_STYLE)
        self.original_exstyle = user32.GetWindowLongPtrW(hwnd, GWLP_EXSTYLE)
        self.original_parent = int(user32.GetParent(hwnd) or 0)
        style = (self.original_style | WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS) & ~(
            WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
        user32.SetWindowLongPtrW(hwnd, GWLP_STYLE, style)
        user32.SetWindowLongPtrW(hwnd, GWLP_EXSTYLE, self.original_exstyle & ~(
            WS_EX_APPWINDOW | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE))
        user32.SetParent(hwnd, self.canvas_hwnd())
        if user32.GetParent(hwnd) != self.canvas_hwnd():
            user32.SetWindowLongPtrW(hwnd, GWLP_STYLE, self.original_style)
            user32.SetWindowLongPtrW(hwnd, GWLP_EXSTYLE, self.original_exstyle)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)
            return False
        self.console = hwnd
        self.sync_console()
        self.update()
        return True

    def release(self) -> None:
        hwnd = self.console
        self.console = None
        if hwnd and user32.IsWindow(hwnd):
            user32.ShowWindow(hwnd, SW_HIDE)
            user32.SetParent(hwnd, self.original_parent)
            user32.SetWindowLongPtrW(hwnd, GWLP_STYLE, self.original_style)
            user32.SetWindowLongPtrW(hwnd, GWLP_EXSTYLE, self.original_exstyle)
            x, y, w, h = self.original_rect
            user32.SetWindowPos(hwnd, 0, x, y, max(320, w), max(220, h),
                                SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        self.update()

    def geometry_json(self) -> dict:
        return dict(x=self.x(), y=self.y(), width=self.width(), height=self.height(), z=self.z)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1d2d42"))
        painter.fillRect(1, 1, self.width()-2, HEADER-1,
                         QColor("#175b72") if self.active else QColor("#31465f"))
        painter.setPen(QPen(QColor("#49d5d2") if self.active else QColor("#7187a3"), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        painter.setPen(QColor("#eef5fb"))
        painter.setFont(QFont(self.parent().settings.fonts["ui"]["face"], self.parent().settings.fonts["ui"]["size"], QFont.Weight.DemiBold))
        painter.drawText(13, 23, self.label)
        painter.setPen(QColor("#88c2d0"))
        painter.drawText(self.width()-65, 23, f"z:{self.z}")
        painter.fillRect(2, self.height()-GRIP, self.width()-4, GRIP-2, QColor("#26394f"))
        painter.setPen(QPen(QColor("#54ddd5") if self.active else QColor("#8caac4"), 2))
        for i in range(3):
            x, y = self.width()-7, self.height()-7
            painter.drawLine(x-5-i*6, y, x, y-5-i*6)
        if not self.console:
            painter.setPen(QColor("#a7b7ca"))
            painter.setFont(QFont(self.parent().settings.fonts["ui"]["face"], self.parent().settings.fonts["ui"]["size"]))
            painter.drawText(14, HEADER+32, "Waiting for existing terminal window…")
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.selected.emit(self.role)
        p = event.position().toPoint()
        if p.x() > self.width()-GRIP and p.y() > self.height()-GRIP:
            self.drag_kind = "resize"
        elif p.y() < HEADER:
            self.drag_kind = "move"
        else:
            return
        self.mouse_start = event.globalPosition().toPoint()
        self.geometry_start = self.geometry()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        p = event.position().toPoint()
        if self.drag_kind:
            delta = event.globalPosition().toPoint() - self.mouse_start
            if self.drag_kind == "move":
                self.move(self.geometry_start.topLeft() + delta)
            else:
                self.resize(max(260, self.geometry_start.width() + delta.x()),
                            max(160, self.geometry_start.height() + delta.y()))
            self.changed.emit()
            event.accept()
        else:
            resize = p.x() > self.width()-GRIP and p.y() > self.height()-GRIP
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if resize else (
                Qt.CursorShape.SizeAllCursor if p.y() < HEADER else Qt.CursorShape.ArrowCursor))

    def mouseReleaseEvent(self, event) -> None:
        if self.drag_kind:
            self.drag_kind = None
            self.changed.emit()
        super().mouseReleaseEvent(event)


class ResizeEdge(QWidget):
    """Clickable border grips for captionless restored windows."""
    def __init__(self, owner: "Workspace", edges: Qt.Edge):
        super().__init__(owner)
        self.owner = owner
        self.edges = edges
        self.manual = False
        self.start_global = QPoint()
        self.start_rect = None
        if edges in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                     Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif edges in (Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                       Qt.Edge.BottomEdge | Qt.Edge.LeftEdge):
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            cursor = Qt.CursorShape.SizeVerCursor
        else:
            cursor = Qt.CursorShape.SizeHorCursor
        self.setCursor(cursor)
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")
        self.show()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.owner.isMaximized():
            return super().mousePressEvent(event)
        self.start_global = event.globalPosition().toPoint()
        self.start_rect = self.owner.geometry()
        self.manual = True
        native = self.owner.windowHandle()
        if native and native.startSystemResize(self.edges):
            self.manual = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self.manual and self.start_rect and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.start_global
            r = self.start_rect
            x,y,w,h = r.x(),r.y(),r.width(),r.height()
            if self.edges & Qt.Edge.LeftEdge:
                x += delta.x()
                w -= delta.x()
            if self.edges & Qt.Edge.RightEdge:
                w += delta.x()
            if self.edges & Qt.Edge.TopEdge:
                y += delta.y()
                h -= delta.y()
            if self.edges & Qt.Edge.BottomEdge:
                h += delta.y()
            if w < self.owner.minimumWidth():
                if self.edges & Qt.Edge.LeftEdge:
                    x -= self.owner.minimumWidth()-w
                w = self.owner.minimumWidth()
            if h < self.owner.minimumHeight():
                if self.edges & Qt.Edge.TopEdge:
                    y -= self.owner.minimumHeight()-h
                h = self.owner.minimumHeight()
            self.owner.setGeometry(x,y,w,h)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.manual = False
        self.start_rect = None
        super().mouseReleaseEvent(event)

class Workspace(QWidget):
    def __init__(self, settings: Settings, *, demo: bool=False, attach_only: bool=False, test_mode: bool=False):
        super().__init__()
        self.settings = settings
        self.demo = demo
        self.attach_only = attach_only
        self.launcher: subprocess.Popen | None = None
        self.demo_procs: list[subprocess.Popen] = []
        self.started = False
        self.capture_enabled = True
        self.status_text = ""
        self.attached: dict[str, int] = {}
        self._closing = False
        self._shutdown_complete = False
        self._shutdown_started = False
        self._shutdown_proc = None
        self._shutdown_log = None
        self._test_mode = test_mode
        self._setup_finished = False
        self._outer_dragging = False
        self._outer_resize = False
        self.zcounter = 0
        self.active_role: str | None = None
        self.last_focus_hwnd: dict[str, int] = {}
        self.focus_hook = None
        self._focus_callback = None
        self.mouse_hook = None
        self._mouse_callback = None

        self.setWindowTitle("Norm Workspace 0.55.10 — Native Terminal Container")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(780, 540)
        self.setMouseTracking(True)
        icon = self.settings.root / "NormWorkspace-Triad.ico"
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))

        ws = self.settings.data["workspace"]
        self.setGeometry(int(ws["x"]), int(ws["y"]), max(780,int(ws["width"])), max(540,int(ws["height"])))
        self.cards = {}
        for role in ROLES:
            p = ws["panes"][role]
            card = TerminalCard(role, str(self.settings.data["capture"]["windows"][role]), p, self)
            card.changed.connect(self.schedule_save)
            card.selected.connect(self.raise_card)
            self.cards[role] = card
            self.zcounter = max(self.zcounter, card.z)
        remembered = str(ws.get("active_role", "")).casefold()
        if remembered not in self.cards:
            remembered = max(self.cards, key=lambda role: self.cards[role].z)
        self.active_role = remembered
        self.sidebar = QWidget(self)
        self.sidebar.setStyleSheet("background:#172639;border-right:1px solid #365b6f;")
        self.buttons = {}
        self.title_label = QLabel("N / Q", self.sidebar)
        self.title_label.setStyleSheet("color:#5de6de;font-weight:bold;background:transparent;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        commands = [
            ("Attach", self.attach_existing), ("Start Norm", self.start_norm),
            ("Release", self.release_all), ("Minimize", self.showMinimized),
            ("Maximize", self.toggle_max), ("Fonts", self.edit_fonts),
            ("Quit Norm", self.request_quit),
        ]
        for key, fn in commands:
            btn = QPushButton(key, self.sidebar)
            btn.setStyleSheet(
                "QPushButton{background:#30445b;color:#f2f6fc;border:1px solid #46637d;"
                "border-radius:6px;padding:7px;}"
                "QPushButton:hover{background:#246a81}"
                "QPushButton:pressed{background:#1d5068}")
            btn.clicked.connect(fn)
            self.buttons[key] = btn
        self.status = QLabel("", self.sidebar)
        self.status.setStyleSheet("color:#72ddd2;background:transparent;")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.apply_ui_font()
        self.layout_sidebar()
        self.resize_handles = [
            ResizeEdge(self, edges) for edges in (
                Qt.Edge.TopEdge, Qt.Edge.BottomEdge,
                Qt.Edge.LeftEdge, Qt.Edge.RightEdge,
                Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
                Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
            )
        ]
        self._layout_resize_handles()

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(850)
        self.save_timer.timeout.connect(self.persist)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(max(250, int(settings.data["capture"]["poll_interval_ms"])))
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        self.arrange_z()
        if ws.get("maximized", False):
            self.showMaximized()
        else:
            self.show()
        self._setup_finished = True
        if not self._test_mode:
            self._install_focus_listener()
            self._install_native_mouse_listener()
        QTimer.singleShot(300, self.startup)

    def layout_sidebar(self) -> None:
        pad = 0 if self.isMaximized() else OUTER_BORDER
        self.sidebar.setGeometry(pad, pad, LEFT_RAIL, max(50, self.height()-pad*2))
        self.sidebar.raise_()
        self.title_label.setGeometry(10, 14, LEFT_RAIL-20, 52)
        for i, (_, button) in enumerate(self.buttons.items()):
            button.setGeometry(9, 79 + 51*i, LEFT_RAIL-18, 43)
        self.status.setGeometry(10, 399, LEFT_RAIL-19,
                                max(80,self.sidebar.height()-420))
        self._layout_resize_handles()

    def _layout_resize_handles(self) -> None:
        if not hasattr(self, "resize_handles"):
            return
        if self.isMaximized():
            for handle in self.resize_handles:
                handle.hide()
            return
        thickness = OUTER_BORDER
        corner = 20
        w,h = self.width(),self.height()
        regions = [
            (corner,0,max(1,w-2*corner),thickness),
            (corner,h-thickness,max(1,w-2*corner),thickness),
            (0,corner,thickness,max(1,h-2*corner)),
            (w-thickness,corner,thickness,max(1,h-2*corner)),
            (0,0,corner,corner),
            (w-corner,0,corner,corner),
            (0,h-corner,corner,corner),
            (w-corner,h-corner,corner,corner),
        ]
        for handle, rect in zip(self.resize_handles,regions):
            handle.setGeometry(*rect)
            handle.show()
            handle.raise_()

    def arrange_z(self) -> None:
        for card in sorted(self.cards.values(), key=lambda c: c.z):
            card.raise_()
        self.sidebar.raise_()
        self._layout_resize_handles()
        for card in self.cards.values():
            card.active = card.role == self.active_role
            card.update()

    def raise_card(self, role: str) -> None:
        if self._closing or role not in self.cards:
            return
        card = self.cards[role]
        if self.active_role == role and card.z == self.zcounter:
            return  # don't raise twice when focus and mouse events report one click
        self.active_role = role
        self.zcounter += 1
        card.z = self.zcounter
        if self.zcounter > 100:
            lowest = min(c.z for c in self.cards.values())
            if lowest > 1:
                shift = lowest - 1
                for c in self.cards.values(): c.z -= shift
                self.zcounter -= shift
            else:
                # Defensive compaction when one pane was never clicked.
                for rank, c in enumerate(sorted(self.cards.values(), key=lambda c:c.z),1): c.z=rank
                self.zcounter=3
        self.arrange_z()
        self.schedule_save()

    def set_status(self, message: str) -> None:
        self.status_text = message
        self.status.setText(message)
        if sys.stdout is not None:
            print("[NormWorkspaceQt]", message, flush=True)
        try:
            with (self.settings.root / "NormWorkspace-Qt.log").open("a", encoding="utf-8") as log:
                log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except OSError:
            pass

    def _role_for_native_hwnd(self, hwnd: int) -> str | None:
        """Return the card containing an actual native terminal click HWND."""
        if not hwnd:
            return None
        for role, card in self.cards.items():
            h = card.console
            if not h or not user32.IsWindow(h):
                continue
            # Some terminal hosts return the Qt canvas HWND under a click.
            canvas = card.canvas_hwnd()
            if (hwnd == h or user32.IsChild(h, hwnd)
                    or hwnd == canvas or user32.IsChild(canvas, hwnd)):
                return role
        return None

    def _install_native_mouse_listener(self) -> None:
        """Observe, never swallow, native mouse downs on captured terminal HWNDs."""
        self._mouse_callback = HOOKPROC(self._on_native_mouse)
        self.mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_callback, None, 0)
        if not self.mouse_hook:
            self.set_status("Native click listener unavailable; header still raises panels.")

    def _on_native_mouse(self, n_code: int, message: int, data: int) -> int:
        # This callback runs on the Qt GUI thread; queue Qt work to return
        # immediately and not hold Windows' low-level mouse hook.
        try:
            if (n_code >= 0 and message in
                    (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN)
                    and not self._closing and self.attached):
                point = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents.pt
                target = int(user32.WindowFromPoint(point) or 0)
                role = self._role_for_native_hwnd(target)
                if role:
                    card = self.cards[role]
                    # WindowFromPoint may resolve Qt's canvas rather than
                    # the genuine terminal. Never remember a Qt HWND as the
                    # native terminal's text-focus target.
                    if (target == card.console or user32.IsChild(card.console, target)):
                        self.last_focus_hwnd[role] = target
                    if (role != self.active_role
                            or card.z != max(c.z for c in self.cards.values())):
                        QTimer.singleShot(0, lambda chosen=role: self.raise_card(chosen))
        except Exception:
            # The hook must never disrupt normal mouse input.
            pass
        return int(user32.CallNextHookEx(
            self.mouse_hook, n_code, message, data))

    def _restore_active_focus(self) -> None:
        """Resume the last terminal's keyboard focus without moving its caret."""
        if (self._closing or not self.active_role or
                not self.isActiveWindow() or self.isMinimized()):
            return
        foreground = int(user32.GetForegroundWindow() or 0)
        self_hwnd = int(self.winId())
        if (foreground not in (0, self_hwnd)
                and not user32.IsChild(self_hwnd, foreground)):
            return
        card = self.cards.get(self.active_role)
        if not card or not card.console or not user32.IsWindow(card.console):
            return
        hwnd = self.last_focus_hwnd.get(self.active_role, card.console)
        if (not user32.IsWindow(hwnd)
                or not (hwnd == card.console or user32.IsChild(card.console, hwnd))):
            hwnd = card.console
        # SetFocus cannot normally target a foreign GUI thread. Attach only
        # for this one call, then detach immediately. No mouse/keypress replay.
        qt_thread = kernel32.GetCurrentThreadId()
        terminal_thread = user32.GetWindowThreadProcessId(hwnd, None)
        if not terminal_thread:
            return
        attached = False
        if qt_thread != terminal_thread:
            attached = bool(user32.AttachThreadInput(qt_thread, terminal_thread, True))
            if not attached:
                return
        try:
            user32.SetFocus(hwnd)
            # A NULL return from SetFocus can mean there was no previous
            # focus, not necessarily failure. GetFocus checks the final
            # target while input threads are attached.
            if user32.GetFocus() != hwnd:
                self.set_status("Terminal focus requested; Windows may require a click.")
        finally:
            if attached:
                user32.AttachThreadInput(qt_thread, terminal_thread, False)

    def _install_focus_listener(self) -> None:
        # Focus events from foreign console process let click-on-content
        # raise the surrounding Qt card, not just the card header.
        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                          wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        self._focus_callback = callback_type(self._on_win_focus)
        user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
              callback_type, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        # EVENT_OBJECT_FOCUS=0x8005, WINEVENT_OUTOFCONTEXT=0, WINEVENT_SKIPOWNPROCESS=0x2
        self.focus_hook = user32.SetWinEventHook(0x8005, 0x8005, None,
                                                self._focus_callback, 0, 0, 0x2)

    def _on_win_focus(self, hook, event, hwnd, obj, child, thread, millis) -> None:
        if obj not in (0, -4) or self._closing:
            return
        h = int(hwnd or 0)
        role = self._role_for_native_hwnd(h)
        if role:
            self.last_focus_hwnd[role] = h
            if (role != self.active_role
                    or self.cards[role].z != max(c.z for c in self.cards.values())):
                QTimer.singleShot(0, lambda chosen=role: self.raise_card(chosen))

    def _release_older_workspace(self) -> None:
        if not self.settings.data["capture"]["release_previous_workspace"]:
            return
        for hwnd in top_windows():
            if winclass(hwnd) != "NormWorkspaceHost":
                continue
            response = ctypes.c_size_t()
            ok = user32.SendMessageTimeoutW(hwnd, WM_COMMAND, OLD_RELEASE_ID,
                                            0, SMTO_ABORTIFHUNG | SMTO_BLOCK, 1500,
                                            ctypes.byref(response))
            if ok:
                self.set_status("Previous native workspace released its terminals.")
            else:
                self.set_status("An older workspace did not respond to its Release request.")

    def _matching_windows(self, role: str) -> list[int]:
        wanted = str(self.settings.data["capture"]["windows"][role]).casefold()
        mode = str(self.settings.data["capture"]["match"]).casefold()
        available = []
        for hwnd in top_windows():
            text = wintext(hwnd).casefold()
            if (text == wanted if mode == "exact" else wanted in text):
                if hwnd not in self.attached.values():
                    available.append(hwnd)
        return available

    def attach_existing(self) -> None:
        self.capture_enabled = True  # Explicit Attach resumes capture.
        self._release_older_workspace()
        problems = []
        for role in ROLES:
            card = self.cards[role]
            if card.console and user32.IsWindow(card.console):
                continue
            if card.console and not user32.IsWindow(card.console):
                card.console = None
                self.attached.pop(role, None)
            matches = self._matching_windows(role)
            if len(matches) > 1:
                problems.append(role+": multiple matching windows")
            elif not matches:
                problems.append(role+": not found")
            elif not card.attach(matches[0]):
                problems.append(role+": Windows refused embedding")
            else:
                self.attached[role] = matches[0]
        if len(self.attached) == 3:
            self.set_status("Attached 3 real terminal windows.")
        else:
            self.set_status(f"Attached {len(self.attached)}/3. " + "; ".join(problems))
        self.arrange_z()

    def registered_hosts_alive(self) -> int:
        path = self.settings.registry_path()
        if not path.is_file():
            return 0
        try:
            rows = json.loads(path.read_text(encoding="utf-8-sig")).get("consoles", [])
            return sum(1 for row in rows if is_live_process(int(row["pid"])))
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.set_status(f"Could not check existing Norm hosts: {error}")
            # Fail closed if registry exists but unreadable.
            return -1

    def startup(self) -> None:
        if self._test_mode:
            self.capture_enabled=False
            return
        if self.demo:
            self.start_demo()
            return
        self.attach_existing()
        if len(self.attached) == 3 or self.attach_only:
            return
        if self.settings.data["norm"].get("start_if_missing", True):
            self.start_norm()

    def start_norm(self) -> None:
        if self.demo:
            self.set_status("Demo mode: Norm startup disabled.")
            return
        self.attach_existing()
        if len(self.attached) == 3:
            return
        if self.launcher and self.launcher.poll() is None:
            self.set_status("Norm launcher already running; waiting for terminal windows.")
            return
        if self.attached and not self.settings.data["norm"].get("launch_when_partial", False):
            self.set_status("Partial terminal set detected; launcher will recheck process ownership before filling missing roles.")
        alive = self.registered_hosts_alive()
        if alive < 0:
            self.set_status("Operator registry unreadable; refusing new terminal launch.")
            return
        if alive:
            self.set_status(f"{alive} existing terminal host(s); role-specific launcher will verify/attach before adding any missing role.")
        root = self.settings.norm_root()
        startup = root / "tools/workspace/start_roles.py"
        if not root.is_dir() or not startup.is_file():
            self.set_status(f"Norm startup not found: {startup}. Check norm.root in NormWorkspace-Qt.json.")
            return
        self.started = True
        log = self.settings.root / "NormWorkspace-Qt-startup.log"
        try:
            with log.open("ab") as output:
                self.launcher = subprocess.Popen(
                    [str(root/".venv/Scripts/python.exe"), "-B", str(startup)], cwd=str(root),
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            self.set_status(f"Starting Norm via {startup.name}; waiting for operator windows.")
        except OSError as e:
            self.set_status(f"Couldn't launch Norm: {e}")

    def poll(self) -> None:
        if self._closing:
            return
        if self.capture_enabled and len(self.attached) < 3:
            # No automatic release of previous workspaces on every poll.
            self._poll_attach_only()
        if self.launcher and self.launcher.poll() is not None:
            rc = self.launcher.returncode
            self.launcher = None
            if rc:
                self.set_status(f"Norm startup returned exit {rc}; see NormWorkspace-Qt-startup.log")

    def _poll_attach_only(self) -> None:
        for role in ROLES:
            card=self.cards[role]
            if card.console and user32.IsWindow(card.console):
                continue
            matches=self._matching_windows(role)
            if len(matches)==1 and card.attach(matches[0]):
                self.attached[role]=matches[0]
                self.set_status(f"Captured {role} ({len(self.attached)}/3).")
        if len(self.attached)==3:
            self.arrange_z()

    def start_demo(self) -> None:
        self.capture_enabled = True
        for role in ROLES:
            title = str(self.settings.data["capture"]["windows"][role])
            cmd = f'title {title} & echo This is a real cmd.exe session: {title}'
            try:
                proc = subprocess.Popen(["cmd.exe", "/D", "/K", cmd],
                       creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)
                self.demo_procs.append(proc)
            except OSError as e:
                self.set_status(f"Demo start failed: {e}")
        self.set_status("Demo: waiting to adopt three real cmd.exe console windows.")

    def release_all(self) -> None:
        # Otherwise the next 700ms poll would capture these windows again.
        self.capture_enabled = False
        for role, card in self.cards.items():
            card.release()
        self.attached.clear()
        self.last_focus_hwnd.clear()
        self.set_status("Released real terminals. Norm service and consoles remain running.")

    def schedule_save(self) -> None:
        if self._setup_finished and not self._closing:
            self.save_timer.start()

    def persist(self) -> None:
        if not self._setup_finished:
            return
        ws = self.settings.data["workspace"]
        if self.isMaximized() or self.isMinimized():
            rect = self.normalGeometry()
        else:
            rect = self.geometry()
        ws.update(x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height(),
                  maximized=self.isMaximized(), active_role=self.active_role)
        for role in ROLES:
            ws["panes"][role] = self.cards[role].geometry_json()
        try:
            self.settings.save()
        except OSError as error:
            self.set_status(f"Settings couldn't be saved: {error}")

    def toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()
        self.layout_sidebar()
        self.update()
        self.schedule_save()

    def resizeEvent(self, event) -> None:
        if hasattr(self, "sidebar"):
            self.layout_sidebar()
        if getattr(self, "_setup_finished", False):
            self.schedule_save()
        super().resizeEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and hasattr(self, "sidebar"):
            self.layout_sidebar()
            self.update()
        if (event.type() == event.Type.ActivationChange
                and self.isActiveWindow()
                and getattr(self, "_setup_finished", False)):
            # Alt+Tab activated our host; restore last selected terminal focus.
            QTimer.singleShot(50, self._restore_active_focus)

    def moveEvent(self, event) -> None:
        if getattr(self, "_setup_finished", False):
            self.schedule_save()
        super().moveEvent(event)

    def paintEvent(self, event) -> None:
        painter=QPainter(self)
        painter.fillRect(self.rect(), QColor("#101a29"))
        if not self.isMaximized():
            painter.setPen(QPen(QColor("#60b7c5"),2))
            painter.drawRect(self.rect().adjusted(1,1,-2,-2))
            painter.setPen(QPen(QColor("#294b65"),2))
            painter.drawRect(self.rect().adjusted(4,4,-5,-5))
        painter.end()

    def _border_hit(self, p: QPoint):
        if self.isMaximized():
            return Qt.Edge(0)
        pad=OUTER_BORDER
        left=p.x()<pad; right=p.x()>=self.width()-pad
        top=p.y()<pad; bottom=p.y()>=self.height()-pad
        edges=Qt.Edge(0)
        if left: edges|=Qt.Edge.LeftEdge
        if right: edges|=Qt.Edge.RightEdge
        if top: edges|=Qt.Edge.TopEdge
        if bottom: edges|=Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        edges=self._border_hit(event.position().toPoint())
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge)
             or (edges & Qt.Edge.RightEdge and edges & Qt.Edge.BottomEdge) else
             Qt.CursorShape.SizeBDiagCursor if (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.BottomEdge)
             or (edges & Qt.Edge.RightEdge and edges & Qt.Edge.TopEdge) else
             Qt.CursorShape.SizeHorCursor if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge) else
             Qt.CursorShape.SizeVerCursor if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge) else
             Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges=self._border_hit(event.position().toPoint())
            if edges and self.windowHandle():
                self.windowHandle().startSystemResize(edges)
                return
            if self.windowHandle():
                self.windowHandle().startSystemMove()
                return
        super().mousePressEvent(event)

    def apply_ui_font(self):
        spec=self.settings.fonts["ui"]
        weight_map={"thin":QFont.Weight.Thin,"light":QFont.Weight.Light,"normal":QFont.Weight.Normal,
                    "medium":QFont.Weight.Medium,"semi-bold":QFont.Weight.DemiBold,
                    "bold":QFont.Weight.Bold,"black":QFont.Weight.Black}
        self.setFont(QFont(spec["face"],spec["size"],weight_map.get(spec["weight"],QFont.Weight.Normal)))
        if hasattr(self,"title_label"):
            self.title_label.setFont(QFont(spec["face"],max(12,spec["size"]+4)))
        if hasattr(self,"cards"):
            for card in self.cards.values():card.update()

    def edit_fonts(self):
        dialog=QDialog(self)
        dialog.setWindowTitle("Norm Workspace - Persistent font settings")
        outer=QVBoxLayout(dialog)
        form=QFormLayout()
        widgets={}
        for role in ("ui","runtime","replies","prompt"):
            spec=self.settings.fonts[role]
            group=QWidget(dialog);h=QHBoxLayout(group)
            h.setContentsMargins(0,0,0,0)
            face=QFontComboBox(group);face.setCurrentFont(QFont(spec["face"]))
            size=QSpinBox(group);size.setRange(6,48);size.setValue(spec["size"])
            weight=QComboBox(group);weight.addItems(sorted(VALID_WEIGHTS))
            weight.setCurrentText(spec["weight"])
            h.addWidget(face,1);h.addWidget(size);h.addWidget(weight)
            form.addRow(role.title(),group)
            widgets[role]=(face,size,weight)
        outer.addLayout(form)
        msg=QLabel("UI font applies immediately. Terminal fonts apply to NEW Windows Terminal sessions. "
                   "Existing console processes will not be restarted to change fonts.")
        msg.setWordWrap(True)
        outer.addWidget(msg)
        actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        actions.accepted.connect(dialog.accept);actions.rejected.connect(dialog.reject)
        outer.addWidget(actions)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        proposed={role:{"face":face.currentFont().family(),"size":size.value(),"weight":weight.currentText()}
                  for role,(face,size,weight) in widgets.items()}
        try:
            self.settings.set_fonts(proposed)
        except Exception as exc:
            QMessageBox.warning(self,"Font settings were not saved",str(exc))
            return
        self.apply_ui_font()
        self.set_status("Fonts saved in Norm config/settings.ini. New terminal sessions use the updated profiles.")

    def request_quit(self):
        """Graceful service exit is a precondition for destroying Qt child HWNDs."""
        if self._test_mode:
            self._shutdown_complete=True
            self.close()
            return
        if self._shutdown_proc and self._shutdown_proc.poll() is None:
            self.set_status("Shutting down Norm; waiting for pools, workers, and consoles.")
            return
        root=self.settings.norm_root()
        python=root/".venv/Scripts/python.exe"
        script=root/"tools/safe_shutdown_norm.py"
        if not python.is_file() or not script.is_file():
            self.set_status("Cannot quit: Norm shutdown helper missing; refusing to leave running processes.")
            return
        logs=root/"logs";logs.mkdir(parents=True,exist_ok=True)
        try:
            self._shutdown_log=(logs/"workspace-shutdown.log").open("ab")
            self._shutdown_proc=subprocess.Popen([str(python),"-B",str(script),"--wait-seconds","300"],
                cwd=str(root),stdin=subprocess.DEVNULL,stdout=self._shutdown_log,
                stderr=subprocess.STDOUT,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            self._shutdown_started=True
            self.buttons["Quit Norm"].setEnabled(False)
            self.shutdown_timer=QTimer(self)
            self.shutdown_timer.setInterval(600)
            self.shutdown_timer.timeout.connect(self._poll_shutdown)
            self.shutdown_timer.start()
            self.set_status("Graceful shutdown in progress. Norm service, pools, workers, browsers and operator consoles will close.")
        except Exception as error:
            self.set_status(f"Could not start verified Norm shutdown: {error}")
            self.buttons["Quit Norm"].setEnabled(True)
            if self._shutdown_log:self._shutdown_log.close();self._shutdown_log=None

    def _poll_shutdown(self):
        proc=self._shutdown_proc
        if proc is None:return
        code=proc.poll()
        if code is None:return
        self.shutdown_timer.stop()
        if self._shutdown_log:self._shutdown_log.close();self._shutdown_log=None
        self._shutdown_proc=None
        if code==0:
            self._shutdown_complete=True
            self.set_status("Verified Norm shutdown complete. Closing workspace.")
            self.close()
        else:
            self.buttons["Quit Norm"].setEnabled(True)
            self.set_status(f"Shutdown incomplete (exit {code}); workspace remains open. See logs/workspace-shutdown.log. Retry Quit Norm.")

    def closeEvent(self, event) -> None:
        if not (self._shutdown_complete or self._test_mode):
            event.ignore()
            self.request_quit()
            return
        self.persist()
        self._closing=True
        self.poll_timer.stop()
        self.save_timer.stop()
        if self.mouse_hook:
            user32.UnhookWindowsHookEx(self.mouse_hook)
            self.mouse_hook = None
        self._mouse_callback = None
        if self.focus_hook:
            user32.UnhookWinEvent.argtypes=[wintypes.HANDLE]
            user32.UnhookWinEvent(self.focus_hook)
            self.focus_hook=None
        self.release_all()
        # Never kill Norm's terminal processes, even demos, on close.
        super().closeEvent(event)

def main() -> int:
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--demo",action="store_true")
    parser.add_argument("--attach-only",action="store_true")
    parser.add_argument("--offscreen-test",action="store_true")
    args=parser.parse_args()
    if args.offscreen_test:
        os.environ["QT_QPA_PLATFORM"]="offscreen"
    # Single-instance guard: duplicate Run-Norm/Workspace launches cannot
    # create another reparenting UI or another operator-console generation.
    mutex_name="Local\\NormWorkspace-05510-test" if args.offscreen_test else "Local\\NormWorkspace-05510"
    mutex=kernel32.CreateMutexW(None,False,mutex_name)
    if not mutex:raise RuntimeError("Workspace mutex creation failed")
    if kernel32.GetLastError()==183:
        kernel32.CloseHandle(mutex)
        print("Norm Workspace is already open; reusing the existing instance.")
        return 0
    app=QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    try:
        settings=Settings()
    except (ValueError,OSError,json.JSONDecodeError) as error:
        if args.offscreen_test:
            print(f"Workspace test settings error: {error}",file=sys.stderr)
            return 2
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None,"NormWorkspace settings error",
            f"NormWorkspace-Qt.json could not be loaded:\n{error}\n\n"
            "Fix or rename the settings file, then try again.")
        return 2
    win=Workspace(settings,demo=args.demo,attach_only=args.attach_only or args.offscreen_test,
                  test_mode=args.offscreen_test)
    if args.offscreen_test:
        QTimer.singleShot(600,win.close)
    try:return app.exec()
    finally:kernel32.CloseHandle(mutex)

if __name__=="__main__":
    raise SystemExit(main())
