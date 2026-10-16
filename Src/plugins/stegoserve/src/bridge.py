"""Validated bridge from Norm's StegoServe plugin to independent v0.6 engine.

Does not run a network listener in Norm; never imports issuer signing secrets
into Norm's normal tool process and never accepts arbitrary shell commands.
"""
from __future__ import annotations
import os
import sys
import json
import subprocess
import hashlib
from pathlib import Path

_REQUIRED = ('ss_ctl.py', 'authority.py', 'paired_auth.py', 'formula_catalog128.cp314-win_amd64.pyd',
             'formula_profiles.cp314-win_amd64.pyd', 'authgate_assignment.py', 'authgate_server.py')


def candidates() -> list[Path]:
    paths = []
    env = os.environ.get('STEGOSERVE_BACKEND_DIR', '').strip()
    if env:
        paths.append(Path(env).expanduser())
    # A backend placed beside the extracted Norm source is also supported.
    root = Path(__file__).resolve().parents[3]
    paths.extend((root / 'stegoserve_backend',
                  root.parent / 'StegoSplit' / 'releases' / 'authgate_v0_6_deploy'))
    if os.name == 'nt':
        paths.append(Path(r'D:\LOCAL_Share\Code Projects\StegoSplit\releases\authgate_v0_6_deploy'))
    unique=[]
    for path in paths:
        path=path.resolve()
        if path not in unique:unique.append(path)
    return unique


def locate_backend(explicit: str | None = None) -> Path:
    options = [Path(explicit).expanduser().resolve()] if explicit else candidates()
    for path in options:
        if path.is_dir() and all((path / name).is_file() for name in _REQUIRED):
            return path
    raise FileNotFoundError(
        'Stego AuthGate v0.6 backend not found. Unpack the separate ' 
        'Stego-AuthGate-v0.6-Win-x64-Python314-Deploy.zip and set ' 
        'STEGOSERVE_BACKEND_DIR or supply --backend. This Norm source ZIP ' 
        'does not contain that standalone gateway binary release.')


def status(backend_dir: str | None = None) -> dict:
    try:
        root=locate_backend(backend_dir)
    except FileNotFoundError as e:
        return {'ok':False, 'backend_connected':False, 'error':str(e),
                'network_gate_in_norm':False,
                'management_plugin':'stegoserve', 'catalog':128,'company_formula_limit':16}
    formula_binary=root / 'formula_catalog128.cp314-win_amd64.pyd'
    sha=hashlib.sha256(formula_binary.read_bytes()).hexdigest()
    return {'ok':True, 'backend_connected':True, 'backend_dir':str(root),
            'catalog':128,'company_formula_limit':16, 'formula_binary_sha256':sha,
            'network_gate_in_norm':False, 'gateway':'standalone authgate_server.py'}


def call_readonly(script: str, args: list[str], backend_dir: str | None = None,
                  timeout: int = 30) -> dict:
    if script not in ('ss_ctl.py','authgate_server.py'):
        raise ValueError('read-only operation not allowed')
    backend=locate_backend(backend_dir)
    result=subprocess.run([sys.executable, str(backend/script), *args],
        cwd=str(backend), input='', capture_output=True,text=True,timeout=timeout,check=False)
    if result.returncode:
        return {'ok':False,'exit_code':result.returncode,
                'error':result.stderr[-1500:] or result.stdout[-1500:]}
    try:
        decoded=json.loads(result.stdout)
        if isinstance(decoded,dict):
            return {'ok':True, 'data':decoded}
    except json.JSONDecodeError:pass
    return {'ok':True, 'message':result.stdout[-1500:]}


def public_profile(verifier_dir: str, backend_dir: str | None = None) -> dict:
    """Inspect active formula assignments without returning any private company seed."""
    return call_readonly('ss_ctl.py',['profile',str(Path(verifier_dir).resolve())],backend_dir)


def gate_config_preflight(config_file: str, backend_dir: str | None = None) -> dict:
    """Check signed routes and TLS configuration without starting the gateway."""
    return call_readonly('authgate_server.py',
                         ['--config',str(Path(config_file).resolve()),'--check-config'],backend_dir)
