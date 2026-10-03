from pathlib import Path
import base64, hashlib, json, shutil, subprocess, textwrap
repo=Path(r'D:\\LOCAL_Share\\Code Projects\\Norm.temp\\github-public-worktree')
out=Path(r'D:\\LOCAL_Share\\Code Projects\\Norm.temp\\github-push-payload-c221507')
shutil.rmtree(out,ignore_errors=True); out.mkdir(parents=True)
def git(*args, text=True):
    return subprocess.check_output(['git','-C',str(repo),*args], text=text)
parent=git('rev-parse','HEAD^').strip()
parent_tree=git('show','-s','--format=%T','HEAD^').strip()
head=git('rev-parse','HEAD').strip()
head_tree=git('show','-s','--format=%T','HEAD').strip()
message=git('log','-1','--format=%B').strip()
raw=git('diff-tree','--no-commit-id','--name-status','-r','-M','HEAD^','HEAD')
entries=[]; counter=0
def add_blob(path):
    global counter
    counter+=1
    ls=git('ls-tree','HEAD','--',path).strip()
    mode,typ,blob_and_path=ls.split(None,2)
    blob_sha=blob_and_path.split('\t',1)[0]
    data=subprocess.check_output(['git','-C',str(repo),'cat-file','blob',f'HEAD:{path}'])
    b64=base64.b64encode(data).decode('ascii')
    wrapped='\n'.join(textwrap.wrap(b64,4096))+'\n'
    fn=f'{counter:04d}.b64'; (out/fn).write_text(wrapped,encoding='ascii',newline='\n')
    entries.append({'op':'blob','path':path,'mode':mode,'blob_sha':blob_sha,'file':fn,'b64_chars':len(b64),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'lines':(len(b64)+4095)//4096})
for line in raw.splitlines():
    parts=line.split('\t'); status=parts[0]
    if status.startswith('R'):
        old,new=parts[1],parts[2]; entries.append({'op':'delete','path':old}); add_blob(new)
    elif status.startswith('D'): entries.append({'op':'delete','path':parts[1]})
    else: add_blob(parts[1])
index={'parent':parent,'parent_tree':parent_tree,'head':head,'head_tree':head_tree,'message':message,'entries':entries}
(out/'index.json').write_text(json.dumps(index,indent=2),encoding='utf-8',newline='\n')
print(json.dumps({'parent':parent,'parent_tree':parent_tree,'head':head,'head_tree':head_tree,'entries':len(entries),'blobs':sum(e['op']=='blob' for e in entries),'deletes':sum(e['op']=='delete' for e in entries),'payload_bytes':sum((out/e['file']).stat().st_size for e in entries if e['op']=='blob')}))
