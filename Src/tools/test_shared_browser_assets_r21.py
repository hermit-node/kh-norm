"""Offline, no auth/network: signed URL resolver, ZIP/PDF fidelity, missing-file guard."""
from __future__ import annotations
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/shared_browser'))
import conversation_js as js

# Mock the *actual* API response shape {url:...}, 531 messages and duplicate image pointers.
with tempfile.TemporaryDirectory(prefix='norm-r21-js-') as d:
    temp=Path(d)
    for name in ('START','STATUS','ITEM','PREVIEW','ASSET_START','ASSET_STATUS','ASSET_CHUNK','ASSET_DROP'):
        p=temp/(name+'.js');p.write_text(getattr(js,name),encoding='utf8')
        subprocess.run(['node','--check',str(p)],check=True,capture_output=True)
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const SRC=fs.readFileSync(process.argv[2],'utf8');
const apiCalls=[];const png=Uint8Array.from([137,80,78,71,13,10,26,10,2,3,4]);
const messages=[];
for(let i=0;i<531;i++){
 const meta=i%14===0&&i/14<38?{attachments:[{id:'file_'+String(i).padStart(8,'0'),mime_type:'image/png',name:'chart'+i+'.png'}]}:{};
 messages.push({id:'m'+i,author:{role:i%2?'assistant':'user'},content:{parts:['Message '+i,...(meta.attachments?[{content_type:'image_asset_pointer',asset_pointer:'sediment://file_'+String(i).padStart(8,'0')}]:[])]},metadata:meta});
}
let resCount=0;
const url='https://chatgpt.com/c/11111111-1111-1111-1111-111111111111';
const context={URL,Uint8Array,window:{},document:{title:'Test'},location:{href:url,origin:'https://chatgpt.com',hostname:'chatgpt.com',pathname:'/c/11111111-1111-1111-1111-111111111111'},btoa:s=>Buffer.from(s,'binary').toString('base64'),
 fetch:async(u)=>{apiCalls.push(String(u));if(String(u).includes('/api/auth/session'))return {ok:true,json:async()=>({accessToken:'faux'})};
 if(String(u).includes('/files/download/')){resCount++;return {ok:true,json:async()=>({url:'https://files.oaiusercontent.com/a.png?token=signed'})}};
 if(String(u).startsWith('https://files.oaiusercontent.com/'))return {ok:true,headers:{get:()=>null},arrayBuffer:async()=>png.buffer};
 return {ok:true,json:async()=>({title:'Test',current_node:'m530',mapping:Object.fromEntries(messages.map((m,i)=>[m.id,{id:m.id,parent:i?'m'+(i-1):null,message:m}]))})};}};
vm.createContext(context);vm.runInContext(SRC,context);
const wait=()=>new Promise(r=>setTimeout(r,10));
(async()=>{
 for(let k=0;k<300&&context.window.__normConversation.state==='loading';k++)await wait();
 const snap=context.window.__normConversation;
 assert.equal(snap.state,'ready',snap.error);assert.equal(snap.messages.length,531);
 const refs=snap.messages.flatMap(m=>m.attachments);
 assert.equal(refs.length,38);assert.equal(resCount,38);
 assert.equal(refs.filter(a=>!!a.url).length,38);
 assert(refs.every(a=>a.url==='https://files.oaiusercontent.com/a.png?token=signed'));
 assert.equal(snap.attachmentResolution.resolved,38);
 const st=vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
 assert.equal(st.attachment_resolution.resolved,38);
 const load=fs.readFileSync(process.argv[4],'utf8');
 assert(vm.runInContext(load+'("https://bad.example/x")',context).ok===false);
 assert(vm.runInContext(load+'("https://files.oaiusercontent.com/a.png")',context).ok===true);
 for(let k=0;k<30&&context.window.__normAssetTransfer.state==='loading';k++)await wait();
 assert.equal(context.window.__normAssetTransfer.state,'ready');
 const chunk=vm.runInContext(fs.readFileSync(process.argv[5],'utf8')+'(0,24576)',context);
 assert.equal(Buffer.from(chunk.base64,'base64').length,png.length);
 assert.equal(chunk.length,png.length);
 vm.runInContext(fs.readFileSync(process.argv[6],'utf8'),context);
 assert.equal(context.window.__normAssetTransfer,null);
 console.log('PASS JS 531 messages, 38 unique images, info.url, browser-transfer chunks');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    runner=temp/'runner.js';runner.write_text(script)
    run=subprocess.run(['node',str(runner),str(temp/'START.js'),str(temp/'STATUS.js'),
                       str(temp/'ASSET_START.js'),str(temp/'ASSET_CHUNK.js'),str(temp/'ASSET_DROP.js')],
                       capture_output=True,text=True)
    print(run.stdout,run.stderr)
    run.check_returncode()

spec=importlib.util.spec_from_file_location('norm_export',ROOT/'plugins/norm_browser/src/extensions/convo_export/exporter.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
from PIL import Image, ImageDraw
import fitz

imgs={}
for i in range(38):
    im=Image.new('RGB',(170,100),(180+i,160+i//2,220-i//2))
    ImageDraw.Draw(im).text((8,24),f'Test chart {i}',fill=(0,0,0))
    b=io.BytesIO();im.save(b,format='JPEG');imgs[f'file_{i:08d}']=b.getvalue()

messages=[]
for i in range(531):
    entry={'id':f'm{i}','role':'user' if i%2==0 else 'assistant','text':f'Synthetic message {i} test', 'attachments':[]}
    if i%14==0 and i//14<38:
        key=f'file_{i//14:08d}'
        entry['attachments']=[{'kind':'file','id':key,'name':f'chart_{i}.jpg','mime':'image/jpeg','url':f'https://files.oaiusercontent.com/{key}?signed=1'},
                              {'kind':'image','id':f'sediment://{key}','name':'Image','mime':'image/png','url':None}]
    messages.append(entry)

def fake_download(url,destination):
    ident=re.search(r'file_\d+',url).group(0)
    destination.write_bytes(imgs[ident]);return len(imgs[ident])

with tempfile.TemporaryDirectory(prefix='norm-r21-zip-') as d:
    mod._download=fake_download
    z=mod.export_conversation(messages,root=Path(d),title='Test images',fmt='zip',
                              options={'attachments':True},allow_incomplete_assets=False,complete=True,source='api')
    assert z['saved_assets']==38 and z['expected_assets']==38 and z['assets_complete'],z
    with zipfile.ZipFile(z['path']) as archive:
        files=[n for n in archive.namelist() if n.startswith('assets/')]
        assert len(files)==38,files
        assert all(n.endswith('.jpg') for n in files),files
        md=archive.read('conversation.md').decode()
        assert md.count('](assets/')==38
        info=json.loads(archive.read('manifest.json'))
        assert info['duplicate_references_removed']==38
        assert info['history_complete'] and info['assets_complete']
        saved=json.loads(archive.read('conversation.json'))
        assert all('url' not in a for m in saved['messages'] for a in m['attachments'])
    p=mod.export_conversation(messages,root=Path(d),title='Test PDF',fmt='pdf',
                              options={'attachments':True},allow_incomplete_assets=False)
    with fitz.open(p['path']) as pdf:
        n_img=sum(len(page.get_images(full=True)) for page in pdf)
        assert n_img==38,(len(pdf),n_img)
    # Browser-authenticated fallback after unauthenticated Python HTTPS fails.
    def direct_fail(url,destination):raise PermissionError('direct signed link denied')
    def browser_fetch(url,destination):return fake_download(url,destination)
    mod._download=direct_fail
    b=mod.export_conversation(messages[:1],root=Path(d),title='Fallback',fmt='zip',
                              options={'attachments':True},asset_fetcher=browser_fetch,
                              allow_incomplete_assets=False)
    assert b['saved_assets']==1 and b['missing_assets']==0,b
    # Strict mode prevents a text-only ZIP from being labeled complete.
    try:
        mod.export_conversation(messages[:1],root=Path(d),title='Missing test',fmt='zip',
                                options={'attachments':True},allow_incomplete_assets=False)
    except RuntimeError as e:
        assert 'could not be included' in str(e)
    else:raise AssertionError('Missing attachment should block export')
    incomplete=mod.export_conversation(messages[:1],root=Path(d),title='Partial',fmt='zip',
                    options={'attachments':True},allow_incomplete_assets=True)
    assert incomplete['assets_complete'] is False and incomplete['missing_assets']==1
    print('PASS ZIP 531/38 JPG assets, 38 images in PDF, browser fallback, strict/partial coverage')
