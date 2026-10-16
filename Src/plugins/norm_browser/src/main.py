"""General Norm browser cooperation API, extending the existing Qt shared browser.

`convo_export` is a bundled extension, not a separate browser implementation.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[3]

def _rpc(action,**args):
    token_path=ROOT/'state'/'shared_browser_token'
    if not token_path.exists():raise RuntimeError('Launch Norm Shared Browser first')
    token=token_path.read_text(encoding='ascii').strip()
    if len(token)!=64:raise RuntimeError('Invalid shared browser token')
    req=Request('http://127.0.0.1:18767/rpc',
        data=json.dumps({'action':action,'args':args},ensure_ascii=False).encode('utf8'),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(req,timeout=48) as response:response_data=json.load(response)
    except Exception as exc:raise RuntimeError('Shared browser RPC: '+str(exc)) from exc
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


def conversation_export(selected_indices: list[int], fmt: str='zip',
                        title: str='', timestamps: bool=True, models: bool=False,
                        attachments: bool=True,page_size: str='Letter',
                        density: str='comfortable') -> dict:
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
         source=status.get('source','dom'))


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
