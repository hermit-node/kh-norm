"""General Norm browser cooperation API, extending the existing Qt shared browser.

`convo_export` is a bundled extension, not a separate browser implementation.
"""
from __future__ import annotations
import json
import time
import base64
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import subprocess

ROOT=Path(__file__).resolve().parents[3]

def _start_if_needed(action: str) -> bool:
    """Lazy-start on explicit browser operations; never on status/monitoring.

    The launcher alone owns session selection and reuses the existing Qt tab.
    """
    if action in ('status','cooperation_status','visual_pdf_status'):
        return False
    # The browser token is persisted, so its presence does not mean it is running.
    py=ROOT/'.venv'/'Scripts'/'python.exe'
    root=ROOT
    launcher=root/'tools'/'start_shared_browser.py'
    if not py.is_file() or not launcher.is_file():
        raise RuntimeError('Norm shared browser launcher or .venv is missing')
    try:
        result=subprocess.run([str(py),'-B',str(launcher)],cwd=str(root),
                              capture_output=True,text=True,timeout=18)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('On-demand shared browser launch failed: '+str(exc)) from exc
    if result.returncode:
        raise RuntimeError('On-demand shared browser launch failed: '+(result.stderr or result.stdout)[-400:])
    return True

def _rpc(action,**args):
    token_path=ROOT/'state'/'shared_browser_token'
    if not token_path.exists():
        if action in ('cooperation_status',):
            return {'running':False,'state':'not_started','on_demand':True}
        _start_if_needed(action)
    if not token_path.exists():
        raise RuntimeError('Shared browser is not running in the Windows login session')
    token=token_path.read_text(encoding='ascii').strip()
    if len(token)!=64:raise RuntimeError('Invalid shared browser token')
    req=Request('http://127.0.0.1:18767/rpc',
        data=json.dumps({'action':action,'args':args},ensure_ascii=False).encode('utf8'),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(req,timeout=48) as response:response_data=json.load(response)
    except HTTPError as exc:
        try:payload=json.load(exc)
        except Exception:payload={}
        raise RuntimeError(str(payload.get('error') or 'Browser HTTP '+str(exc.code))) from exc
    except OSError as exc:
        if action == 'cooperation_status':
            return {'running':False,'state':'not_started','on_demand':True}
        _start_if_needed(action)
        try:
            with urlopen(req,timeout=48) as response:response_data=json.load(response)
        except HTTPError as second:
            try:payload=json.load(second)
            except Exception:payload={}
            raise RuntimeError(str(payload.get('error') or 'Browser HTTP '+str(second.code))) from second
        except OSError as second:
            raise RuntimeError('Shared browser could not start in the interactive Windows session') from second
    if not response_data.get('ok'):raise RuntimeError(str(response_data.get('error') or 'Browser error'))
    return response_data.get('result') or {}


def browser_cooperation_status() -> dict:
    """Report the shared browser page, script permissions and installed page adapters."""
    return _rpc('cooperation_status')


def browser_script_execute(source: str, world: str='isolated') -> dict:
    """Execute bounded custom JavaScript in the approved HTTPS page. No Python privileges exposed."""
    return _rpc('script_execute',source=source,world=world)


def browser_script_register(name: str, source: str, world: str='isolated', timing: str='ready') -> dict:
    """Register a named domain-specific JavaScript adapter for future page loads."""
    return _rpc('script_register',name=name,source=source,world=world,timing=timing)


def browser_script_remove(name: str) -> dict:
    """Remove a saved browser script; existing page hooks may require reload."""
    return _rpc('script_remove',name=name)


def conversation_preview(start: int=0,count: int=50,refresh: bool=False) -> dict:
    """Preview selectable ChatGPT turns, API-first, with explicit completeness state."""
    if refresh:_rpc('convo_start')
    status=_rpc('convo_status')
    if status.get('state')=='none':
        _rpc('convo_start');status=_rpc('convo_status')
    if status.get('state')=='ready':
        status.update(_rpc('convo_preview',start=max(0,int(start)),count=max(1,min(100,int(count)))))
    return status


def _collect_one(index: int, expected_id: str) -> dict:
    start=0;pieces=[];metadata=None
    while True:
        row=_rpc('convo_item',index=index,start=start,length=24000)
        if row.get('error'):raise RuntimeError(row['error'])
        if str(row.get('id'))!=str(expected_id):raise RuntimeError('Conversation changed during export')
        if metadata is None:metadata={k:v for k,v in row.items() if k not in ('text','text_length')}
        piece=row.get('text','');pieces.append(piece)
        start+=len(piece)
        if start>=int(row.get('text_length') or 0):break
        if not piece:raise RuntimeError('Truncated conversation message')
    metadata['text']=''.join(pieces)
    return metadata


def _asset_via_browser_js(url: str, destination: Path) -> int:
    """Fetch into the already-authenticated Qt page, then stream bounded base64 chunks.

    This is a fallback for ChatGPT-origin assets not accessible to Python's
    unauthenticated HTTPS client. Bytes never go to an external PDF service.
    """
    started=_rpc('convo_asset_start',url=url)
    if not started.get('ok'):
        raise RuntimeError(str(started.get('error') or 'Browser asset fetch rejected'))
    try:
        deadline=time.monotonic()+45.0
        status={}
        while time.monotonic()<deadline:
            status=_rpc('convo_asset_status')
            if status.get('state') in ('ready','failed'):
                break
            time.sleep(.15)
        if status.get('state')!='ready':
            raise RuntimeError(str(status.get('error') or 'Browser asset fetch timeout'))
        total=int(status.get('length') or 0)
        if not 0<total<=24*1024*1024:
            raise RuntimeError('Browser asset exceeds size limit')
        written=0
        with destination.open('wb') as file:
            while written<total:
                part=_rpc('convo_asset_chunk',start=written,length=24576)
                if part.get('error') or int(part.get('start',-1))!=written or int(part.get('length',-1))!=total:
                    raise RuntimeError('Browser asset transfer failed continuity check')
                decoded=base64.b64decode(part.get('base64') or '',validate=True)
                if not decoded or len(decoded)>min(24576,total-written):
                    raise RuntimeError('Browser asset transfer returned invalid chunk')
                file.write(decoded)
                written+=len(decoded)
        return written
    finally:
        try:_rpc('convo_asset_drop')
        except Exception:pass



def _asset_via_browser_native(url: str, destination: Path) -> int:
    """CORS-independent Chromium-profile download, extension-worker equivalent.

    Qt downloads into a disposable local directory using the logged-in cookie
    jar. Copy the bytes into the export staging folder, then discard the temp.
    """
    import shutil
    from norm_runtime.settings import load_path_settings
    allowed=(Path(load_path_settings(ROOT)['workspace_root'])/
             'web'/'shared_browser').resolve()
    started=_rpc('convo_asset_native_start',url=url)
    if not started.get('ok'):
        raise RuntimeError(str(started.get('error') or 'Native download rejected'))
    try:
        deadline=time.monotonic()+38
        status={}
        while time.monotonic()<deadline:
            status=_rpc('convo_asset_native_status')
            if status.get('state') in ('ready','failed'):break
            time.sleep(.2)
        if status.get('state')!='ready':
            raise RuntimeError(str(status.get('error') or 'Chromium native download timed out'))
        length=int(status.get('length') or 0)
        file=Path(str(status.get('path') or '')).resolve()
        if not file.is_relative_to(allowed) or file.name!='asset.bin':
            raise RuntimeError('Native asset returned an invalid temporary location')
        if not 0<length<=256*1024*1024 or not file.is_file() or file.stat().st_size!=length:
            raise RuntimeError('Native asset has inconsistent size')
        shutil.copyfile(file,destination)
        return length
    finally:
        try:_rpc('convo_asset_native_drop')
        except Exception:pass


def _asset_via_browser(url: str, destination: Path) -> int:
    """Try the real Chromium background network stack before page JS fetch.

    The old JavaScript fallback remains useful for first-party URLs, but the
    native download avoids the CORS restrictions that caused previous failures.
    """
    try:
        return _asset_via_browser_native(url,destination)
    except Exception as native_error:
        destination.unlink(missing_ok=True)
        try:
            return _asset_via_browser_js(url,destination)
        except Exception as js_error:
            raise RuntimeError('Authenticated Chromium download: '+str(native_error)[:120]+
                               '; page transfer: '+str(js_error)[:130]) from js_error


def conversation_export(selected_indices: list[int], fmt: str='zip',
                        title: str='', timestamps: bool=True, models: bool=False,
                        attachments: bool=True,page_size: str='Letter',
                        density: str='comfortable',
                        allow_incomplete_assets: bool=False) -> dict:
    """Export selected conversation turns locally with assets, PDF/MD/JSON/ZIP.

    Requires an already-prepared browser snapshot. The user may choose any
    subset of messages; unresolved files are accounted for in manifest.json.
    """
    import importlib.util
    if not isinstance(selected_indices,list) or not selected_indices:
        raise ValueError('Select one or more message indices')
    if len(selected_indices)>30000:raise ValueError('Too many messages')
    status=_rpc('convo_status')
    if status.get('state')!='ready':raise RuntimeError('Run conversation_preview and wait for ready first')
    unique=sorted(set(int(v) for v in selected_indices))
    if unique[0]<0 or unique[-1]>=status['count']:raise ValueError('Selection outside conversation')
    summaries={}
    for start in range(0,status['count'],100):
        page=_rpc('convo_preview',start=start,count=min(100,status['count']-start))
        summaries.update({int(m['index']):m for m in page.get('messages',[])})
    messages=[_collect_one(i,summaries[i]['id']) for i in unique]
    after=_rpc('convo_status')
    if after.get('url')!=status.get('url') or after.get('count')!=status.get('count'):
        raise RuntimeError('Conversation changed; export was not published')
    file=Path(__file__).resolve().parent/'extensions'/'convo_export'/'exporter.py'
    spec=importlib.util.spec_from_file_location('norm_convo_exporter',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from sys import path as sys_path
    core=str(ROOT/'core')
    if core not in sys_path:sys_path.insert(0,core)
    from norm_runtime.settings import load_path_settings
    workspace=Path(load_path_settings(ROOT)['workspace_root'])/'web'/'shared_browser'/'exports'
    return module.export_conversation(messages,root=workspace,title=title or status.get('title') or 'ChatGPT',
         fmt=fmt,options={'timestamps':timestamps,'models':models,'attachments':attachments,
                          'page_size':page_size,'density':density},
         source_url=status.get('url',''),complete=bool(status.get('complete')),
         source=status.get('source','dom'),
         asset_fetcher=_asset_via_browser if attachments else None,
         allow_incomplete_assets=allow_incomplete_assets,
         attachment_resolution=status.get('attachment_resolution'))


def browser_open(url: str, mode: str='shared') -> dict:
    """Open in existing shared Qt session, or in independent visible/headless Playwright."""
    mode=str(mode).strip().lower()
    if mode=='shared':return _rpc('open',url=url)
    if mode not in ('playwright','headless'):
        raise ValueError('mode must be shared, playwright, or headless')
    import importlib,sys
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    web=importlib.import_module('plugins.web_browser.src.main')
    result=web.web_open(url,headless=mode=='headless')
    return {'mode':mode,'result':result,'note':'Independent Playwright session; does not share Qt cookies/profile'}
