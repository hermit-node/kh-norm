"""Asynchronous, DOM-positioned viewport capture for Norm's shared Qt Chromium.
The active tab is not navigated or copied. Exports are created atomically by worker.
"""
from __future__ import annotations
import re
import uuid
import random
import hashlib
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from PySide6.QtCore import QTimer, QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

PREPARE_JS = r"""(()=>{
 const all=[...document.querySelectorAll('[data-message-author-role]')];
 if(all.length<2) return {error:'Conversation contains fewer than two loaded messages'};
 const e=all[0]; let target=null;
 for(let n=e;n;n=n.parentElement){
   const h=n.scrollHeight-n.clientHeight, style=getComputedStyle(n);
   if(h>80 && /(auto|scroll)/.test(style.overflowY)){target=n;break;}
 }
 target=target||document.scrollingElement;
 if(!target)return {error:'No conversation scroll container found'};
 target.setAttribute('data-norm-visual-scroll','active');
 const r=target===document.scrollingElement?
  {left:0,top:0,right:innerWidth,bottom:innerHeight}:target.getBoundingClientRect();
 const x=Math.max(0,Math.ceil(r.left)),y=Math.max(0,Math.ceil(r.top));
 const right=Math.min(innerWidth,Math.floor(r.right)),bottom=Math.min(innerHeight,Math.floor(r.bottom));
 const prompts=all.filter(n=>n.getAttribute('data-message-author-role')==='user')
     .slice(-6).map(n=>(n.innerText||n.textContent||'').trim().slice(0,260));
 return {x,y,w:right-x,h:bottom-y,scroll_y:target.scrollTop,
   max:Math.max(0,target.scrollHeight-target.clientHeight),count:all.length,
   prompts,title:document.title,url:location.href};
})()"""

# Return scrolling information, message fingerprints, and visible loading-icon
# boxes. CSS loaders are only hints; capture samples determine if they move.
SCROLL_JS = r"""(()=>{
 let n=document.querySelector('[data-norm-visual-scroll="active"]');
 if(!n || !n.isConnected){
   let seed=document.querySelector('[data-message-author-role]')||document.body;
   for(let p=seed;p;p=p.parentElement){
     if(p.scrollHeight-p.clientHeight>80 && /(auto|scroll)/.test(getComputedStyle(p).overflowY)){n=p;break;}
   }
   n=n||document.scrollingElement;
   if(n)n.setAttribute('data-norm-visual-scroll','active');
 }
 if(!n)return {error:'Conversation scroll container is missing'};
 n.scrollTop+=STEP;
 n.dispatchEvent(new Event('scroll',{bubbles:true}));
 const nodes=[...document.querySelectorAll('[data-message-author-role]')];
 const first=nodes[0],last=nodes[nodes.length-1];
 const fingerprint=(e)=>e ? String(e.getAttribute('data-message-id')||e.id||'')+'|'+
    (e.textContent||'').slice(0,90) : '';
 const bounds=n===document.scrollingElement?
   {left:0,top:0,right:innerWidth,bottom:innerHeight}:n.getBoundingClientRect();
 const busy=[];
 const selectors='[role="progressbar"],[aria-busy="true"],[class*="spinner"],svg[class*="animate-spin"],[data-state="loading"]';
 for(const el of document.querySelectorAll(selectors)){
   if(busy.length>=5)break;
   const r=el.getBoundingClientRect(),style=getComputedStyle(el);
   if(style.display==='none'||style.visibility==='hidden'||Number(style.opacity)<.01)continue;
   if(r.width<8||r.height<8||r.width>160||r.height>160)continue;
   if(r.bottom<=bounds.top||r.top>=bounds.bottom||
      r.right<=bounds.left||r.left>=bounds.right)continue;
   busy.push([Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]);
 }
 return {scroll_y:n.scrollTop,max:Math.max(0,n.scrollHeight-n.clientHeight),
  count:nodes.length,first:fingerprint(first),last:fingerprint(last),
  at_top:n.scrollTop<=2,at_bottom:n.scrollTop+3>=n.scrollHeight-n.clientHeight,
  loaders:busy};
})()"""

REGIONS_JS = r"""(()=>{
 const scroll=document.querySelector('[data-norm-visual-scroll="active"]')||document.scrollingElement;
 const viewport=scroll===document.scrollingElement?
   {left:0,top:0,right:innerWidth,bottom:innerHeight}:scroll.getBoundingClientRect();
 const selector='img,svg,canvas,video,pre,table,.katex,.MathJax,[role="img"]';
 const selected=[];
 for(const element of document.querySelectorAll(selector)){
  if(selected.length>=80)break;
  const rect=element.getBoundingClientRect();
  const style=getComputedStyle(element);
  if(style.visibility==='hidden'||style.display==='none'||parseFloat(style.opacity)<.01)continue;
  if(rect.width<44||rect.height<26)continue;
  if(rect.width*rect.height<4000)continue;
  const x0=Math.max(viewport.left,rect.left),x1=Math.min(viewport.right,rect.right);
  const y0=Math.max(viewport.top,rect.top),y1=Math.min(viewport.bottom,rect.bottom);
  if(x1-x0<35||y1-y0<22)continue;
  // Never mark the whole page as an "image".
  if((x1-x0)*(y1-y0)>(viewport.right-viewport.left)*(viewport.bottom-viewport.top)*.82)continue;
  const box={x:x0,y:y0,w:x1-x0,h:y1-y0};
  if(selected.some(b=>box.x>=b.x&&box.y>=b.y&&
      box.x+box.w<=b.x+b.w&&box.y+box.h<=b.y+b.h))continue;
  selected.push(box);
 }
 return selected;
})()"""

STOPWORDS=set('a an and are at be by can could do does for from had has have how i if in is it me my of on or our please so that the this to was we well what when where which who why will with would you your then just let get make file create save name wire scan few last lines chat end'.split())

def safe_name(text):
    words=re.findall(r"[A-Za-z0-9]+",text or "")
    useful=[w.lower().removesuffix("s") if w.lower() in ("norms",) else w.lower()
            for w in words if w.lower() not in STOPWORDS and len(w)>2 and not w.isdigit()]
    useful=list(dict.fromkeys(useful))
    return '-'.join(useful[:9])[:66].strip('-') or 'conversation'

def infer_name(prompts, title):
    # Prefer the last substantive user message, not the assistant's answer.
    for text in reversed(prompts):
        if len(text.strip())>=28:
            candidate=safe_name(text[:240])
            if candidate!='conversation':return candidate
    return safe_name(title)

def _chat_url(url):
    from urllib.parse import urlparse
    u=urlparse(url)
    return (u.scheme=='https' and u.hostname in ('chatgpt.com','chat.openai.com')
            and bool(re.search(r'/(?:c|share)/[A-Za-z0-9_-]+(?:/|$)',u.path)))

class VisualCapture:
    def __init__(self,browser,base):
        self.browser=browser
        self.base=Path(base)
        self.state={'state':'idle'}
        self.job=None
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='norm-visual-pdf')
        self.cancelled=False

    def start(self,name=''):
        if self.job is not None:
            raise RuntimeError('Visual export is already running')
        if not _chat_url(self.browser.page.url().toString()):
            raise RuntimeError('Open a ChatGPT conversation before exporting')
        if self.browser.loading:
            raise RuntimeError('Wait for the browser to finish loading')
        self.cancelled=False
        ident=uuid.uuid4().hex[:12]
        self.job={'id':ident,'name':safe_name(name) if str(name or '').strip() else '', 'frames':[],
                  'y':None,'max':0,'stable':0,'start':monotonic(),
                  'url':self.browser.page.url().toString()}
        self.state={'state':'seeking_top','id':ident,'frames':0,'message':'Locating conversation and scroll thumb'}
        # Do not change geometry, hide, minimize, reload, or recreate the
        # logged-in page. Show only if it is currently hidden.
        if not self.browser.isVisible():
            self.browser.show()
            self.browser.raise_()
        QTimer.singleShot(350,self._prepare)
        return dict(self.state)

    def status(self):
        return dict(self.state)

    def cancel(self):
        if self.job is None:return {'cancelled':False,'state':self.state['state']}
        self.cancelled=True
        self.job['cancel']=True
        if self.state['state']=='assembling':
            return {'cancel_requested':True,'state':'assembling'}
        self._fail('Cancelled; existing PDFs left untouched')
        return {'cancelled':True}

    def _fail(self,reason):
        j=self.job
        self.state={'state':'failed','id':j['id'] if j else None,'error':str(reason)}
        if j and j.get('folder'):
            import shutil
            shutil.rmtree(j['folder'],ignore_errors=True)
        self.job=None

    def _prepare(self):
        """Locate the scroll container without jumping to an arbitrary end."""
        if self.job is None:return
        self.job["prepare_attempts"]=self.job.get("prepare_attempts",0)+1
        self.browser.page.runJavaScript(PREPARE_JS,self._prepared)

    def _prepared(self,data):
        if self.job is None:return
        if not isinstance(data,dict) or data.get('error'):
            issue=(data or {}).get('error','JavaScript returned no scroll metadata') if isinstance(data,dict) else 'JavaScript returned no scroll metadata'
            if self.job['prepare_attempts']<9 and not self.cancelled:
                self.state={'state':'capturing','id':self.job['id'],
                            'frames':0,'message':f'Waiting for conversation DOM ({self.job["prepare_attempts"]}/9): {issue}'}
                QTimer.singleShot(1400,self._prepare)
                return
            return self._fail(f'Could not locate scroll area after {self.job["prepare_attempts"]} attempts: {issue}; browser remains open')
        if not _chat_url(data.get('url','')) or data.get('url')!=self.job['url']:
            return self._fail('Conversation changed during preparation')
        x,y,w,h=(int(data.get(v,0)) for v in ('x','y','w','h'))
        if min(w,h)<180:return self._fail('Scroll viewport too small for capture')
        j=self.job
        j.update(clip=(x,y,w,h),count=int(data['count']),
                 title=data.get('title',''),initial_top=float(data['scroll_y'])<=2,
                 name=j['name'] or infer_name(data.get('prompts') or [],data.get('title','')),
                 discovery_start=monotonic(),top_last_change=monotonic(),
                 top_signature=None,top_checks=0,loader_samples=0,loader_hashes=[])
        self.state={'state':'seeking_top','id':j['id'],'message':'Discovering oldest loaded conversation turn'}
        QTimer.singleShot(200,self._seek_top)

    def _seek_top(self):
        j=self.job
        if not j or self.cancelled:return
        if self.browser.page.url().toString()!=j['url']:
            return self._fail('Conversation URL changed; logged-in window kept open')
        # Scroll upward by a real viewport fraction instead of setting scrollTop
        # to zero once. This triggers the website's lazy-history listeners.
        delta=-max(350,int(j['clip'][3]*.72))
        self.browser.page.runJavaScript(SCROLL_JS.replace('STEP',str(delta)),self._top_checked)

    def _spinner_fingerprint(self,rects):
        pix=self.browser.view.grab()
        if pix.isNull():
            return None
        import hashlib
        hash_values=[]
        scale_x=pix.width()/max(self.browser.view.width(),1)
        scale_y=pix.height()/max(self.browser.view.height(),1)
        for x,y,w,h in rects[:5]:
            r=QRect(round(x*scale_x),round(y*scale_y),
                    max(1,round(w*scale_x)),max(1,round(h*scale_y)))
            r=r.intersected(QRect(0,0,pix.width(),pix.height()))
            if not r.isEmpty():
                cropped=pix.copy(r).toImage().convertToFormat(
                    QImage.Format.Format_RGBA8888)
                # Hash only candidate loader pixels, never the whole conversation.
                hash_values.append(hashlib.blake2b(bytes(cropped.bits()),digest_size=10).hexdigest())
        return tuple(hash_values)

    def _loader_sample(self,phase="top"):
        j=self.job
        if j is None:return
        signature=self._spinner_fingerprint(j.get('loading_rects') or [])
        if signature is not None:
            j['loader_hashes'].append(signature)
        j['loader_samples']+=1
        if j['loader_samples']<6:
            QTimer.singleShot(random.randint(1000,3000),
                               lambda:self._loader_sample(phase))
            return
        observed=j['loader_hashes']
        moving=len(set(observed))>1
        j['loader_samples']=0
        j['loader_hashes']=[]
        j['loading_rects']=[]
        if not moving:
            # Static loading-shaped DOM widgets aren't progress evidence.
            j['ignore_static_loaders_until']=monotonic()+30.0
        action=self._seek_top if phase=="top" else self._scroll
        self.state={'state':'seeking_top' if phase=="top" else 'capturing',
                    'id':j['id'],'frames':len(j['frames']),
                    'message':('Loading animation moving; waiting 5 seconds' if moving
                               else 'Static loading icon ignored; retrying scroll')}
        QTimer.singleShot(5000 if moving else 350,action)

    def _top_checked(self,meta):
        j=self.job
        if j is None:return
        if not isinstance(meta,dict) or meta.get('error'):
            return self._fail((meta or {}).get('error','Upward scrolling unavailable'))
        j['top_checks']+=1
        now=monotonic()
        signature=(round(float(meta.get('max',0))),int(meta.get('count',0)),
                   str(meta.get('first','')))
        if signature!=j.get('top_signature') or float(meta.get('scroll_y',0))>2:
            j['top_last_change']=now
        j['top_signature']=signature
        if meta.get('loaders') and now >= j.get('ignore_static_loaders_until',0):
            j['loading_rects']=meta['loaders']
            j['loader_samples']=0
            j['loader_hashes']=[]
            self.state={'state':'seeking_top','id':j['id'],
                        'message':'Checking visible loading animation (six screenshot samples)'}
            self._loader_sample()
            return
        at_top=bool(meta.get('at_top',float(meta.get('scroll_y',0))<=2))
        stable_for=now-j['top_last_change']
        # Already positioned at the top: quick start in ~6s. If we traveled
        # upwards, allow 60s after the last lazy-history change.
        required=5.0 if j['initial_top'] else 60.0
        self.state={'state':'seeking_top','id':j['id'],
                    'scroll_y':round(float(meta['scroll_y'])),
                    'stable_seconds':round(stable_for,1),
                    'message':'Top scrollbar stable; checking history' if at_top else 'Scrolling up to oldest conversation turn'}
        if at_top and stable_for>=required:
            j['y']=float(meta['scroll_y'])
            j['max']=float(meta['max'])
            j['count']=int(meta.get('count',j['count']))
            j['folder']=self.base/'.visual-capture-work'/j['id']
            j['folder'].mkdir(parents=True,exist_ok=False)
            j['discovery_complete']=True
            QTimer.singleShot(100,self._snap)
            return
        if now-j['discovery_start']>900:
            return self._fail('Conversation top not stable after 15 minutes; browser preserved')
        QTimer.singleShot(650,self._seek_top)

    def _snap(self,refresh=False):
        if not self.job or self.cancelled:return
        self.browser.page.runJavaScript(
            REGIONS_JS,lambda rects:self._snap_pixels(rects,refresh))

    def _snap_pixels(self,rects,refresh=False):
        j=self.job
        if not j or self.cancelled:return
        if self.browser.page.url().toString()!=j['url']:
            return self._fail('Conversation URL changed during capture')
        pix=self.browser.view.grab()
        if pix.isNull():return self._fail('Browser view could not be captured')
        x,y,w,h=j['clip']
        sw=self.browser.view.width();sh=self.browser.view.height()
        scale_x=pix.width()/max(sw,1);scale_y=pix.height()/max(sh,1)
        rect=QRect(round(x*scale_x),round(y*scale_y),round(w*scale_x),round(h*scale_y))
        if rect.right()>=pix.width() or rect.bottom()>=pix.height():
            rect=rect.intersected(QRect(0,0,pix.width(),pix.height()))
        cropped=pix.copy(rect)
        if cropped.isNull() or cropped.width()<250 or cropped.height()<200:
            return self._fail('Capture crop was empty')
        index=len(j['frames'])-1 if refresh else len(j['frames'])
        path=j['folder']/f"frame-{index:04d}.png"
        if not cropped.save(str(path),'PNG'):
            return self._fail('Unable to write captured frame')
        if refresh:
            self._assemble()
            return
        j['frames'].append({'path':str(path),'y':j['y'],
                             'visual_regions':rects if isinstance(rects,list) else []})
        self.state={'state':'capturing','id':j['id'],'frames':len(j['frames']),
                    'scroll_y':round(j['y']),'max_scroll':round(j['max']),
                    'loaded_messages':j['count'],'suggested_name':j['name']}
        if len(j['frames'])>=500:
            return self._fail('500-frame safety limit reached; partial capture not published')
        # Capture a second, slightly later sample of each location for
        # repairing compositor tearing / transitions during stitching.
        QTimer.singleShot(100,self._snap_alternative)

    def _snap_alternative(self):
        j=self.job
        if not j or self.cancelled:return
        pix=self.browser.view.grab()
        if not pix.isNull() and j['frames']:
            x,y,w,h=j['clip']
            scale_x=pix.width()/max(1,self.browser.view.width())
            scale_y=pix.height()/max(1,self.browser.view.height())
            rect=QRect(round(x*scale_x),round(y*scale_y),
                       round(w*scale_x),round(h*scale_y))
            rect=rect.intersected(QRect(0,0,pix.width(),pix.height()))
            sample=pix.copy(rect)
            if not sample.isNull() and sample.width()>=250 and sample.height()>=200:
                alternate=j['folder']/f"alternate-{len(j['frames'])-1:04d}.png"
                if sample.save(str(alternate),'PNG'):
                    j['frames'][-1]['alt_path']=str(alternate)
        QTimer.singleShot(25,self._scroll)

    def _scroll(self):
        j=self.job
        if not j:return
        delta=int(j['clip'][3]*.68)
        self.browser.page.runJavaScript(SCROLL_JS.replace('STEP',str(delta)),self._scrolled)

    def _scrolled(self,meta):
        j=self.job
        if not j:return
        if not isinstance(meta,dict) or meta.get('error'):
            return self._fail((meta or {}).get('error','Scroll metadata unavailable'))
        y=float(meta.get('scroll_y',0))
        limit=float(meta.get('max',0))
        if y+1<j['y']:
            return self._fail('Page shifted backwards during capture')
        if y-j['y']>j['clip'][3]*.95:
            return self._fail('Scroll displacement exceeded safe overlap')
        j['count']=max(j['count'],int(meta.get('count',0)))
        if y>j['y']+.5:
            j['y']=y;j['max']=limit;j['stable']=0
            j['bottom_signature']=None
            j['bottom_last_change']=monotonic()
            QTimer.singleShot(95,self._snap)
            return
        if monotonic()-j['start']>3600:
            return self._fail('Capture exceeded one-hour safety budget; original browser/session preserved')
        # DOM scrollbar indicator is the primary bottom-of-page signal. A
        # stationary thumb alone is insufficient: compare the last turn and
        # loading animation; keep attempting to scroll on any new content.
        if not meta.get('at_bottom',y+3>=limit):
            return self._fail('Scroll stalled above scrollbar bottom; browser still visible')
        signature=(round(limit),int(meta.get('count',0)),str(meta.get('last','')))
        if signature!=j.get('bottom_signature'):
            j['bottom_signature']=signature
            j['bottom_last_change']=monotonic()
        if meta.get('loaders') and monotonic()>=j.get('ignore_static_loaders_until',0):
            j['loading_rects']=meta['loaders']
            j['loader_samples']=0
            j['loader_hashes']=[]
            j['stable']=0
            self._loader_sample("bottom")
            return
        if monotonic()-j.get('bottom_last_change',monotonic())<4.0:
            QTimer.singleShot(600,self._scroll)
        else:
            QTimer.singleShot(250,lambda:self._snap(refresh=True))

    def _assemble(self):
        j=self.job
        if not j:return
        self.state={'state':'assembling','id':j['id'],'frames':len(j['frames']),
                    'suggested_name':j['name']}
        from visual_pdf_worker import build_visual_pdf
        try:
            task=self.executor.submit(build_visual_pdf,j,self.base)
        except Exception as exc:
            return self._fail(exc)
        def poll():
            if self.job is None:return
            if not task.done():
                QTimer.singleShot(200,poll)
                return
            try:
                result=task.result()
            except Exception as exc:
                return self._fail(exc)
            self.state={'state':'complete','id':j['id'],**result}
            self.job=None
        QTimer.singleShot(200,poll)
