"""Norm's StegoServe provisioning/planning facade (no live gateway listener).

Only read-only / preparatory actions are exported through Norm's dynamic tool
broker. Administrative issuance, rotation, revocation and policy signing are
performed in an explicitly opened local operator terminal (admin_cli.py).
"""
from __future__ import annotations
from typing import Any
from pathlib import Path
import json
import re
import bridge

PLUGIN_VERSION = '0.6.0-norm-adapter'
_USER_RE = re.compile(r'^[A-Za-z0-9_.@-]{1,96}$')
_COMPANY_RE = re.compile(r'^[a-z][a-z0-9_-]{0,8}$')


def engine_status(backend_dir: str | None = None) -> dict:
    """Discover the independently installed StegoSplit/Stego AuthGate engine."""
    return bridge.status(backend_dir)


def profile_info(verifier_dir: str, backend_dir: str | None = None) -> dict:
    """Read public company formula slots/complexities; do not expose private seeds."""
    return bridge.public_profile(verifier_dir, backend_dir)


def gateway_preflight(config_file: str, backend_dir: str | None = None) -> dict:
    """Verify gateway signed routes and TLS setup without opening listener ports."""
    return bridge.gate_config_preflight(config_file, backend_dir)


def plan_batch(company: str, users: list[str], output_dir: str,
               expiration_days: int = 30) -> dict:
    """Prepare a bulk credential plan without handling or logging user passwords."""
    if not isinstance(company,str):
        return {'ok':False,'error':'company alias must be text'}
    company=company.strip().lower()
    if not _COMPANY_RE.fullmatch(company):
        return {'ok':False,'error':'company alias must match [a-z][a-z0-9_-]{0,8}'}
    if not isinstance(users, list) or not 1<=len(users)<=500 or any(
        not isinstance(u,str) or not _USER_RE.fullmatch(u) for u in users):
        return {'ok': False, 'error':'provide 1-500 valid user identities'}
    if len(users)!=len(set(users)):
        return {'ok':False,'error':'duplicate user identity'}
    if not isinstance(expiration_days,int) or not 1<=expiration_days<=3650:
        return {'ok':False,'error':'expiration_days must be 1..3650'}
    dest=Path(output_dir).expanduser().resolve()
    entries=[{'user':user,'formula_slot':i%16,
              'b_image':str(dest / (user+'.png')),
              'enrollment':str(dest / (user+'.enroll.json'))}
             for i,user in enumerate(users)]
    return {'ok':True,'schema':'stegoserve/batch-plan/1',
            'company':company,'expiration_days':expiration_days,
            'users':entries,'passwords_in_plan':False,
            'catalog':128,'complexity_tiers_per_16':[4,4,4,4],
            'note':'Each user enters their password privately during local admin issuance.'}


def plan_rotation(company: str, verifier_dir: str, new_output_dir: str) -> dict:
    """Explain a coordinated rotor-round change without changing active access."""
    if not isinstance(company,str) or not _COMPANY_RE.fullmatch(company.strip().lower()):
        return {'ok':False,'error':'invalid company alias'}
    company=company.strip().lower()
    return {'ok':True,'company':company,'current_verifier':str(Path(verifier_dir).resolve()),
            'new_user_image_dir':str(Path(new_output_dir).resolve()),
            'operation':'rotate issuer round/seed, reissue all B images, sync verifier and enroll',
            'requires_local_operator':True,
            'old_B_images_after_cutover':'invalid',
            'rollback':'retain verifier old round until new credentials are ready'}


def run(payload: dict[str, Any]) -> dict:
    """Dispatch safe status/planning operations; never expose issuer mutations."""
    if not isinstance(payload,dict):return {'ok':False,'error':'expected JSON object'}
    action=payload.get('action','status')
    if action=='status':return engine_status(payload.get('backend_dir'))
    if action=='profile':return profile_info(payload['verifier_dir'],payload.get('backend_dir'))
    if action=='preflight':return gateway_preflight(payload['config_file'],payload.get('backend_dir'))
    if action=='plan_batch':
        return plan_batch(payload['company'],payload['users'],payload['output_dir'],
                          payload.get('expiration_days',30))
    if action=='plan_rotation':
        return plan_rotation(payload['company'],payload['verifier_dir'],payload['new_output_dir'])
    if action=='save_plan':
        return save_batch_plan(payload['company'], payload['users'], payload['output_dir'],
                               payload['plan_file'], payload.get('expiration_days',30))
    if action=='save_gateway_config':
        return save_gateway_config(payload['config_file'],payload['listener_port'],
            payload['tls_certificate'],payload['tls_private_key_file'], payload['routes'],
            payload.get('blocking_port',0))
    return {'ok':False,'error':'operator-only mutation; run admin_cli.py locally'}


def save_batch_plan(company: str, users: list[str], output_dir: str,
                    plan_file: str, expiration_days: int = 30) -> dict:
    """Save a non-secret company issuance plan, refusing to overwrite existing files."""
    plan=plan_batch(company,users,output_dir,expiration_days)
    if not plan.get('ok'):return plan
    path=Path(plan_file).expanduser().resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as handle:
        json.dump(plan,handle,sort_keys=True,indent=2)
    return {'ok':True,'plan_file':str(path),'company':plan['company'],
            'users':len(users),'contains_passwords':False,
            'next':'Run stegoserve/src/admin_cli.py issue-batch --plan ... from local operator console.'}


def save_gateway_config(config_file: str, listener_port: int,
                        tls_certificate: str, tls_private_key_file: str,
                        routes: list[dict[str, str]], blocking_port: int = 0,
                        listener_bind: str = '127.0.0.1') -> dict:
    """Write AuthGate's declarative policy/configuration without starting or exposing a port."""
    if not isinstance(listener_port,int) or not 1024<=listener_port<=65535:
        return {'ok':False,'error':'listener_port must be 1024..65535'}
    if not isinstance(blocking_port,int) or not 0<=blocking_port<=65535 or blocking_port==listener_port:
        return {'ok':False,'error':'blocking port must differ from the AuthGate listener'}
    if listener_bind not in ('127.0.0.1','::1'):
        return {'ok':False,'error':'Norm only prepares loopback configs; change external bind after deployment review'}
    if not isinstance(routes,list) or not 1<=len(routes)<=100:
        return {'ok':False,'error':'at least one signed company routing manifest is required'}
    normalized=[]
    for entry in routes:
        if not isinstance(entry,dict) or set(entry)!={'manifest','issuer_pub'}:
            return {'ok':False,'error':'each route requires manifest and issuer_pub paths'}
        normalized.append({'manifest':str(Path(entry['manifest']).expanduser().resolve()),
                           'issuer_pub':str(Path(entry['issuer_pub']).expanduser().resolve())})
    target=Path(config_file).expanduser().resolve()
    doc={'schema':'stego-authgate/config/1',
         'listener':{'bind':listener_bind,'port':listener_port,
                     'cert':str(Path(tls_certificate).expanduser().resolve()),
                     'key':str(Path(tls_private_key_file).expanduser().resolve())},
         'session':{'lease_seconds':3600,'heartbeat_grace':360},
         'routes':normalized}
    if blocking_port:
        doc['blocking_listener']={'bind':listener_bind,'port':blocking_port}
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as f:
        json.dump(doc,f,sort_keys=True,indent=2)
    return {'ok':True,'config_file':str(target),'companies':len(normalized),
            'network_listener_started':False,
            'next':'Run gateway_preflight, then start standalone authgate_server.py explicitly.'}
