"""Offline browser adapter and export regression, no user session/network required."""
from __future__ import annotations
import importlib.util,json,subprocess,sys,tempfile,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'plugins/norm_browser/src/extensions/convo_export/exporter.py'
spec=importlib.util.spec_from_file_location('convo_exporter_test',source)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

with tempfile.TemporaryDirectory(prefix='norm-browser-r11-') as d:
    root=Path(d)
    image=b'\x89PNG\r\n\x1a\n'+b'X'*1024
    def fake_download(url,destination):
        if not mod._safe_host(url):raise ValueError('Outside allowed asset hosts')
        destination.write_bytes(image)
        return len(image)
    mod._download=fake_download
    messages=[{'id':'one','position':0,'role':'user','timestamp':1000000000,
               'text':'Hello \U0001f680 (Unicode)\n\nSample question',
               'attachments':[{'kind':'image','name':'chart','url':'https://files.oaiusercontent.com/chart.png','mime':'image/png'}]},
              {'id':'two','position':1,'role':'assistant','timestamp':1000000100,
               'text':'Answer with *Markdown* and code:\n```python\nprint(1)\n```',
               'attachments':[{'kind':'file','name':'outside','url':'https://example.com/bad.pdf'}]}]
    output=mod.export_conversation(messages,root=root,title='Test Conversation',fmt='zip',
         options={'attachments':True,'timestamps':True,'page_size':'Letter'},
         source_url='https://chatgpt.com/c/00000000-0000-0000-0000-000000000000',complete=True,source='api')
    assert output['message_count']==2,output
    assert output['saved_assets']==1,output
    assert output['missing_assets']==1,output
    with zipfile.ZipFile(output['path']) as z:
        names=set(z.namelist())
        assert {'conversation.md','conversation.json','manifest.json','assets/00001_01.png'} <=names,names
        md=z.read('conversation.md').decode('utf8')
        assert 'Hello \U0001f680' in md
        assert '![chart](assets/00001_01.png)' in md
        assert 'Attachment unavailable: outside' in md
        meta=json.loads(z.read('manifest.json'))
        assert meta['selected_count']==2 and len(meta['missing_assets'])==1
    assert not mod._safe_host('https://127.0.0.1/foo')
    assert not mod._safe_host('http://files.oaiusercontent.com/foo')
    assert not mod._safe_host('https://chatgpt.com.evil.example/foo')
    md=mod.export_conversation(messages[:1],root=root,title='Just one',fmt='md')
    mdtext=Path(md['path']).read_text('utf8')
    assert '_assets/' in mdtext
    json_out=mod.export_conversation(messages[:1],root=root,title='Just one',fmt='json')
    record=json.loads(Path(json_out['path']).read_text('utf8'))
    assert '_assets/' in record['messages'][0]['attachments'][0]['local_path']
    pdf=mod.export_conversation(messages[:1],root=root,title='PDF only',fmt='pdf',
         options={'attachments':False})
    assert Path(pdf['path']).read_bytes().startswith(b'%PDF-')

# Node can validate the JavaScript syntax and simulate the async conversation API.
sys.path.insert(0,str(ROOT/'tools/shared_browser'))
import conversation_js
with tempfile.TemporaryDirectory(prefix='norm-js-r11-') as d:
    temp=Path(d)
    for n in ('START','STATUS','PREVIEW','ITEM','EARLY_AUTH_HOOK'):
        file=temp/(n+'.js');file.write_text(getattr(conversation_js,n),encoding='utf8')
        subprocess.run(['node','--check',str(file)],check=True,capture_output=True)
    test='''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[2],'utf8');
const context={window:{},document:{title:'Test page'},
 location:{hostname:'chatgpt.com',origin:'https://chatgpt.com',
 href:'https://chatgpt.com/c/11111111-1111-1111-1111-111111111111',
 pathname:'/c/11111111-1111-1111-1111-111111111111'},
 fetch:async (u)=>({ok:true,json:async()=>{
   if(u.includes('/api/auth/session'))return {accessToken:'fake'};
   if(u.includes('/files/download/'))return {download_url:'https://files.oaiusercontent.com/file.png'};
   return {title:'Conversation',current_node:'2',mapping:{
     '1':{id:'1',parent:null,message:{id:'u1',author:{role:'user'},
         content:{parts:['Hello 🚀']},metadata:{}}},
     '2':{id:'2',parent:'1',message:{id:'a1',author:{role:'assistant'},
         content:{parts:['Yes',{content_type:'image_asset_pointer',
             asset_pointer:'sediment://file'}]},metadata:{}}}
   }};
 }})};
vm.createContext(context);
vm.runInContext(source,context);
const probe=()=>new Promise(r=>setTimeout(r,10));
(async()=>{for(let i=0;i<100 && context.window.__normConversation.state==='loading';i++) await probe();
 const s=context.window.__normConversation;
 assert.strictEqual(s.state,'ready');assert.strictEqual(s.complete,true);
 assert.strictEqual(s.messages.length,2);assert.strictEqual(s.messages[0].text,'Hello 🚀');
 assert.strictEqual(s.messages[1].attachments[0].url,'https://files.oaiusercontent.com/file.png');
 console.log('PASS conversation adapter API/attachment/Unicode');
})().catch(e=>{console.error(e);process.exitCode=1});'''
    file=temp/'test.js';file.write_text(test,encoding='utf8')
    result=subprocess.run(['node',str(file),str(temp/'START.js')],capture_output=True,text=True)
    print(result.stdout.strip(),result.stderr.strip())
    result.check_returncode()
print('PASS offline ZIP, MD, JSON, PDF, asset manifest, URL safety, JS syntax')
