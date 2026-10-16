"""Asynchronous, DOM-positioned viewport capture for Norm's shared Qt Chromium.
The active tab is not navigated or copied. Exports are created atomically by worker.
"""
from __future__ import annotations
import re
import uuid
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from PySide6.QtCore import QTimer, QRect
from PySide6.QtWidgets import QApplication

PREPARE_JS = r"""(()=>{
 const all=[...document.querySelectorAll('[data-message-author-role]')];
 if(all.length<2) return {error:'Conversation contains fewer than two loaded messages'};
 let e=all[0], target=null;
 for(let n=e;n;n=n.parentElement){
   let h=n.scrollHeight-n.clientHeight, style=getComputedStyle(n);
   if(h>80 && /(auto|scroll)/.test(style.overflowY)){target=n;break;}
 }
 if(!target)target=document.scrollingElement;
 if(!target)return {error:'No scroll container found'};
 target.setAttribute('data-norm-visual-scroll','active');
 target.scrollTop=0;target.dispatchEvent(new Event('scroll',{bubbles:true}));
 const r=target===document.scrollingElement?{left:0,top:0,right:innerWidth,bottom:innerHeight}:target.getBoundingClientRect();
 let x=Math.max(0,Math.ceil(r.left)),y=Math.max(0,Math.ceil(r.top));
 let right=Math.min(innerWidth,Math.floor(r.right)),bottom=Math.min(innerHeight,Math.floor(r.bottom));
 const prompts=all.filter(n=>n.getAttribute('data-message-author-role')==='user')
     .slice(-6).map(n=>(n.innerText||n.textContent||'').trim().slice(0,260));
 return {x,y,w:right-x,h:bottom-y,scroll_y:target.scrollTop,
         max:Math.max(0,target.scrollHeight-target.clientHeight),
         count:all.length,prompts,title:document.title,url:location.href};
})()"""

BOTTOM_JS = r"""(()=>{
 const all=[...document.querySelectorAll('[data-message-author-role]')];
 let t=all[0]||document.body, target=null;
 for(let n=t;n;n=n.parentElement){
   const h=n.scrollHeight-n.clientHeight,style=getComputedStyle(n);
   if(h>80 && /(auto|scroll)/.test(style.overflowY)){target=n;break;}
 }
 target=target||document.scrollingElement;
 if(!target)return {error:'No scroll target'};
 target.scrollTop=target.scrollHeight;
 target.dispatchEvent(new Event('scroll',{bubbles:true}));
 return {atEnd:true};
})()"""

TOP_JS = r"""(()=>{
 const n=document.querySelector('[data-norm-visual-scroll="active"]');
 if(!n)return {error:'Scroll element replaced while rewinding'};
 n.scrollTop=0;n.dispatchEvent(new Event('scroll',{bubbles:true}));
 return {scroll_y:n.scrollTop,max:Math.max(0,n.scrollHeight-n.clientHeight),
         count:document.querySelectorAll('[data-message-author-role]').length};
})()"""

SCROLL_JS = r"""
(()=>{
 const n=document.querySelector('[data-norm-visual-scroll="active"]');
 if(!n)return {error:'Scroll element was replaced by the web page'};
 n.scrollTop+=STEP;n.dispatchEvent(new Event('scroll',{bubbles:true}));
 const max=Math.max(0,n.scrollHeight-n.clientHeight);
 return {scroll_y:n.scrollTop,max,count:document.querySelectorAll('[data-message-author-role]').length};
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
        self.state={'state':'capturing','id':ident,'frames':0,'message':'Locating conversation'}
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
        """Rewind to top first, without a risky bottom-jump or page reload.

        QWebEngine may return None when DOM JavaScript is not yet ready; retry
        boundedly with a visible diagnostic instead of declaring conversation end
        impossible. No navigation or profile changes on failure.
        """
        if self.job is None:return
        self.job["prepare_attempts"]=self.job.get("prepare_attempts",0)+1
        self.browser.page.runJavaScript(PREPARE_JS,self._prepared)

    def _prepared(self,data):
        if self.job is None:return
        if not isinstance(data,dict) or data.get('error'):
            issue=(data or {}).get('error','JavaScript returned no scroll metadata') if isinstance(data,dict) else 'JavaScript returned no scroll metadata'
            self.job['prepare_last_error']=issue
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
        self.job.update(clip=(x,y,w,h),y=float(data['scroll_y']),max=float(data['max']),
                        count=int(data['count']), title=data.get('title',''),
                        name=self.job['name'] or infer_name(data.get('prompts') or [],data.get('title','')))
        self.job['folder']=self.base/'.visual-capture-work'/self.job['id']
        self.job['folder'].mkdir(parents=True,exist_ok=False)
        self.job['top_checks']=0
        self.job['top_signature']=None
        self.job['top_stable']=0
        QTimer.singleShot(650,self._check_top)

    def _check_top(self):
        if self.job is None:return
        self.browser.page.runJavaScript(TOP_JS,self._top_checked)

    def _top_checked(self,meta):
        j=self.job
        if j is None:return
        if not isinstance(meta,dict) or meta.get('error'):
            return self._fail((meta or {}).get('error','Could not rewind'))
        j['top_checks']+=1
        signature=(round(float(meta.get('max',0))),int(meta.get('count',0)))
        if float(meta.get('scroll_y',-1))>2:
            j['top_stable']=0
        elif signature==j['top_signature']:
            j['top_stable']+=1
        else:
            j['top_stable']=0
        j['top_signature']=signature
        if j['top_stable']>=2:
            j['y']=0
            j['max']=float(meta['max'])
            QTimer.singleShot(350,self._snap)
        elif j['top_checks']>=90:
            self._fail('Top of conversation did not stabilize after repeated attempts; window and login preserved; no PDF replaced')
        else:
            QTimer.singleShot(700,self._check_top)

    def _snap(self,refresh=False):
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
        j['frames'].append({'path':str(path),'y':j['y']})
        self.state={'state':'capturing','id':j['id'],'frames':len(j['frames']),
                    'scroll_y':round(j['y']),'max_scroll':round(j['max']),
                    'loaded_messages':j['count'],'suggested_name':j['name']}
        if len(j['frames'])>=500:
            return self._fail('500-frame safety limit reached; partial capture not published')
        QTimer.singleShot(90,self._scroll)

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
            QTimer.singleShot(450,self._snap)
            return
        # At apparent bottom, do not trust three identical pictures alone.
        if y+3<limit:
            return self._fail('Scroll stalled above document bottom')
        if abs(limit-j['max'])>3:
            j['max']=limit;j['stable']=0
        else:
            j['stable']+=1
        if j['stable']<4:
            QTimer.singleShot(850,self._scroll)
        else:
            # Re-grab the final viewport after lazy rendering settles.
            QTimer.singleShot(350,lambda:self._snap(refresh=True))

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
