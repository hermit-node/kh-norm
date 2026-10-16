from pathlib import Path
import ast,hashlib,json,sys,zipfile
root=Path(r'D:\LOCAL_Share\Code Projects\Norm')
src=root/'Norm-0.55.8r10-STAGING'
old=root/'Norm-0.55.7-CLEAN-RELEASE'/'Norm-0.55.8r9-N2-restored-portable-source.zip'
out=root/'Norm-0.55.8r10c-portable-source.zip'
if out.exists():
    raise SystemExit(f'Refusing to overwrite existing release: {out}')
bad=[]
for p in sorted(src.rglob('*.py')):
    try:ast.parse(p.read_text(encoding='utf8'),filename=str(p))
    except (SyntaxError,UnicodeError) as e:bad.append(f'{p.relative_to(src)}: {e}')
if bad:raise SystemExit('Syntax validation failed:\n'+'\n'.join(bad[:15]))
print('SYNTAX: every staged Python file parsed')
ignored={'__pycache__','.git','.venv','.pytest_cache'}
files=sorted(p for p in src.rglob('*') if p.is_file()
    and not any(part in ignored for part in p.relative_to(src).parts)
    and p.name not in {'source-content-hashes.json'}
    and not p.name.startswith('_apply_') and not p.name.startswith('_r10_test_'))
for p in files:
    relative=p.relative_to(src).as_posix()
    if any(part.lower() in {'state','secrets','file-backups','deletion-trash'} for part in p.relative_to(src).parts):
        raise SystemExit('Unexpected private/generated directory: '+relative)
    if p.suffix.lower() in {'.env','.key','.pem'}:
        raise SystemExit('Unexpected secret-type file in portable source: '+relative)
compiled={}
with zipfile.ZipFile(old) as z:
    originals={n:z.read(n) for n in z.namelist()
         if n.lower().endswith(('.pyd','.dll','.so')) or 'stegosplit' in n.lower() and n.lower().endswith(('.bin','.dat'))}
for name,body in originals.items():
    p=src/name
    if not p.is_file() or p.read_bytes()!=body:
        raise SystemExit('Protected binary changed or missing: '+name)
print('PROTECTED PLUGIN PAYLOADS:',len(originals),'binary/hash-sensitive files unchanged from r9')
manifest={
    'schema':1,'release':'0.55.8r10c',
    'baseline':'Norm-0.55.8r9-N2-restored-portable-source.zip',
    'file_count':len(files),
    'hash_algorithm':'SHA-256',
    'files':{p.relative_to(src).as_posix():{
        'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}
}
hashfile=src/'source-content-hashes.json'
hashfile.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
files.append(hashfile)
files.sort()
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7,strict_timestamps=False) as z:
    for p in files:z.write(p,p.relative_to(src).as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
checksum=out.with_name(out.name+'.sha256')
checksum.write_text(f'{sha}  {out.name}\n',encoding='utf8')
with zipfile.ZipFile(out) as z:
    badmember=z.testzip()
    assert badmember is None,badmember
    for name,info in manifest['files'].items():
        assert hashlib.sha256(z.read(name)).hexdigest()==info['sha256'],name
    print('ZIP VERIFIED',len(z.namelist()),'entries')
print('RELEASE',out)
print('SHA256',sha)
print('BYTES',out.stat().st_size)
print('CHECKSUM',checksum)