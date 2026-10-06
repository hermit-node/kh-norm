from __future__ import annotations
import argparse, hashlib, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SPEC=ROOT/'Norm-Installer.spec'
EXPECTED='8A23A8A93FD45A3EF72BF3F4E6735A333C3973E76825B2A91597527B7CDA80BE'
def sha256(p: Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest().upper()
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--verify-reference',action='store_true')
    args=ap.parse_args()
    subprocess.run([sys.executable,'-m','PyInstaller','--clean','--noconfirm',str(SPEC)],cwd=ROOT,check=True)
    exe=ROOT/'dist'/'Norm-Installer.exe'
    if not exe.is_file(): raise SystemExit('Build completed but dist/Norm-Installer.exe is missing')
    digest=sha256(exe)
    print(f'EXE={exe}')
    print(f'SHA256={digest}')
    print(f'REFERENCE={EXPECTED}')
    print('REFERENCE_MATCH='+str(digest==EXPECTED))
    if args.verify_reference and digest!=EXPECTED: raise SystemExit(2)
if __name__=='__main__': main()
