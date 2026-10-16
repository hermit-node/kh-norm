"""Focused tests of the authenticated browser transfer hand-off.

No ChatGPT login or external network is accessed. A separate Qt smoke on KHzz
confirmed PySide6 6.12 QWebEnginePage.download -> DownloadCompleted -> bytes.
"""
from __future__ import annotations
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from norm_runtime import settings
spec=importlib.util.spec_from_file_location('norm_browser_native_test',ROOT/'plugins/norm_browser/src/main.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

with tempfile.TemporaryDirectory(prefix='norm-native-export-test-') as directory:
    workspace=Path(directory)
    temp_asset=workspace/'web'/'shared_browser'/'asset-012345'/'asset.bin'
    temp_asset.parent.mkdir(parents=True)
    fixture=b'\xff\xd8\xff'+b'K'*244
    temp_asset.write_bytes(fixture)
    calls=[]
    before=settings.load_path_settings
    def simulated_rpc(action,**args):
        calls.append((action,args))
        if action=='convo_asset_native_start':
            assert args['url'].startswith('https://files.oaiusercontent.com/')
            return {'ok':True,'state':'loading'}
        if action=='convo_asset_native_status':
            return {'state':'ready','path':str(temp_asset),'length':len(fixture)}
        if action=='convo_asset_native_drop':return {'dropped':True}
        raise AssertionError('Unexpected RPC '+action)
    settings.load_path_settings=lambda ignored: {'workspace_root':str(workspace)}
    mod._rpc=simulated_rpc
    target=workspace/'output.jpg'
    try:
        size=mod._asset_via_browser_native('https://files.oaiusercontent.com/signed?token=synthetic',target)
        assert size==len(fixture) and target.read_bytes()==fixture
        assert [a for a,_ in calls]==['convo_asset_native_start','convo_asset_native_status','convo_asset_native_drop']
        calls.clear()
        external=workspace.parent/'evil-untrusted-location.bin'
        # Explicitly reject out-of-workspace paths even if an RPC reports ready.
        def bad_rpc(action,**args):
            if action=='convo_asset_native_start':return {'ok':True}
            if action=='convo_asset_native_status':return {'state':'ready','path':str(external),'length':3}
            if action=='convo_asset_native_drop':return {'dropped':True}
            raise AssertionError(action)
        mod._rpc=bad_rpc
        try:mod._asset_via_browser_native('https://files.oaiusercontent.com/signed',target)
        except RuntimeError as exc: assert 'invalid temporary location' in str(exc)
        else:raise AssertionError('Accepted an unauthorized local file path')
        # Native 403/unsupported path should not prevent a same-origin JS fallback.
        def failed_native(*args):raise PermissionError('Native download denied')
        def javascript(url,dest):dest.write_bytes(fixture);return len(fixture)
        mod._asset_via_browser_native=failed_native
        mod._asset_via_browser_js=javascript
        result=mod._asset_via_browser('https://chatgpt.com/first-party/image',target)
        assert result==len(fixture) and target.read_bytes()==fixture
        print('PASS Qt-native media path, byte preservation, local-path guard, JS fallback')
    finally:settings.load_path_settings=before
