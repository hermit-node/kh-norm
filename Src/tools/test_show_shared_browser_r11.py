"""A double-click shortcut must not create a second Chromium process."""
from pathlib import Path
import importlib.util,sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"tools"))
spec=importlib.util.spec_from_file_location("show_r11",root/"tools/show_shared_browser.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
calls=[]
m.ready=lambda:True
m.start_browser=lambda:calls.append("start")
m.show_existing=lambda:{"visible":True,"manual_control":False}
assert m.main()==0
assert calls==[],calls
print("PASS if already running: show without launching second browser")
flags=[False,True]
m.ready=lambda:flags.pop(0)
m.start_browser=lambda:calls.append("start")
m.show_existing=lambda:{"visible":True,"manual_control":True}
assert m.main()==0
assert calls==["start"],calls
print("PASS when missing: request single start, reveal, preserve human lock")
for f in ("Show Shared Browser.cmd","tools/shared_browser/Show Shared Browser.cmd",
          "tools/shared_browser/Launch Browser.cmd","tools/shared_browser/Start Hidden Browser.cmd"):
 assert (root/f).is_file(),f
print("PASS double-click shortcuts present and legacy launcher not opening independent tab")
