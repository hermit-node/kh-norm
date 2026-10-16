"""r11r9 shared browser packaging smoke test (no PySide6, network, or login required)."""
import ast
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from norm_runtime.plugin_identity import verify_identity

app=(ROOT/'tools/shared_browser/app.py').read_text(encoding='utf8')
launcher=(ROOT/'Run-Norm.bat').read_text(encoding='utf8')
assert 'start_shared_browser.py' not in launcher, 'Unexpected eager Chromium startup'
assert 'self.visual=None' in app and 'from visual_capture import VisualCapture' in app
assert 'newWindowRequested.connect(self._new_window_requested)' in app
assert 'browser_shared_chatgpt_diagnostics' in (ROOT/'plugins/shared_browser/src/main.py').read_text(encoding='utf8')
module=ast.parse(app)
constants={}
for node in module.body:
    if isinstance(node,ast.Assign):
        for target in node.targets:
            if isinstance(target,ast.Name) and target.id in ('TYPE_SCRIPT','SCROLL_SCRIPT'):
                constants[target.id]=ast.literal_eval(node.value)
assert len(constants)==2
assert "document.execCommand('insertText'" in constants['TYPE_SCRIPT']
assert 'conversation_ancestor' in constants['SCROLL_SCRIPT']
for name in ('shared_browser','norm_browser'):
    data=verify_identity(ROOT/'plugins'/name)
    assert data and len(data['sha256'])==64
manifest=json.loads((ROOT/'package-manifest.json').read_text(encoding='utf8'))
assert ('r11r9' in manifest['version'] or 'r11r10' in manifest['version']) and 'no-celery' in manifest['version']
print('PASS r11r9 shared browser source/integrity/offline smoke tests')
print('LIVE CHATGPT QT SESSION NOT TESTED')
