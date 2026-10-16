"""Test visibility and control as independent Qt state machines (offscreen)."""
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS","--no-sandbox --disable-gpu")
root=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(root/"core"))
from norm_runtime import settings as _settings
_sandbox=TemporaryDirectory(prefix="norm-r11-config-")
_settings.load_path_settings=lambda _:{"workspace_root":Path(_sandbox.name)}
s=importlib.util.spec_from_file_location("norm_qt_browser_r11",root/"tools/shared_browser/app.py")
m=importlib.util.module_from_spec(s)
s.loader.exec_module(m)
app=m.QApplication([])
# Chromium keeps profile files open until the Qt application terminates.
# Do not turn a Windows temporary-folder unlink race into a false test failure.
with TemporaryDirectory(prefix="norm-r11-control-",ignore_cleanup_errors=True) as temp:
    m.BASE=Path(temp)
    browser=m.Browser()
    def run(action,**args):
        job={"action":action,"args":args,"event":Event(),"result":{},"error":""}
        browser.dispatch(job)
        assert job["event"].is_set(),(action,"missing response")
        return job
    initial=run("status")["result"]
    assert initial["visible"] is False and initial["manual_control"] is False
    shown=run("show")["result"]
    assert shown["visible"] is True and shown["manual_control"] is False
    assert browser.take_button.isEnabled() and not browser.release_button.isEnabled()
    locked=run("take_control")["result"]
    assert locked["visible"] and locked["manual_control"]
    assert not browser.take_button.isEnabled() and browser.release_button.isEnabled()
    denied=run("open",url="https://example.com")
    assert "Human controls" in denied["error"] and not denied["result"]
    hidden=run("hide")["result"]
    assert hidden["visible"] is False and hidden["manual_control"] is True
    exposed=run("show")["result"]
    assert exposed["visible"] is True and exposed["manual_control"] is True
    released=run("release")["result"]
    assert released["visible"] is True and released["manual_control"] is False
    assert browser.take_button.isEnabled() and not browser.release_button.isEnabled()
    opened=run("open",url="https://example.com")
    assert not opened["error"] and opened["result"]["navigation_started"]
    hidden=run("hide")["result"]
    assert hidden["visible"] is False and hidden["manual_control"] is False
    released=run("release")["result"]
    assert released["visible"] is False and released["manual_control"] is False
    browser.show()
    browser.close()
    assert not browser.isVisible() and not browser.manual
    browser.page.deleteLater()
print("PASS Show/Hide preserve control; Take locks Norm; Release preserves visibility; close hides only")
app.quit()

_sandbox.cleanup()
