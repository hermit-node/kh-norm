"""Page-side ChatGPT exporter adapter. No external services or token exposure.

The JavaScript starts a bounded, asynchronous API-first snapshot in MainWorld.
Python accesses only sanitized snapshots in small chunks via the existing RPC.
The undocumented ChatGPT endpoints may change; DOM fallback is labelled partial.
"""
from __future__ import annotations

START = r'''(() => {
  if (!['chatgpt.com','chat.openai.com'].includes(location.hostname))
    return {started:false,error:'Not a ChatGPT page'};
  if (window.__normConversation?.state === 'loading')
    return {started:false,error:'Extraction already running'};
  const snap = {state:'loading',source:null, complete:false, messages:[],error:null,
                url:location.href,title:document.title,startedAt:Date.now()};
  window.__normConversation = snap;
  const asText = x => typeof x === 'string' ? x : '';
  const formatPart = part => {
    if (typeof part === 'string') return part;
    if (!part || typeof part !== 'object') return '';
    if (part.content_type === 'image_asset_pointer') return '[Image]';
    if (typeof part.text === 'string') return part.text;
    if (typeof part.content === 'string') return part.content;
    return '';
  };
  const toMsg = (node, idx) => {
    const m=node.message || {}, meta=m.metadata || {}, body=m.content || {};
    const rawParts=Array.isArray(body.parts) ? body.parts : [];
    const attachments=[];
    for (const a of Array.isArray(meta.attachments)?meta.attachments:[]) {
      if (!a || typeof a !== 'object') continue;
      attachments.push({kind:(a.mime_type||'').startsWith('image/')?'image':'file',
        name:a.name||a.file_name||'attachment',id:a.id||a.file_id||null,
        url:a.download_url||a.url||null,mime:a.mime_type||null});
    }
    for (const p of rawParts) {
      if (p && typeof p==='object' && p.content_type==='image_asset_pointer') {
        attachments.push({kind:'image',name:'Image',id:asText(p.asset_pointer).replace(/^sediment:\/\//,''),
          url:p.download_url||null,mime:'image/png'});
      }
    }
    const text=rawParts.map(formatPart).filter(Boolean).join('\n\n') ||
      (typeof body.text==='string'?body.text : typeof body.content==='string'?body.content:'');
    return {id:m.id||node.id||String(idx),role:(m.author||{}).role||'unknown',
      model:meta.model_slug||meta.resolved_model_slug||'',
      timestamp:m.create_time||null,text,attachments,
      content_type:body.content_type||'text',position:idx};
  };
  const domFallback = () => {
    const elements=Array.from(document.querySelectorAll('[data-message-author-role]'));
    snap.messages=elements.map((el,i)=>({id:el.getAttribute('data-message-id')||'dom-'+i,
      role:el.getAttribute('data-message-author-role')||'unknown',position:i,
      text:el.innerText||el.textContent||'',model:'',timestamp:null,
      attachments:Array.from(el.querySelectorAll('img')).map((img,j)=>({kind:'image',
        name:img.alt||'Image '+(j+1),url:img.currentSrc||img.src||null,id:null,mime:null})),
      content_type:'dom'}));
    snap.source='dom';snap.complete=false;snap.state='ready';
  };
  (async()=>{
    try {
      const route=location.pathname.match(/^\/c\/([0-9a-f-]{36})\/?$/i);
      if(!route) {domFallback();return;}
      const id=route[1];
      let token=asText(window.__normChatAuth);
      try {
        const session=await fetch('/api/auth/session',{credentials:'same-origin'});
        if(session.ok) token=asText((await session.json()).accessToken)||token;
      } catch(_) {}
      const headers=token?{Authorization:'Bearer '+token}:{};
      const resp=await fetch('/backend-api/conversation/'+encodeURIComponent(id),
        {credentials:'same-origin',headers});
      if(!resp.ok) throw Error('Conversation API HTTP '+resp.status);
      const data=await resp.json();
      if(!data || !data.mapping || typeof data.mapping!=='object')
        throw Error('Conversation graph unavailable');
      let node=data.current_node; const chain=[],visited=new Set();
      while(node && data.mapping[node] && !visited.has(node) && chain.length<30000) {
        visited.add(node);chain.push(data.mapping[node]);node=data.mapping[node].parent;
      }
      if(!chain.length) throw Error('Conversation branch empty');
      chain.reverse();
      snap.messages=chain.filter(n=>n.message && n.message.author &&
        ['user','assistant'].includes(n.message.author.role))
        .map((n,i)=>toMsg(n,i));
      // Resolve file IDs through the same authenticated session. URL values are
      // handed to Python only; access tokens never leave the web renderer.
      const assets=snap.messages.flatMap(m=>m.attachments).filter(a=>a.id&&!a.url).slice(0,1500);
      for(let start=0; start<assets.length;start+=6){
        await Promise.all(assets.slice(start,start+6).map(async a=>{
          try {
            const fileId=String(a.id).replace(/^sediment:\/\//,'').split('?')[0];
            const req=await fetch('/backend-api/files/download/'+encodeURIComponent(fileId)+
              '?conversation_id='+encodeURIComponent(id),{headers,credentials:'same-origin'});
            if(req.ok){const info=await req.json();if(typeof info.download_url==='string')
              a.url=info.download_url;}
          }catch(_){}
        }));
      }
      snap.title=data.title||document.title;
      snap.source='api';snap.complete=!node || !data.mapping[node];snap.state='ready';
      if(!snap.messages.length) throw Error('No user/assistant messages in graph');
    } catch(e) {
      snap.error=String(e);
      try {domFallback();} catch(f) {snap.state='error';snap.error+='; DOM: '+String(f);}
    }
  })();
  return {started:true};
})()'''

STATUS = r'''(() => {
  const s=window.__normConversation;
  if(!s)return {state:'none'};
  return {state:s.state,source:s.source,complete:s.complete,error:s.error,
    title:s.title,url:s.url,count:s.messages.length};
})()'''

ITEM = r'''((index,start,length) => {
  const s=window.__normConversation;
  if(!s||s.state!=='ready')return {error:'No prepared conversation'};
  const m=s.messages[index];if(!m)return {error:'Invalid message index'};
  const codepoints=Array.from(m.text);
  return {index,id:m.id,role:m.role,model:m.model,timestamp:m.timestamp,
    content_type:m.content_type,text_length:codepoints.length,
    text:codepoints.slice(start,start+length).join(''),attachments:m.attachments};
})'''

PREVIEW = r'''((start,count) => {
  const s=window.__normConversation;if(!s||s.state!=='ready')return {state:s?.state||'none',messages:[]};
  return {state:s.state,total:s.messages.length,start,messages:s.messages.slice(start,start+count).map((m,i)=>({
    index:start+i,id:m.id,role:m.role,model:m.model,timestamp:m.timestamp,
    preview:m.text.slice(0,210),length:m.text.length,attachment_count:m.attachments.length,
    has_code:m.text.includes("```")}))};
})'''

# Runs before ChatGPT initializes on future page loads. Only captures auth
# already used by this site; nothing is sent to Python or external services.
EARLY_AUTH_HOOK = r'''(() => {
  if (!['https://chatgpt.com','https://chat.openai.com'].includes(location.origin)) return;
  if (window.__normAuthObserved) return;
  window.__normAuthObserved = true;
  const original = window.fetch;
  if (typeof original !== 'function') return;
  window.fetch = function(resource,init) {
    try {
      const uri = typeof resource==='string' ? resource : resource?.url || '';
      const url = new URL(uri,location.href);
      if (url.origin===location.origin && url.pathname.startsWith('/backend-api/')) {
        const headers = init?.headers || (resource instanceof Request ? resource.headers : null);
        let bearer='';
        if (headers instanceof Headers) bearer=headers.get('Authorization')||'';
        else if (Array.isArray(headers)) bearer=(headers.find(([k])=>k.toLowerCase()==='authorization')||[])[1]||'';
        else if (headers) bearer=headers.Authorization || headers.authorization || '';
        if (bearer.startsWith('Bearer ')) window.__normChatAuth=bearer.slice(7);
      }
    } catch (_) {}
    return original.apply(this,arguments);
  };
})()'''
