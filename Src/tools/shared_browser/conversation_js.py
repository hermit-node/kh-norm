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
  const snap = {state:'loading',source:null,complete:false,messages:[],error:null,
                url:location.href,title:document.title,startedAt:Date.now(),pages:0};
  window.__normConversation = snap;
  const text = x => typeof x === 'string' ? x : '';
  const formatPart = p => {
    if (typeof p === 'string') return p;
    if (!p || typeof p !== 'object') return '';
    if (p.content_type === 'image_asset_pointer') return '[Image]';
    if (typeof p.text === 'string') return p.text;
    if (typeof p.content === 'string') return p.content;
    return '';
  };
  const toMsg = (m,idx,nodeId) => {
    m=m||{};
    const meta=m.metadata||{}, body=m.content||{}, parts=Array.isArray(body.parts)?body.parts:[];
    const attachments=[];
    for (const a of Array.isArray(meta.attachments)?meta.attachments:[]) {
      if (!a||typeof a!=='object') continue;
      attachments.push({kind:(a.mime_type||'').startsWith('image/')?'image':'file',
        name:a.name||a.file_name||'attachment',id:a.id||a.file_id||null,
        url:a.download_url||a.url||null,mime:a.mime_type||null});
    }
    for (const p of parts) {
      if (p&&typeof p==='object'&&p.content_type==='image_asset_pointer') {
        attachments.push({kind:'image',name:'Image',id:text(p.asset_pointer).replace(/^sediment:\/\//,''),
          url:p.download_url||null,mime:'image/png'});
      }
    }
    let content=parts.map(formatPart).filter(Boolean).join('\n\n');
    if (!content) content=text(body.text)||text(body.content)||text(body.result)||text(body.summary);
    return {id:m.id||nodeId||String(idx),role:(m.author||{}).role||'unknown',
      model:meta.model_slug||meta.resolved_model_slug||'',timestamp:m.create_time||null,
      text:content,attachments,content_type:body.content_type||'text',position:idx};
  };
  const domFallback = () => {
    // ChatGPT can render both traditional message nodes and turn-based layouts.
    // DOM reads are never treated as proof of complete conversation coverage.
    const selectors=[
      '[data-message-author-role]',
      'section[data-turn-id]',
      '[data-testid^="conversation-turn-"]',
      'main article',
      'main [data-message-id]'
    ];
    let nodes=[],selector='';
    for (const candidate of selectors) {
      const found=Array.from(document.querySelectorAll(candidate));
      const usable=found.filter(el=>(el.innerText||el.textContent||'').trim()||el.querySelector('img,canvas'));
      if (usable.length>nodes.length) {nodes=usable;selector=candidate;}
      if (usable.length>=2&&candidate==='[data-message-author-role]') break;
    }
    if (!nodes.length) throw Error('No conversation turns in the loaded DOM; check page state or authentication');
    snap.messages=nodes.map((el,i)=>{
      const tagged=el.matches('[data-message-author-role]')?el:el.querySelector('[data-message-author-role]');
      let role=(tagged?.getAttribute('data-message-author-role')||el.getAttribute('data-turn')||
        el.getAttribute('data-role')||'').toLowerCase();
      if (!['user','assistant'].includes(role)) role='unknown';
      return {id:el.getAttribute('data-message-id')||el.getAttribute('data-turn-id')||'dom-'+i,
        role,position:i,text:el.innerText||el.textContent||'',model:'',timestamp:null,
        attachments:Array.from(el.querySelectorAll('img')).map((img,j)=>({kind:'image',
          name:img.alt||'Image '+(j+1),url:img.currentSrc||img.src||null,id:null,mime:null})),
        content_type:'dom'};
    });
    snap.source='dom ('+selector+')';snap.complete=false;snap.state='ready';
  };
  const live = () => location.href===snap.url && window.__normConversation===snap;
  const getJSON = async (url,headers) => {
    const resp=await fetch(url,{credentials:'same-origin',headers});
    if (!resp.ok) throw Error(url.split('?')[0]+': HTTP '+resp.status);
    return resp.json();
  };
  (async()=>{
    try {
      const match=location.pathname.match(/(?:^|\/)c\/([0-9a-f-]{36})(?:\/|$)/i);
      if (!match) {domFallback();return;}
      const id=match[1];
      let auth=text(window.__normChatAuth);
      if (!auth) {
        try {
          const session=await fetch('/api/auth/session',{credentials:'same-origin'});
          if(session.ok) auth=text((await session.json()).accessToken)||auth;
        } catch (_) {}
      }
      const headers=auth?{Authorization:'Bearer '+auth}:{};
      let result=null, flatError=null;
      try {
        // Current ChatGPT layout: selected conversation branch paginated in 100-turn batches.
        const url='/backend-api/conversations/'+encodeURIComponent(id)+
          '?include_has_versions=true&num_turns=100';
        const chunks=[],seenCursors=new Set();
        let before='',pageCount=0;
        for (;;) {
          if (!live()) return;
          if (++pageCount>800) throw Error('Pagination safety limit (800 pages) reached');
          const page=await getJSON(url+(before?'&before='+encodeURIComponent(before):''),headers);
          if (!Array.isArray(page.messages)) throw Error('Paginated messages array unavailable');
          chunks.unshift(page.messages);
          snap.pages=pageCount;
          const pagination=page.page_info||{};
          if (typeof pagination.has_previous_page!=='boolean')
            throw Error('Paginated response has no reliable coverage metadata');
          if (!pagination.has_previous_page) {
            const all=chunks.flat();const unique=new Set();
            const parsed=[];
            for (const message of all) {
              const m=message?.message||message;
              if (!m || !['user','assistant'].includes(m.author?.role)) continue;
              const key=m.id||message.id;
              if (key && unique.has(key)) continue;
              if (key) unique.add(key);
              parsed.push(toMsg(m,parsed.length,message.id));
            }
            if (!parsed.length) throw Error('Paginated API returned no user/assistant turns');
            result={source:'api-paginated',messages:parsed,title:page.title,complete:true};
            break;
          }
          const next=text(pagination.start_cursor);
          if (!next||seenCursors.has(next)) throw Error('Pagination stalled or missing start_cursor');
          seenCursors.add(next);before=next;
        }
      } catch (e) {flatError=String(e);}
      if (!result) {
        try {
          // Legacy mapping endpoint remains available on some accounts.
          const data=await getJSON('/backend-api/conversation/'+encodeURIComponent(id),headers);
          if (!data?.mapping || typeof data.mapping!=='object')
            throw Error('Legacy conversation graph unavailable');
          let node=data.current_node;
          const chain=[],visited=new Set();
          while(node&&data.mapping[node]&&!visited.has(node)&&chain.length<30000){
            visited.add(node);chain.push(data.mapping[node]);node=data.mapping[node].parent;
          }
          if (!chain.length||node&&data.mapping[node]) throw Error('Legacy branch incomplete or cyclic');
          chain.reverse();
          const msgs=chain.filter(n=>n.message&&['user','assistant'].includes(n.message.author?.role))
            .map((n,i)=>toMsg(n.message,i,n.id));
          if (!msgs.length) throw Error('Legacy graph contained no visible turns');
          result={source:'api-legacy',messages:msgs,title:data.title,complete:true};
        } catch (legacyError) {
          snap.error='Paginated API: '+flatError+'; legacy API: '+String(legacyError);
          domFallback();return;
        }
      }
      if (!live()) return;
      snap.messages=result.messages;snap.title=result.title||document.title;
      snap.source=result.source;snap.complete=result.complete;
      // Resolve optional attachments in bounded batches; failure remains visible in export manifest.
      const assets=snap.messages.flatMap(m=>m.attachments).filter(a=>a.id&&!a.url).slice(0,1500);
      for (let start=0;start<assets.length;start+=6) {
        if (!live()) return;
        await Promise.all(assets.slice(start,start+6).map(async a=>{
          try {
            const fileId=String(a.id).replace(/^sediment:\/\//,'').split('?')[0];
            const info=await getJSON('/backend-api/files/download/'+encodeURIComponent(fileId)+
              '?conversation_id='+encodeURIComponent(id),headers);
            if (typeof info.download_url==='string') a.url=info.download_url;
          } catch (_) {}
        }));
      }
      if (live()) snap.state='ready';
    } catch (e) {
      snap.error=String(e);
      try {domFallback();} catch (f) {snap.state='error';snap.error+='; DOM: '+String(f);}
    }
  })();
  return {started:true};
})()'''

STATUS = r'''(() => {
  const s=window.__normConversation;
  if(!s)return {state:'none'};
  return {state:s.state,source:s.source,complete:s.complete,error:s.error,
    title:s.title,url:s.url,count:s.messages.length,pages:s.pages};
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
