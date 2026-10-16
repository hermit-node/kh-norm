"""Persistent, normally-hidden Chromium WebEngine shared by human and Norm.

Run in interactive Windows session; only loopback authenticated RPC is exposed.
Disk cache is separate from extraction archives and is never packaged.
"""
from __future__ import annotations
import argparse
import hmac
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
VENDOR=HERE/"vendor"
if VENDOR.exists():sys.path.insert(0,str(VENDOR))
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
    "--js-flags=--max-old-space-size=12288 --disk-cache-size=2147483647 "
    "--disable-background-timer-throttling --disable-renderer-backgrounding")
if "--enable-cdp" in sys.argv:
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "127.0.0.1:18768")
from PySide6.QtCore import QObject, QUrl, Signal, Slot, Qt, QTimer
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLineEdit,QLabel
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from visual_capture import VisualCapture

TOKEN=ROOT/"state"/"shared_browser_token"
sys.path.insert(0,str(ROOT/"core"))
from norm_runtime.settings import load_path_settings
BASE=Path(load_path_settings(ROOT)["workspace_root"])/"web"/"shared_browser"
DISK_CACHE=2**31-1
ARCHIVE_BUDGET=32*1024**3

class CommandBridge(QObject):
    request=Signal(object)

class Browser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Norm Shared Browser | Human + N1 + N2")
        self.resize(1400,900)
        self.manual=False;self.loading=False;self.load_ok=None
        (BASE/"profile").mkdir(parents=True,exist_ok=True)
        (BASE/"cache").mkdir(parents=True,exist_ok=True)
        self.profile=QWebEngineProfile("NormShared",self)
        self.profile.setPersistentStoragePath(str(BASE/"profile"))
        self.profile.setCachePath(str(BASE/"cache"))
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setHttpCacheMaximumSize(DISK_CACHE)
        self.page=QWebEnginePage(self.profile,self)
        self.view=QWebEngineView(self);self.view.setPage(self.page)
        self.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        self.page.loadStarted.connect(self._loading)
        self.page.loadFinished.connect(self._loaded)
        self.page.urlChanged.connect(lambda u:self.address.setText(u.toString()))
        self.address=QLineEdit(self)
        self.address.setPlaceholderText("https://chatgpt.com/...")
        self.address.returnPressed.connect(self._navigate)
        bar=QWidget();layout=QVBoxLayout(bar);tools=QHBoxLayout()
        back=QPushButton("Back");back.clicked.connect(self.view.back)
        forward=QPushButton("Forward");forward.clicked.connect(self.view.forward)
        reload=QPushButton("Reload");reload.clicked.connect(self.view.reload)
        go=QPushButton("Go");go.clicked.connect(self._navigate)
        hide=QPushButton("Hide");hide.clicked.connect(self._hide_requested)
        self.take_button=QPushButton("Take Control")
        self.take_button.clicked.connect(self._take)
        self.release_button=QPushButton("Return Control to Norm")
        self.release_button.clicked.connect(self._release)
        self.nav_buttons=(back,forward,reload,go)
        self.export_button=QPushButton("Visual PDF")
        self.export_button.setToolTip("Capture conversation top-to-bottom; auto-name from recent user turns")
        self.export_button.clicked.connect(self._export_ui)
        self.cancel_button=QPushButton("Cancel PDF")
        self.cancel_button.clicked.connect(lambda:self.visual.cancel())
        self.cancel_button.setEnabled(False)
        for x in (back,forward,reload):tools.addWidget(x)
        tools.addWidget(self.address,1)
        for x in (go,self.take_button,self.release_button,self.export_button,self.cancel_button,hide):tools.addWidget(x)
        self.label=QLabel("Norm has control | 2 GiB cache; 32 GiB archive; 12 GiB JS ceiling")
        self._update_control_indicators()
        layout.addLayout(tools);layout.addWidget(self.label);layout.addWidget(self.view,1)
        self.setCentralWidget(bar)
        self.bridge=CommandBridge(self)
        self.bridge.request.connect(self.dispatch,Qt.ConnectionType.QueuedConnection)
        self.visual=VisualCapture(self,BASE)
        self.visual_timer=QTimer(self)
        self.visual_timer.timeout.connect(self._visual_update)
        self.visual_timer.start(900)
    def _loading(self):self.loading=True;self.load_ok=None
    def _loaded(self,ok):
        self.loading=False;self.load_ok=bool(ok)
        self._update_control_indicators()
    def _navigate(self):
        if self.visual.job is not None:return
        self.page.setUrl(QUrl.fromUserInput(self.address.text().strip()))
    def _update_control_indicators(self):
        self.take_button.setEnabled(not self.manual)
        self.release_button.setEnabled(self.manual)
        controller=("Human has control; Norm page actions paused"
                    if self.manual else
                    "Norm has control; window may stay visible")
        loaded=("loading" if self.loading else
                "loaded" if self.load_ok is True else
                "load failed" if self.load_ok is False else "no page loaded")
        self.label.setText(f"{controller} | {loaded}")
    def _take(self):
        if self.visual.job is not None:
            self.label.setText("Visual export in progress; cancel it before changing control")
            return
        self.manual=True
        self._update_control_indicators()
    def _release(self):
        self.manual=False
        self._update_control_indicators()
    def _hide_requested(self):
        if self.visual.job is not None:
            self.label.setText("Visual export requires visible Chromium; cancel export to hide")
            return
        self.hide()
    def _export_ui(self):
        try:
            result=self.visual.start()
            self.label.setText("Visual PDF started: "+result['id'])
        except Exception as exc:
            self.label.setText("Visual PDF not started: "+str(exc))
    def _visual_update(self):
        result=self.visual.status()
        busy=self.visual.job is not None
        self.export_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.take_button.setEnabled(not self.manual and not busy)
        for button in self.nav_buttons:
            button.setEnabled(not busy)
        self.address.setReadOnly(busy)
        if result['state']=='capturing':
            self.label.setText(f"Visual PDF: {result.get('frames',0)} frames | "+
                               f"{result.get('scroll_y',0)} / {result.get('max_scroll',0)} px")
        elif result['state']=='assembling':
            self.label.setText("Visual PDF: assembling and paginating...")
        elif result['state']=='complete':
            self.label.setText("Visual PDF saved: "+result['pdf'])
        elif result['state']=='failed':
            self.label.setText("Visual PDF failed: "+result['error'])
    def closeEvent(self,event):
        # Closing the browser during visual PDF capture must not silently hide
        # the logged-in session or strand its Qt screenshot work.
        if self.visual.job is not None:
            event.ignore()
            self.label.setText("Visual PDF is still running. Cancel PDF before hiding this window.")
            return
        event.ignore()
        self.hide()  # Explicit user close means hide, never delete browser profile.

    @staticmethod
    def done(job,result=None,error=""):
        job["result"]=result if result is not None else {}
        job["error"]=str(error);job["event"].set()
    def script(self,job,js):
        def callback(raw):
            try:
                if not isinstance(raw,str) or not raw:
                    raise ValueError("Javascript returned no structured result")
                if len(raw)>2000000:
                    raise ValueError("Result exceeds 2MB; use smaller batches")
                self.done(job,json.loads(raw))
            except Exception as exc:self.done(job,error=f"DOM extraction: {exc}")
        self.page.runJavaScript("JSON.stringify("+js+")",callback)

    @Slot(object)
    def dispatch(self,job):
        act=str(job.get("action") or "");args=job.get("args") or {}
        if self.manual and act not in ("status","show","hide","take_control","release","visual_pdf_status","visual_pdf_cancel","quit"):
            self.done(job,error="Human controls this page; return control to Norm")
            return
        if self.visual.job is not None and act not in ("status","visual_pdf_status","visual_pdf_cancel","quit"):
            self.done(job,error="Visual PDF capture in progress; other page actions paused")
            return
        try:
            if act=="status":
                self.done(job,{"url":self.page.url().toString(),"title":self.page.title(),
                    "loading":self.loading,"load_ok":self.load_ok,
                    "visible":self.isVisible(),"manual_control":self.manual,
                    "cache_path":str(BASE/"cache"),"profile_path":str(BASE/"profile"),
                    "cache_limit_bytes":DISK_CACHE,"archive_budget_bytes":ARCHIVE_BUDGET,
                    "effective_cache_limit_bytes":self.profile.httpCacheMaximumSize()})
            elif act=="show":
                self.show();self.raise_();self.activateWindow()
                self.done(job,{"visible":self.isVisible(),"manual_control":self.manual})
            elif act=="hide":
                if self.manual or self.visual.job is not None:
                    raise ValueError("Window stays visible during manual control or Visual PDF; only the user may hide it.")
                self.hide()
                self.done(job,{"visible":self.isVisible(),"manual_control":self.manual})
            elif act=="take_control":
                self._take()
                self.show();self.raise_();self.activateWindow()
                self.done(job,{"visible":self.isVisible(),"manual_control":True})
            elif act=="release":
                self._release()
                self.done(job,{"visible":self.isVisible(),"manual_control":False})
            elif act=="open":
                url=str(args.get("url") or "").strip()
                target=QUrl.fromUserInput(url)
                if not url or len(url)>4000 or target.scheme().lower() not in ("http","https","file","data","about"):
                    raise ValueError("Unsupported URL or length")
                self.page.setUrl(target)
                self.done(job,{"navigation_started":True,"url":target.toString()})
            elif act=="messages":
                start=max(0,int(args.get("start") or 0))
                count=max(1,min(40,int(args.get("count") or 20)))
                script=("(()=>{let a=Array.from(document.querySelectorAll('[data-message-author-role]'));"
                    "return {url:location.href,title:document.title,total_loaded:a.length,"
                    "start:"+str(start)+",messages:a.slice("+str(start)+","+str(start+count)+").map((e,i)=>"
                    "({position:i+"+str(start)+",role:e.getAttribute('data-message-author-role'),"
                    "message_id:e.getAttribute('data-message-id')||e.id||null,"
                     "text_length:(e.innerText||e.textContent||'').length,"
                    "text:(e.innerText||e.textContent||'').slice(0,16000)}))};})()")
                self.script(job,script)
            elif act=="message_piece":
                pos=max(0,int(args.get("position") or 0))
                start=max(0,int(args.get("start") or 0))
                length=max(1,min(32000,int(args.get("length") or 16000)))
                script=("(()=>{let e=document.querySelectorAll('[data-message-author-role]')["+str(pos)+"];"
                    "if(!e)return {found:false};let txt=e.innerText||e.textContent||'';"
                    "return {found:true,position:"+str(pos)+",total_length:txt.length,"
                    "start:"+str(start)+",text:txt.slice("+str(start)+","+str(start+length)+")};})()")
                self.script(job,script)
            elif act=="dom":
                selector=json.dumps(str(args.get("selector") or "body")[:300])
                start=max(0,int(args.get("start") or 0))
                count=max(1,min(50,int(args.get("count") or 20)))
                script=("(()=>{let a=Array.from(document.querySelectorAll("+selector+"));"
                    "return {url:location.href,total:a.length,start:"+str(start)+",items:"
                    "a.slice("+str(start)+","+str(start+count)+").map((e,i)=>"
                    "({index:i+"+str(start)+",tag:e.tagName,text:(e.innerText||e.textContent||'').slice(0,35000),"
                    "html:e.outerHTML.slice(0,50000)}))};})()")
                self.script(job,script)
            elif act=="html":
                maxchars=max(1024,min(150000,int(args.get("max_chars") or 60000)))
                self.page.toHtml(lambda value:self.done(job,
                    {"url":self.page.url().toString(),"html":value[:maxchars]}))
            elif act=="scroll":
                dy=max(-25000,min(25000,int(args.get("pixels") or 0)))
                # Real sites often scroll an inner content container, not
                # window. Find the largest scrollable region using the DOM.
                script=("(()=>{let nodes=[document.scrollingElement,...document.querySelectorAll('main *,[role=main] *')];"
                    "let best=null,range=0;for(let el of nodes){if(!el)continue;"
                    "let d=el.scrollHeight-el.clientHeight;"
                    "if(d>range+20){range=d;best=el;}}"
                    "if(!best)best=document.scrollingElement||document.body;"
                    "best.scrollTop+=("+str(dy)+");best.dispatchEvent(new Event('scroll',{bubbles:true}));"
                    "return {tag:best.tagName,y:best.scrollTop,max:range};})()")
                self.script(job,script)
            elif act=="click":
                selector=json.dumps(str(args.get("selector") or "")[:300])
                self.script(job,"(()=>{let e=document.querySelector("+selector+");"
                    "if(!e)return {found:false};e.click();return {found:true};})()")
            elif act=="type":
                selector=json.dumps(str(args.get("selector") or "")[:300])
                value=json.dumps(str(args.get("value") or "")[:10000])
                self.script(job,"(()=>{let e=document.querySelector("+selector+");"
                    "if(!e)return {found:false};e.focus();e.value="+value+";"
                    "e.dispatchEvent(new Event('input',{bubbles:true}));"
                    "e.dispatchEvent(new Event('change',{bubbles:true}));return {found:true};})()")
            elif act=="visual_pdf_start":
                self.done(job,self.visual.start(args.get("name","")))
            elif act=="visual_pdf_status":
                self.done(job,self.visual.status())
            elif act=="visual_pdf_cancel":
                self.done(job,self.visual.cancel())
            elif act=="quit":
                self.done(job,{"closing":True})
                QTimer.singleShot(100,QApplication.instance().quit)
            else:
                raise ValueError("Unsupported browser operation")
        except Exception as exc:
            self.done(job,error=f"{type(exc).__name__}: {exc}")


class Handler(BaseHTTPRequestHandler):
    bridge=None;token=""
    def log_message(self,*args):pass
    def reply(self,code,obj):
        body=json.dumps(obj,ensure_ascii=False,default=str).encode("utf8")
        self.send_response(code);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)));self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        if self.path!="/rpc":return self.reply(404,{"ok":False,"error":"Not found"})
        if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+self.token):
            return self.reply(403,{"ok":False,"error":"Unauthenticated"})
        try:
            n=int(self.headers.get("Content-Length","0"))
            if not 0<n<=256000:raise ValueError("RPC payload exceeds 256KB")
            request=json.loads(self.rfile.read(n))
            if not isinstance(request,dict) or not isinstance(request.get("args",{}),dict):
                raise ValueError("Invalid browser RPC")
            job={"action":request.get("action"),"args":request.get("args") or {},
                 "event":threading.Event(),"result":{},"error":""}
            self.bridge.request.emit(job)
            if not job["event"].wait(45):
                return self.reply(504,{"ok":False,"error":"Browser did not answer within 45 seconds"})
            if job["error"]:return self.reply(400,{"ok":False,"error":job["error"]})
            return self.reply(200,{"ok":True,"result":job["result"]})
        except (ValueError,KeyError,TypeError) as exc:
            return self.reply(400,{"ok":False,"error":str(exc)})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--show",action="store_true")
    parser.add_argument("--enable-cdp",action="store_true")
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--port",type=int,default=18767)
    args=parser.parse_args()
    app=QApplication(sys.argv[:1]);app.setQuitOnLastWindowClosed(False)
    browser=Browser()
    if args.show:browser.show()
    if args.self_test:
        def run():
            browser.page.setHtml("<html><body><div data-message-author-role='user'>hello</div></body></html>")
            browser.page.loadFinished.connect(lambda ok:
                browser.page.runJavaScript(
                    "document.querySelector('[data-message-author-role]').innerText",
                    lambda value:(print("WEBENGINE_DOM_TEST",ok,value,flush=True),app.quit())))
        QTimer.singleShot(250,run)
        return app.exec()
    TOKEN.parent.mkdir(parents=True,exist_ok=True)
    if not TOKEN.exists():
        with TOKEN.open("x",encoding="ascii") as f:f.write(secrets.token_hex(32))
    key=TOKEN.read_text("ascii").strip()
    if len(key)!=64:raise RuntimeError("Browser token invalid")
    Handler.bridge=browser.bridge;Handler.token=key
    server=ThreadingHTTPServer(("127.0.0.1",args.port),Handler)
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    print("Norm Shared Browser ready; hidden unless requested",flush=True)
    try:return app.exec()
    finally:server.shutdown();server.server_close()

if __name__=="__main__":
    raise SystemExit(main())
