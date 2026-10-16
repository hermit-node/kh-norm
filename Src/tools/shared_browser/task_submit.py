"""Send contextual Ask Norm messages through the EXISTING console ingress stream.

This is not a new agent, service, or direct task runner. The normal GUI prompt
consumer must be active to dispatch the durable Redis stream entry to N1/N2.
"""
from __future__ import annotations
import uuid
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def enqueue_browser_task(text: str, page_url: str, page_title: str) -> dict:
    instruction=str(text or '').strip()
    if not instruction:raise ValueError('Enter a browser task')
    if len(instruction)>30000:raise ValueError('Browser task exceeds 30,000 characters')
    if not page_url.startswith(('http://','https://')):
        raise ValueError('Open an HTTP(S) page before submitting a contextual task')
    import sys
    core=str(ROOT/'core')
    if core not in sys.path:sys.path.insert(0,core)
    from runtime_bootstrap import load_config
    import redis
    cfg=load_config(ROOT)['console_queue']
    client=redis.Redis(host=cfg['host'],port=int(cfg['port']),db=int(cfg.get('db',3)),
                       decode_responses=True,socket_connect_timeout=5,socket_timeout=8)
    stream=str(cfg.get('console_ingress_stream','norm:gui:ingress'))
    group=str(cfg.get('console_ingress_group','norm-gui-dispatchers'))
    try:
        groups=client.xinfo_groups(stream)
    except redis.ResponseError as exc:
        raise RuntimeError('Norm prompt console ingress is not initialized; start the existing Norm Prompt Console') from exc
    active=any(g.get('name')==group and int(g.get('consumers',0))>0 for g in groups)
    if not active:raise RuntimeError('Norm Prompt Console dispatcher is not running. Start it before sending browser tasks.')
    prompt=('Use /web-browse with the existing Norm Shared Browser page as the starting target.\n'
            'Originating page URL: '+page_url+'\n'
            'Originating page title: '+str(page_title or '')[:250]+'\n'
            'The URL and tab were captured at submission; do not silently switch to a different active tab.\n'
            'User request: '+instruction)
    prompt_id=str(uuid.uuid4())
    entry=client.xadd(stream,{'kind':'prompt','prompt_id':prompt_id,'message':prompt,
               'project_id':'default','source':'norm-browser','enqueued_at':datetime.now(timezone.utc).isoformat()},
               maxlen=5000,approximate=True)
    client.set(str(cfg.get('console_last_submission_key','norm:gui:last-submission')),prompt)
    return {'queue_entry':entry,'prompt_id':prompt_id,'status':'accepted by existing console ingress'}
