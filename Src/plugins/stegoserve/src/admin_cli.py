"""Operator-only StegoServe command line interface.

This file is intentionally NOT a Norm-discovered tool entrypoint. It is invoked
locally by an administrator, who types user-selected passwords through getpass.
Private signing keys remain with the standalone issuer and are never returned
through Norm's model/tool interface.
"""
from __future__ import annotations
import argparse
import getpass
import json
import sys
from pathlib import Path
import bridge


def _load_engine(backend: str | None):
    root=bridge.locate_backend(backend)
    if str(root) not in sys.path:
        sys.path.insert(0,str(root))
    import authority, paired_auth, formula_profiles, authgate_assignment
    return root,authority,paired_auth,formula_profiles,authgate_assignment


def _load_plan(path: str, expected_company: str) -> dict:
    doc=json.loads(Path(path).read_text(encoding='utf-8'))
    from main import plan_batch
    if doc.get('schema')!='stegoserve/batch-plan/1' or doc.get('company')!=expected_company:
        raise ValueError('plan has unexpected company or schema')
    entries=doc.get('users')
    if not isinstance(entries,list) or not 1<=len(entries)<=500:
        raise ValueError('invalid user batch')
    if len(entries)!=len({u['user'] for u in entries}):
        raise ValueError('duplicated users')
    dest=Path(entries[0]['b_image']).resolve().parent
    refreshed=plan_batch(expected_company,[u['user'] for u in entries],
                         str(dest),doc['expiration_days'])
    if not refreshed.get('ok') or refreshed['users']!=entries:
        raise ValueError('tampered or inconsistent batch plan; recreate it in Norm')
    for entry in entries:
        for name in ('b_image','enrollment'):
            if Path(entry[name]).exists():
                raise FileExistsError('output already exists: '+entry[name])
    return doc


def _user_passwords(plan: dict) -> dict[str,str]:
    # Fetch all chosen passwords before any issuing, so operator cancel does
    # not create an incomplete mix of initialized accounts and images.
    result={}
    for row in plan['users']:
        user=row['user']
        first=getpass.getpass('Password for '+user+': ')
        second=getpass.getpass('Confirm for '+user+': ')
        if not first or first!=second:
            raise ValueError('password missing or confirmation mismatch for '+user)
        result[user]=first
    return result


def _check_issuer(authority, profiles, issuer: str, verifier: str, company: str):
    issuing=profiles.issuer_profile(issuer,company)
    active=profiles.verifier_profile(verifier)
    if issuing['server_account']!=active['server_account'] or issuing['seed']!=active['seed']:
        raise ValueError('company seed and verifier do not match')
    return issuing,active


def _issue_batch(authority, profiles, args) -> dict:
    plan=_load_plan(args.plan,args.company)
    issuer=Path(args.issuer).resolve()
    verifier=Path(args.verifier).resolve()
    issuing,active=_check_issuer(authority,profiles,str(issuer),str(verifier),args.company)
    if issuing['round']!=active['round']:
        raise ValueError('issuer and verifier have different rounds; resolve before issuing')
    passwords=_user_passwords(plan)
    created=[]
    for u in plan['users']:
        issued=authority.issue_user(str(issuer),u['user'],u['b_image'],
            passwords[u['user']],u['enrollment'],scopes=('login',),
            expires_days=plan['expiration_days'],
            server_account=args.company,formula_slot=u['formula_slot'])
        created.append(issued)
    # Keep the signed B enrollment manifests atomic with issuance where
    # possible: no enrollment is attempted until all 16 B images self-verify.
    for u in plan['users']:
        authority.enroll(str(verifier),u['enrollment'])
    return {'ok':True,'company':args.company,'issued':len(created),
            'round':issuing['round'],'user_ids':[u['user'] for u in plan['users']],
            'output_dir':str(Path(plan['users'][0]['b_image']).parent),
            'passwords_saved':False}


def _rotate_batch(authority, profiles, args) -> dict:
    plan=_load_plan(args.plan,args.company)
    issuer=Path(args.issuer).resolve()
    verifier=Path(args.verifier).resolve()
    issuing,active=_check_issuer(authority,profiles,str(issuer),str(verifier),args.company)
    if issuing['round']!=active['round']:
        raise ValueError('rotation already pending; do not advance the round twice')
    print('Rotation will issue replacement images and invalidate ALL current B images after cutover.',file=sys.stderr)
    confirmation=input('Type ROTATE '+args.company+' to proceed: ')
    if confirmation!='ROTATE '+args.company:raise ValueError('rotation not confirmed')
    passwords=_user_passwords(plan)
    rotated=authority.hard_reset(str(issuer),args.company)
    for u in plan['users']:
        authority.issue_user(str(issuer),u['user'],u['b_image'],
            passwords[u['user']],u['enrollment'],scopes=('login',),
            expires_days=plan['expiration_days'],
            server_account=args.company,formula_slot=u['formula_slot'])
    # Cutover only once all replacement B images were produced and self-verified.
    authority.sync_profile(str(issuer),str(verifier))
    for u in plan['users']:
        authority.enroll(str(verifier),u['enrollment'])
    return {'ok':True,'company':args.company,'new_round':rotated['round'],
            'users':len(plan['users']),'old_B_images_invalid':True,
            'passwords_saved':False}


def main() -> int:
    p=argparse.ArgumentParser(description='StegoServe local operator provisioning (NOT a network gate)')
    p.add_argument('--backend', help='extracted Stego AuthGate v0.6 release directory')
    commands=p.add_subparsers(dest='action',required=True)
    init=commands.add_parser('init-company',help='new company logo and server A image')
    init.add_argument('--image',required=True)
    init.add_argument('--company',required=True)
    init.add_argument('--issuer',required=True)
    init.add_argument('--verifier',required=True)
    batch=commands.add_parser('issue-batch',help='issue and enroll a Norm-created plan')
    rotate=commands.add_parser('rotate-batch',help='advance company rotor round and reissue users')
    for cmd in (batch,rotate):
        cmd.add_argument('--plan',required=True)
        cmd.add_argument('--company',required=True)
        cmd.add_argument('--issuer',required=True)
        cmd.add_argument('--verifier',required=True)
    route=commands.add_parser('sign-route',help='sign the destination allowed behind AuthGate')
    route.add_argument('--company',required=True)
    route.add_argument('--issuer',required=True)
    route.add_argument('--verifier',required=True)
    route.add_argument('--host',required=True)
    route.add_argument('--port',type=int,required=True)
    route.add_argument('--out',required=True)
    route.add_argument('--allow-user',action='append')
    revoke=commands.add_parser('revoke',help='revoke a signed user enrollment')
    revoke.add_argument('--verifier',required=True)
    revoke.add_argument('--credential-id',required=True)
    args=p.parse_args()
    _,authority,paired,profiles,assignment=_load_engine(args.backend)
    if args.action=='init-company':
        src=Path(args.image).resolve()
        if not src.is_file() or src.suffix.lower()!='.png':
            raise ValueError('source image must be existing lossless PNG')
        created=authority.initialize_issuer(str(src),args.issuer,server_account=args.company)
        created['verifier']=authority.provision_verifier(args.issuer,args.verifier,
                                                           server_account=args.company)
        result=created
    elif args.action=='issue-batch':
        result=_issue_batch(authority,profiles,args)
    elif args.action=='rotate-batch':
        result=_rotate_batch(authority,profiles,args)
    elif args.action=='sign-route':
        result=assignment.issue_route(args.issuer,args.verifier,args.company,
            args.host,args.port,args.out,args.allow_user)
    elif args.action=='revoke':
        result=paired.revoke(args.verifier,args.credential_id)
    else:
        raise AssertionError(args.action)
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
