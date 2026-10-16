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
        name:a.name||a.file_name||'attachment',id:String(a.id||a.file_id||a.asset_pointer||'').replace(/^sediment:\/\//,'')||null,
        url:a.download_url||a.downloadUrl||a.url||a.public_url||null,mime:a.mime_type||a.mime||null,
        message_id:m.id||nodeId||null});
    }
    for (const p of parts) {
      if (p&&typeof p==='object'&&p.content_type==='image_asset_pointer') {
        attachments.push({kind:'image',name:'Image',id:text(p.asset_pointer).replace(/^sediment:\/\//,''),
          url:p.download_url||p.url||p.public_url||null,mime:p.mime_type||'image/*',
          message_id:m.id||nodeId||null});
      } else if (p&&typeof p==='object'&&p.content_type==='image_v2'&&Array.isArray(p.images)) {
        for (const image of p.images) {
          if (!image||typeof image!=='object') continue;
          attachments.push({kind:'image',name:'Generated image',id:image.id||image.asset_pointer||null,
            url:image.public_url||image.content_url||image.thumbnail_url||null,mime:'image/*',
            message_id:m.id||nodeId||null});
        }
      }
    }
    // ChatGPT commonly lists the same image twice: metadata file_... and
    // multimodal sediment://file_... . Preserve one asset per distinct ID.
    const canonical = new Map(), anonymous = [];
    for (const a of attachments) {
      const key=String(a.id||'').replace(/^sediment:\/\//,'').split(/[?#]/)[0];
      if (!key) {anonymous.push(a);continue;}
      a.id=key;
      const prev=canonical.get(key);
      if (!prev) {canonical.set(key,a);continue;}
      if (a.kind==='image') prev.kind='image';
      if (!prev.url && a.url) prev.url=a.url;
      if ((!prev.mime||prev.mime==='image/png') && a.mime) prev.mime=a.mime;
      if ((!prev.name||prev.name==='Image') && a.name) prev.name=a.name;
    }
    const distinctAttachments=[...canonical.values(),...anonymous];
    let content=parts.map(formatPart).filter(Boolean).join('\n\n');
    if (!content) content=text(body.text)||text(body.content)||text(body.result)||text(body.summary);
    return {id:m.id||nodeId||String(idx),role:(m.author||{}).role||'unknown',
      model:meta.model_slug||meta.resolved_model_slug||'',timestamp:m.create_time||null,
      text:content,attachments:distinctAttachments,content_type:body.content_type||'text',position:idx};
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
          if (!chain.length || (node!==null && node!==undefined))
            throw Error('Legacy branch missing oldest ancestor or cyclic');
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
      // Match the extension's two-stage operation: resolve signed first-party
      // URLs by file ID, then let the local exporter fetch the actual bytes.
      // Keep these short-lived URLs inside the live page until packaging ends.
      const groups=new Map(), allAssets=snap.messages.flatMap(m=>m.attachments||[]);
      for (const a of allAssets) {
        const raw=String(a.id||'').replace(/^sediment:\/\//,'').split(/[?#]/)[0];
        if (!raw) continue;
        if (!/^[A-Za-z0-9_-]{4,150}$/.test(raw)) {
          a.resolution_error='Unsupported attachment ID format';continue;
        }
        a.id=raw;
        if (!groups.has(raw)) groups.set(raw,[]);
        groups.get(raw).push(a);
      }
      snap.attachmentResolution={expected:groups.size,resolved:0,failed:0,
        failures:[],references:allAssets.length,limited:groups.size>1500};
      const entries=Array.from(groups.entries());
      if(entries.length>1500) {
        snap.attachmentResolution.failed=entries.length;
        snap.attachmentResolution.failures.push({reason:'Attachment safety limit exceeded (1500 distinct IDs)'});
      }
      for (let start=0;start<Math.min(entries.length,1500);start+=3) {
        if (!live()) return;
        await Promise.all(entries.slice(start,start+3).map(async ([fileId,aliases])=>{
          try {
            // Prefer a signed URL already supplied in attachment metadata.
            let candidate=aliases.map(a=>a.url).find(x=>typeof x==='string'&&x.trim());
            if(!candidate) {
              let err;
              const queries=['conversation_id='+encodeURIComponent(id)];
              const messageId=aliases.map(a=>a.message_id).find(Boolean);
              // ChatGPT Exporter can use message_id rather than conversation_id
              // for certain file contexts. Do not substitute an invented ID.
              if(messageId) queries.push('message_id='+encodeURIComponent(messageId));
              for (const query of queries) {
                try {
                  const info=await getJSON('/backend-api/files/download/'+encodeURIComponent(fileId)+'?'+query,headers);
                  candidate=info?.download_url||info?.url||info?.downloadUrl||info?.public_url||
                    info?.data?.url||info?.data?.download_url;
                  if(candidate)break;
                  err=Error('No signed asset URL in file metadata');
                }catch(e){err=e;}
              }
              if(!candidate)throw err||Error('ChatGPT did not provide a file URL');
            }
            const parsed=new URL(candidate,location.origin);
            const host=parsed.hostname.toLowerCase();
            if (parsed.protocol!=='https:' || parsed.username || parsed.password ||
                !(['chatgpt.com','chat.openai.com','images.openai.com',
                   'openai.com','oaistatic.com','oaiusercontent.com'].includes(host) ||
                  ['openai.com','oaistatic.com','oaiusercontent.com'].some(base=>host.endsWith('.'+base))))
              throw Error('Asset URL outside allowed OpenAI domains');
            aliases.forEach(a=>{a.url=parsed.href;delete a.resolution_error;});
            snap.attachmentResolution.resolved++;
          }catch(error){
            const description=String(error).slice(0,170);
            aliases.forEach(a=>a.resolution_error=description);
            snap.attachmentResolution.failed++;
            if(snap.attachmentResolution.failures.length<40)
              snap.attachmentResolution.failures.push({id:fileId,reason:description});
          }
        }));
      }
      if (snap.attachmentResolution.limited)
        snap.error='Media resolution limit: more than 1500 distinct files. Partial assets are not verified.';
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
    title:s.title,url:s.url,count:s.messages.length,pages:s.pages,
    attachment_resolution:s.attachmentResolution||null};
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

# Browser-local transfer fallback for ChatGPT assets when a signed URL cannot
# be fetched in the Python worker. Transfers are capped and chunked over the
# existing authenticated localhost RPC; no bearer token or persistent blob.
ASSET_START = r'''((raw) => {
  if (!['chatgpt.com','chat.openai.com'].includes(location.hostname)) return {ok:false,error:'Wrong origin'};
  if (!window.__normConversation || window.__normConversation.state!=='ready')
    return {ok:false,error:'No conversation snapshot'};
  let url;
  try {url=new URL(raw,location.href)} catch (_) {return {ok:false,error:'Invalid URL'}}
  const host=url.hostname.toLowerCase();
  const safe=['chatgpt.com','chat.openai.com','openai.com','oaistatic.com','oaiusercontent.com'].includes(host) ||
    ['openai.com','oaistatic.com','oaiusercontent.com'].some(base=>host.endsWith('.'+base));
  if (url.protocol!=='https:' || !safe || url.username || url.password)
    return {ok:false,error:'Asset host not permitted'};
  if (window.__normAssetTransfer?.state==='loading') return {ok:false,error:'Asset transfer busy'};
  const transfer={state:'loading',data:null,length:0,error:null,url:url.href};
  window.__normAssetTransfer=transfer;
  (async()=>{
    try {
      const resp=await fetch(url.href,{credentials:url.origin===location.origin?'same-origin':'omit',cache:'no-store'});
      if (!resp.ok) throw Error('Asset HTTP '+resp.status);
      if (Number(resp.headers.get('content-length')||0)>24*1024*1024) throw Error('Asset exceeds 24 MiB');
      const bytes=new Uint8Array(await resp.arrayBuffer());
      if (!bytes.length || bytes.length>24*1024*1024) throw Error('Invalid asset size');
      if (window.__normAssetTransfer!==transfer) return;
      transfer.data=bytes;transfer.length=bytes.length;transfer.state='ready';
    } catch(e) {if(window.__normAssetTransfer===transfer){transfer.state='failed';transfer.error=String(e).slice(0,220)}}
  })();
  return {ok:true,state:'loading'};
})'''
ASSET_STATUS = r'''(() => {
  const a=window.__normAssetTransfer;
  return a?{state:a.state,length:a.length,error:a.error}:{state:'none'};
})()'''
ASSET_CHUNK = r'''((start,length) => {
  const a=window.__normAssetTransfer;
  if (!a||a.state!=='ready'||!a.data) return {error:'Asset bytes unavailable'};
  const from=Math.max(0,Math.floor(start));
  const size=Math.max(1,Math.min(24576,Math.floor(length)));
  const bytes=a.data.subarray(from,from+size);
  let raw='';for(let i=0;i<bytes.length;i+=8192)
    raw+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return {start:from,length:a.length,base64:btoa(raw)};
})'''
ASSET_DROP = r'''(() => {window.__normAssetTransfer=null;return {dropped:true};})()'''
