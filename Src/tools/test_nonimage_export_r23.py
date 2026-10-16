"""Offline regression: document pointers and honest generated-file coverage.

No ChatGPT credentials, live network, or running Norm changes.
"""
from __future__ import annotations
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/shared_browser'))
from conversation_js import START

with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    (root/'start.js').write_text(START,encoding='utf8')
    runner=r'''
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const id='11111111-1111-1111-1111-111111111111';
const msgs=[
 {id:'m1',author:{role:'user'},content:{parts:['Inspect these files',
  {content_type:'file_asset_pointer',asset_pointer:'sediment://file_DOCUMENT1',filename:'example.zip',mime_type:'application/zip'},
  {content_type:'file',file:{file_id:'file_DOCFILE2',name:'data.csv',mime_type:'text/csv'}}]},
  metadata:{attachments:[{id:'attachment-placeholder',file_id:'file_DOCUMENT1',name:'example.zip',mime_type:'application/zip'}]}},
 {id:'m2',author:{role:'assistant'},content:{parts:['Download [report](sandbox:/mnt/data/report.docx)']},metadata:{}}
];
let fetched=[];
const context={URL,window:{},document:{title:'Test'},location:{href:'https://chatgpt.com/c/'+id,
 hostname:'chatgpt.com',origin:'https://chatgpt.com',pathname:'/c/'+id},fetch:async u=>{
 fetched.push(String(u));
 if(String(u).includes('/api/auth/session')) return {ok:true,json:async()=>({accessToken:'dummy'})};
 if(String(u).includes('/backend-api/conversations/')) return {ok:false,status:404};
 if(String(u).includes('/backend-api/conversation/')) return {ok:true,json:async()=>({current_node:'m2',mapping:Object.fromEntries(msgs.map((m,i)=>[m.id,{id:m.id,parent:i?msgs[i-1].id:null,message:m}]))})};
 if(String(u).includes('/backend-api/files/download/')) return {ok:true,json:async()=>({download_url:'https://files.oaiusercontent.com/assets/binary?token=x'})};
 throw Error('unexpected '+u);
}};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
 for(let i=0;i<200&&context.window.__normConversation.state==='loading';i++)await new Promise(r=>setTimeout(r,5));
 const snap=context.window.__normConversation;
 assert.equal(snap.state,'ready',snap.error);
 assert.equal(snap.messages[0].attachments.length,2,'metadata + part dedup');
 assert(snap.messages[0].attachments.some(x=>x.id==='file_DOCUMENT1'&&x.kind==='file'));
 assert(snap.messages[0].attachments.some(x=>x.id==='file_DOCFILE2'&&x.name==='data.csv'));
 assert.equal(snap.messages[1].attachments[0].name,'report.docx');
 assert.equal(snap.messages[1].attachments[0].url,null,'sandbox link cannot invent a URL');
 assert.equal(snap.attachmentResolution.expected,3);
 assert.equal(snap.attachmentResolution.resolved,2);
 assert.equal(snap.attachmentResolution.failed,1);
 assert(fetched.filter(x=>x.includes('/backend-api/files/download/')).every(x=>x.includes('inline=false')));
 console.log('PASS JS file references, duplicate IDs, sandbox visibility, inline=false');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    (root/'runner.cjs').write_text(runner,encoding='utf8')
    run=subprocess.run(['node',str(root/'runner.cjs'),str(root/'start.js')],capture_output=True,text=True)
    print(run.stdout,run.stderr)
    run.check_returncode()

spec=importlib.util.spec_from_file_location('exporter',ROOT/'plugins/norm_browser/src/extensions/convo_export/exporter.py')
exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
assert exporter.MAX_ASSET>=25*1024*1024
assert exporter.MAX_IMAGE_ASSET==24*1024*1024
assert exporter._asset_suffix(b'PK\x03\x04','application/octet-stream','https://files.oaiusercontent.com/noext','report.zip')=='.zip'
assert not exporter._safe_host('https://files.oaiusercontent.com.attacker.net/file.zip')

# Two distinct references with their original extension. Binary content
# is real and verified in the ZIP; a second reference is deduplicated.
msg=[{'role':'assistant','id':'1','text':'Files attached','attachments':[
    {'kind':'file','id':'file_ZIP1','name':'large-archive.zip','mime':'application/octet-stream','url':'https://files.oaiusercontent.com/file_ZIP1'},
    {'kind':'file','id':'file_ZIP1','name':'large-archive.zip','mime':'application/octet-stream','url':'https://files.oaiusercontent.com/file_ZIP1'},
    {'kind':'file','id':'file_CSV2','name':'prices.csv','mime':'text/csv','url':'https://files.oaiusercontent.com/file_CSV2'}]}]

def fake_download(url,target):
    if 'ZIP1' in url:
        with target.open('wb') as f:
            f.write(b'PK\x03\x04')
            # 25MiB non-image is above old 24MiB guard.
            for _ in range(25):f.write(b'Z'*1024*1024)
    else:target.write_bytes(b'date,price\n2026-10-16,1.25\n')
    return target.stat().st_size

exporter._download=fake_download
with tempfile.TemporaryDirectory() as td:
    z=exporter.export_conversation(msg,root=Path(td),title='Docs',fmt='zip',options={'attachments':True},complete=True)
    assert z['saved_assets']==2 and z['expected_assets']==2 and z['assets_complete'],z
    assert z['nonimage_assets_saved']==2 and z['nonimage_assets_expected']==2,z
    with zipfile.ZipFile(z['path']) as archive:
        files=[x for x in archive.namelist() if x.startswith('assets/')]
        assert len(files)==2,files
        assert any(x.endswith('_large-archive.zip') for x in files),files
        assert any(x.endswith('_prices.csv') for x in files),files
        info=json.loads(archive.read('manifest.json'))
        assert info['assets_complete'] and info['nonimage_assets_saved']==2
        assert all(a['kind']=='file' for a in info['saved_assets'])
        assert any(archive.getinfo(x).file_size>24*1024*1024 for x in files)
    # PDF cannot render a ZIP/CSV as text, but the originals are retained.
    pdf=exporter.export_conversation(msg,root=Path(td),title='Docs',fmt='pdf',options={'attachments':True})
    folder=Path(td)/pdf['pdf_nonimage_sidecar']
    assert Path(pdf['path']).is_file() and folder.is_dir() and len(list(folder.glob('*.zip')))==1
    assert len(list(folder.glob('*.csv')))==1
    assert (folder/'manifest.json').is_file()
    # Structured failures must not be counted as successful assets.
    try:
        exporter.export_conversation([{'role':'assistant','text':'[doc](sandbox:/mnt/data/doc.zip)',
            'attachments':[{'kind':'file','name':'doc.zip','resolution_error':'No file ID'}]}],
            root=Path(td),title='Missing',fmt='zip',allow_incomplete_assets=False)
    except RuntimeError as exc:assert 'No file ID' in str(exc)
    else:raise AssertionError('Silent export of missing assistant file')
    def bad_download(url,target):target.write_bytes(b'<html><title>Login</title></html>');return target.stat().st_size
    exporter._download=bad_download
    try:exporter.export_conversation(msg,root=Path(td),title='Corrupt',fmt='zip',allow_incomplete_assets=False)
    except RuntimeError as exc:assert 'HTML/JSON error' in str(exc)
    else:raise AssertionError('Saved HTML login as ZIP')
    print('PASS large ZIP and CSV bytes, dedup, PDF sidecar, missing-file refusal, reject HTML')

# A mislabeled image is still subject to the image cap, and strict exports
# must refuse to claim complete coverage when an image is oversized.
def large_image(url,target):
    with target.open('wb') as stream:
        stream.write(b'\x89PNG\r\n\x1a\n')
        stream.write(b'I'*(24*1024*1024 + 1 - 8))
    return target.stat().st_size
exporter._download=large_image
with tempfile.TemporaryDirectory() as td:
    a=[{'role':'assistant','text':'large file','attachments':[{'kind':'file','id':'file_GIANT','name':'large.png',
       'mime':'application/octet-stream','url':'https://files.oaiusercontent.com/image'}]}]
    try:exporter.export_conversation(a,root=Path(td),title='Giant',fmt='zip')
    except RuntimeError as exc:assert 'Image exceeds 24 MiB' in str(exc),str(exc)
    else:raise AssertionError('Oversized, mislabeled image bypassed safety cap')
    assert not list(Path(td).glob('*.zip')),'Strict failure must not publish a partial ZIP'
    report=exporter.export_conversation(a,root=Path(td),title='Giant',fmt='zip',allow_incomplete_assets=True)
    assert report['missing_assets']==1 and not report['assets_complete']
    with zipfile.ZipFile(report['path']) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert 'Image exceeds 24 MiB' in manifest['missing_assets'][0]['reason']
    print('PASS oversized image strict refusal / incomplete manifest / no source mutation')
