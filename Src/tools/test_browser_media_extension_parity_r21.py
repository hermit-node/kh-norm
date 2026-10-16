"""Offline reproduction of extension-style authenticated file resolution + local export.
No live tokens, user files, or Internet requests. Tests the actual JS and exporter modules.
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
from conversation_js import START,STATUS

with tempfile.TemporaryDirectory(prefix='norm-r21-parity-') as directory:
    root=Path(directory)
    start=root/'start.js';start.write_text(START,encoding='utf8')
    status=root/'status.js';status.write_text(STATUS,encoding='utf8')
    runner=root/'runner.cjs'
    runner.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const fixed='https://chatgpt.com/g/g-p-proj/c/11111111-1111-1111-1111-111111111111';
let endpoints=[];
const msgs=[
 {id:'m1',author:{role:'user'},content:{content_type:'multimodal_text',parts:['two images',
 {content_type:'image_asset_pointer',asset_pointer:'sediment://file_AAAAAAAA'},
 {content_type:'image_asset_pointer',asset_pointer:'sediment://file_BBBBBBBB'}]},
 metadata:{attachments:[{id:'file_AAAAAAAA',name:'first.jpg',mime_type:'image/jpeg'},
                          {id:'file_BBBBBBBB',name:'second.jpg',mime_type:'image/jpeg'}]}},
 {id:'m2',author:{role:'assistant'},content:{parts:['attached PDF']},
 metadata:{attachments:[{id:'file_PDFXXXXX',name:'document.pdf',mime_type:'application/pdf'}]}},
 {id:'m3',author:{role:'assistant'},content:{parts:[{content_type:'image_v2',images:[
 {content_url:'https://images.openai.com/g/generated-image-1.png'}]}]},metadata:{}}
];
const context={URL,window:{},document:{title:'ChatGPT test'},location:{href:fixed,
 origin:'https://chatgpt.com',hostname:'chatgpt.com',pathname:'/g/g-p-proj/c/11111111-1111-1111-1111-111111111111'},
 fetch:async url=>{
  endpoints.push(String(url));
  if(String(url).includes('/api/auth/session')) return {ok:true,json:async()=>({accessToken:'fake-not-real'})};
  if(String(url).includes('/backend-api/conversations/')) return {ok:false,status:404};
  if(String(url).includes('/backend-api/conversation/'))return {ok:true,json:async()=>({current_node:'m3',title:'Chat history',
    mapping:Object.fromEntries(msgs.map((m,i)=>[m.id,{id:m.id,parent:i?msgs[i-1].id:null,message:m}]))})};
  if(String(url).includes('/backend-api/files/download/')) {
    if(String(url).includes('file_AAAAAAAA'))return {ok:true,json:async()=>({url:'https://files.oaiusercontent.com/first?x=1'})};
    if(String(url).includes('file_BBBBBBBB')&&String(url).includes('conversation_id='))return {ok:false,status:403};
    if(String(url).includes('file_BBBBBBBB')&&String(url).includes('message_id='))return {ok:true,json:async()=>({data:{url:'https://files.oaiusercontent.com/second?x=2'}})};
    if(String(url).includes('file_PDFXXXXX'))return {ok:true,json:async()=>({download_url:'https://files.oaiusercontent.com/pdf?x=3'})};
  }
  throw Error('unexpected '+url)
 }};
vm.createContext(context);
vm.runInContext(source,context);
(async()=>{
 for(let i=0;i<200&&context.window.__normConversation.state==='loading';i++)await new Promise(r=>setTimeout(r,5));
 const snap=context.window.__normConversation;
 assert.equal(snap.state,'ready',snap.error);
 assert.equal(snap.complete,true);
 assert.equal(snap.messages.length,3);
 assert.equal(snap.messages[0].attachments.length,2);
 assert.equal(snap.messages[0].attachments[0].url,'https://files.oaiusercontent.com/first?x=1');
 assert.equal(snap.messages[0].attachments[1].url,'https://files.oaiusercontent.com/second?x=2');
 assert.equal(snap.messages[1].attachments[0].url,'https://files.oaiusercontent.com/pdf?x=3');
 assert.equal(snap.messages[2].attachments[0].url,'https://images.openai.com/g/generated-image-1.png');
 assert.equal(snap.attachmentResolution.expected,3);
 assert.equal(snap.attachmentResolution.resolved,3);
 assert.equal(snap.attachmentResolution.failed,0);
 assert.equal(endpoints.filter(x=>x.includes('/files/download/file_AAAAAAAA')).length,1);
 assert.equal(endpoints.filter(x=>x.includes('/files/download/file_BBBBBBBB')).length,2);
 assert.equal(endpoints.filter(x=>x.includes('/files/download/file_PDFXXXXX')).length,1);
 console.log('PASS extension parity: first party url/download_url, message_id fallback, metadata/pointer dedup, generated image, PDF attachment');
})().catch(e=>{console.error(e);process.exitCode=1});
''',encoding='utf8')
    for path in (start,status):subprocess.run(['node','--check',str(path)],check=True,capture_output=True)
    done=subprocess.run(['node',str(runner),str(start)],capture_output=True,text=True)
    print(done.stdout,done.stderr)
    done.check_returncode()

spec=importlib.util.spec_from_file_location('norm_r21_exporter',ROOT/'plugins/norm_browser/src/extensions/convo_export/exporter.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
# Signed asset link is fetched in Python (extension's background worker analogue),
# not in any 3rd-party PDF conversion process.
assert mod._safe_host('https://images.openai.com/a.png')
assert mod._safe_host('https://files.oaiusercontent.com/a.pdf')
assert not mod._safe_host('https://files.oaiusercontent.com.evil.example/a.png')
assert not mod._safe_host('http://files.oaiusercontent.com/a.png')
print('PASS URL safety: known OpenAI hosts only')
