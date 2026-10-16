"""Native N1-gated tools for the authenticated Qt shared browser."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import subprocess


def _root():
    return Path(__file__).resolve().parents[3]


def _start_if_needed(action: str) -> bool:
    """Lazy-start on explicit browser operations; never on status/monitoring.

    The launcher alone owns session selection and reuses the existing Qt tab.
    """
    if action in ('status','cooperation_status','visual_pdf_status'):
        return False
    # The browser token is persisted, so its presence does not mean it is running.
    py=_root()/'.venv'/'Scripts'/'python.exe'
    root=_root()
    launcher=root/'tools'/'start_shared_browser.py'
    if not py.is_file() or not launcher.is_file():
        raise RuntimeError('Norm shared browser launcher or .venv is missing')
    try:
        result=subprocess.run([str(py),'-B',str(launcher)],cwd=str(root),
                              capture_output=True,text=True,timeout=18)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('On-demand shared browser launch failed: '+str(exc)) from exc
    if result.returncode:
        raise RuntimeError('On-demand shared browser launch failed: '+(result.stderr or result.stdout)[-400:])
    return True

def _rpc(action, **kwargs):
    tokenfile=_root()/"state/shared_browser_token"
    if not tokenfile.is_file():
        if action in ('status','visual_pdf_status'):
            return {'running':False,'state':'not_started','on_demand':True}
        _start_if_needed(action)
    if not tokenfile.is_file():
        raise RuntimeError('Shared browser unavailable; open Show Shared Browser in your Windows session')
    token=tokenfile.read_text("ascii").strip()
    if len(token)!=64:raise PermissionError("Invalid local browser token")
    request=Request("http://127.0.0.1:18767/rpc",
        json.dumps({"action":action,"args":kwargs},ensure_ascii=False).encode("utf8"),
        method="POST",headers={"Authorization":"Bearer "+token,
                               "Content-Type":"application/json"})
    try:
        with urlopen(request,timeout=50) as response:
            obj=json.load(response)
    except HTTPError as exc:
        try:payload=json.load(exc)
        except Exception:payload={}
        raise RuntimeError(str(payload.get('error') or 'Browser HTTP '+str(exc.code))) from exc
    except OSError as exc:
        if action in ('status','visual_pdf_status'):
            return {'running':False,'state':'not_started','on_demand':True}
        _start_if_needed(action)
        try:
            with urlopen(request,timeout=50) as response:
                obj=json.load(response)
        except HTTPError as second:
            try:payload=json.load(second)
            except Exception:payload={}
            raise RuntimeError(str(payload.get('error') or 'Browser HTTP '+str(second.code))) from second
        except OSError as second:
            raise RuntimeError('Shared browser unavailable in the interactive Windows session') from second
    if not obj.get("ok"):
        raise RuntimeError(str(obj.get("error") or "Browser operation failed"))
    return obj.get("result") or {}


def browser_shared_status() -> dict:
    """Inspect persistent Qt Chromium session: URL, cache, loading and human-control state."""
    return _rpc("status")


def browser_shared_open(url: str) -> dict:
    """Navigate persistent Qt Chromium to a page; asynchronous loading, poll status."""
    return _rpc("open",url=url)


def browser_shared_show() -> dict:
    """Show the SAME Chromium page without changing who controls it."""
    return _rpc("show")


def browser_shared_hide() -> dict:
    """Hide the browser without releasing control or changing its page."""
    return _rpc("hide")


def browser_shared_take_control() -> dict:
    """Give the human manual control; pause Norm page actions until released."""
    return _rpc("take_control")


def browser_shared_release() -> dict:
    """Return page control to Norm, leaving the window visible as-is."""
    return _rpc("release")


def browser_shared_chatgpt_diagnostics() -> dict:
    """Read ChatGPT UI state (login/challenge/composer/turn counts), no credentials."""
    return _rpc('chatgpt_diagnostics')


def browser_shared_messages(start: int = 0, count: int = 20) -> dict:
    """Read already-loaded ChatGPT messages directly from DOM, no mouse or scrolling."""
    return _rpc("messages",start=int(start),count=max(1,min(40,int(count))))


def browser_shared_dom(selector: str = "body", start: int = 0, count: int = 20) -> dict:
    """Extract HTML/text from DOM nodes matching CSS, without using a mouse."""
    return _rpc("dom",selector=selector,start=start,count=count)


def browser_shared_bs_select(selector: str, max_chars: int = 60000) -> dict:
    """Parse captured HTML with BeautifulSoup and SoupSieve; browser not clicked."""
    from bs4 import BeautifulSoup
    import soupsieve
    response=_rpc("html",max_chars=max_chars)
    soup=BeautifulSoup(response["html"],"html.parser")
    found=soupsieve.select(selector,soup,limit=30)
    return {"url":response["url"],"selector":selector,"count":len(found),
            "items":[{"text":x.get_text(" ",strip=True)[:12000],
                      "html":str(x)[:20000]} for x in found],
            "html_truncated":len(response["html"])>=max_chars}


def browser_shared_scroll(pixels: int = 800) -> dict:
    """Trigger JavaScript scrolling to load lazily-rendered elements, no UI dragging."""
    return _rpc("scroll",pixels=pixels)


def browser_shared_click(selector: str) -> dict:
    """Click a DOM CSS selector in the shared session; blocked during human control."""
    return _rpc("click",selector=selector)


def browser_shared_type(selector: str, value: str) -> dict:
    """Set a normal input element's value and fire input/change events."""
    return _rpc("type",selector=selector,value=value)


def _export_root() -> Path:
    """Resolve the same Documents workspace used by the main Norm runtime."""
    root=_root()
    import sys
    core=str(root/"core")
    if core not in sys.path:sys.path.insert(0,core)
    from norm_runtime.settings import load_path_settings
    return Path(load_path_settings(root)["workspace_root"])/"web"/"shared_browser"/"exports"


def _slug(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+","_",name)[:80].strip("._") or "chatgpt"


def _valid_chat_url(url: str) -> bool:
    from urllib.parse import urlparse
    u=urlparse(url)
    import re
    return (u.scheme=="https" and (u.hostname or "").casefold()
            in {"chatgpt.com","chat.openai.com"} and
            bool(re.search(r"/(?:c|share)/[A-Za-z0-9_-]+(?:/|$)",u.path)))


def browser_shared_archive_loaded(name: str = "chatgpt", *, max_messages: int = 6000) -> dict:
    """Incrementally archive currently loaded ChatGPT turns as durable JSONL.

    Does not assert the whole chat is loaded. Repeated calls add only new/changed
    messages. Large individual replies are collected in 16k DOM text chunks.
    """
    root=_export_root()
    slug=_slug(name)
    dest=root/(slug+".jsonl")
    index_path=root/(slug+".sqlite")
    snapshot=_rpc("messages",start=0,count=1)
    total=min(max(0,int(snapshot.get("total_loaded") or 0)),max(1,int(max_messages)))
    url=str(snapshot.get("url") or "")
    if not _valid_chat_url(url):
        raise RuntimeError("Browser is not on an individual ChatGPT conversation. Show the browser, log in and open the chat; nothing exported.")
    if total<2:
        raise RuntimeError(f"Only {total} ChatGPT message(s) loaded; refusing an empty/one-message archive.")
    if dest.is_file() and dest.stat().st_size:
        with dest.open("r",encoding="utf8") as previous:
            first=previous.readline().strip()
        if first and json.loads(first).get("url")!=url:
            raise RuntimeError("Archive name belongs to a different conversation; choose a new name.")
    root.mkdir(parents=True,exist_ok=True)
    import sqlite3
    con=sqlite3.connect(index_path)
    con.execute("CREATE TABLE IF NOT EXISTS archived (fingerprint TEXT PRIMARY KEY, role TEXT, position INTEGER)")
    new=0
    incomplete=total<int(snapshot.get("total_loaded") or 0)
    with dest.open("a",encoding="utf8") as output:
        for start in range(0,total,20):
            batch=_rpc("messages",start=start,count=min(20,total-start))
            for item in batch.get("messages",[]):
                position=int(item.get("position") or 0)
                content=str(item.get("text") or "")
                length=int(item.get("text_length") or len(content))
                while len(content)<length:
                    piece=_rpc("message_piece",position=position,start=len(content),length=16000)
                    if not piece.get("found") or not piece.get("text"):
                        incomplete=True;break
                    content+=str(piece["text"])
                if len(content)!=length:incomplete=True
                role=str(item.get("role") or "unknown")
                message_id=str(item.get("message_id") or "")
                identity=message_id or f"position:{position}"
                fingerprint=hashlib.sha256((role+"\0"+identity+"\0"+content).encode("utf8")).hexdigest()
                if con.execute("SELECT 1 FROM archived WHERE fingerprint=?",(fingerprint,)).fetchone():
                    continue
                row={"position":position,"role":role,"message_id":message_id or None,
                     "text":content,"url":url,"captured_at":__import__("datetime").datetime.now(
                         __import__("datetime").timezone.utc).isoformat()}
                if dest.stat().st_size>=32*1024**3:
                    raise RuntimeError("Conversation archive reached the 32 GiB storage allowance.")
                output.write(json.dumps(row,ensure_ascii=False)+"\n")
                con.execute("INSERT INTO archived VALUES (?,?,?)",(fingerprint,role,position))
                new+=1
            output.flush();os.fsync(output.fileno());con.commit()
    con.close()
    return {"ok":True,"jsonl":str(dest),"index":str(index_path),
        "new_messages":new,"currently_loaded":total,
        "entire_conversation_verified":False,
        "warning":"Only currently loaded DOM turns are captured. Historical/virtualized turns may not be loaded; a full-chat audit is required." if not incomplete else "Capture had an incomplete chunk or message limit. Retry after loading more content."}


def browser_shared_pdf(name: str = "chatgpt") -> dict:
    """Create a searchable text PDF from the archived JSONL without visiting the UI."""
    root=_export_root()
    slug=_slug(name)
    src=root/(slug+".jsonl")
    if not src.is_file():raise FileNotFoundError(str(src))
    messages_found=0
    content_chars=0
    roles=set()
    origins=set()
    with src.open("r",encoding="utf8") as check:
        for raw in check:
            if not raw.strip():continue
            row=json.loads(raw)
            if not isinstance(row,dict):raise ValueError("Archive row must be a record")
            messages_found+=1
            roles.add(str(row.get("role") or ""))
            origins.add(str(row.get("url") or ""))
            content_chars+=len(str(row.get("text") or "").strip())
    if messages_found<2 or not {"user","assistant"}.issubset(roles) or content_chars==0:
        raise RuntimeError("PDF refused: no usable user/assistant conversation messages in JSONL; existing PDF left untouched.")
    if len(origins)!=1 or not _valid_chat_url(next(iter(origins))):
        raise RuntimeError("PDF refused: archive has mixed or invalid conversation URLs.")
    vendor=_root()/"tools/shared_browser/vendor"
    import sys
    if vendor.exists():sys.path.insert(0,str(vendor))
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font="Helvetica"
    fontfile=Path(r"C:\Windows\Fonts\segoeui.ttf")
    if fontfile.exists():
        pdfmetrics.registerFont(TTFont("SegoeUI-Norm",str(fontfile)))
        font="SegoeUI-Norm"
    dst=root/(slug+".pdf")
    pending=root/(slug+".partial.pdf")
    doc=canvas.Canvas(str(pending),pagesize=letter,pageCompression=1)
    width,height=letter;left=46;right=46;top=54;bottom=50
    y=height-top;pages=1;messages=0
    def nextpage():
        nonlocal y,pages
        doc.showPage();pages+=1;y=height-top
    def line(t,size=9,leading=13):
        nonlocal y
        if y<bottom+leading:nextpage()
        doc.setFont(font,size)
        doc.drawString(left,y,t);y-=leading
    def wrap(text,size=9):
        words=text.expandtabs(4).split(" ")
        limit=width-left-right;buffer=""
        for word in words:
            trial=buffer+" "+word if buffer else word
            if pdfmetrics.stringWidth(trial,font,size)<=limit:
                buffer=trial;continue
            if buffer:line(buffer,size)
            buffer=""
            # split long code tokens at character boundaries, no overflow
            for c in word:
                if pdfmetrics.stringWidth(buffer+c,font,size)>limit:
                    line(buffer,size);buffer=""
                buffer+=c
        if buffer:line(buffer,size)
    line("Norm conversation archive: "+slug,size=14,leading=24)
    with src.open("r",encoding="utf8") as handle:
        for raw in handle:
            row=json.loads(raw)
            messages+=1
            y-=9
            line(str(row.get("role","unknown")).upper()+"  #"+str(row.get("position","?")),size=10,leading=16)
            for raw_line in str(row.get("text","")).splitlines() or [""]:
                wrap(raw_line or " ",size=9)
    doc.save()
    if messages!=messages_found:
        pending.unlink(missing_ok=True)
        raise RuntimeError("Source JSONL changed during PDF creation; output not published.")
    os.replace(pending,dst)
    return {"ok":True,"pdf":str(dst),"jsonl":str(src),"messages":messages,
            "pages":pages,"content_chars":content_chars,
            "status":"partial_unverified","entire_conversation_verified":False,
            "warning":"This confirms only the archived messages were rendered, not that the full ChatGPT conversation was captured."}


def browser_shared_visual_pdf(name: str = "") -> dict:
    """Start high-fidelity visual PDF capture in the *shared* Chromium tab.

    Optional name overrides the suggested filename. When empty, the helper
    examines recent user turns for a filename. Work continues asynchronously.
    """
    return _rpc("visual_pdf_start", name=str(name or "")[:90])


def browser_shared_visual_status() -> dict:
    """Get visual capture progress, page count, output paths, or failure reason."""
    return _rpc("visual_pdf_status")


def browser_shared_visual_cancel() -> dict:
    """Cancel an active visual capture without overwriting past exports."""
    return _rpc("visual_pdf_cancel")


def browser_shared_visual_title_context(last_user: int = 3) -> dict:
    """Get recent loaded USER lines for an LLM-generated export title."""
    state=_rpc("messages",start=0,count=1)
    url=str(state.get("url") or "")
    if not _valid_chat_url(url):
        raise RuntimeError("Open a ChatGPT conversation first")
    total=int(state.get("total_loaded") or 0)
    tail=_rpc("messages",start=max(0,total-40),count=40)
    prompts=[str(x.get("text") or "").strip()[:320]
             for x in tail.get("messages",[])
             if x.get("role")=="user" and str(x.get("text") or "").strip()]
    return {"url":url,"title":str(state.get("title") or ""),
            "recent_user_messages":prompts[-max(1,min(6,int(last_user))):],
            "loaded_messages":total,"entire_conversation_verified":False}


# PDF export-format service: a single location for text, visual and rich-dialogue.
def _pdf_engine():
    """Lazy, private native-vector formatter; shared-browser process stays unchanged."""
    import importlib.util
    import sys
    path = _root()/"tools"/"shared_browser"/"rich_dialogue_pdf.py"
    if not path.is_file():
        raise FileNotFoundError(f"PDF dialogue formatter is missing: {path}")
    key = "_norm_shared_browser_rich_pdf"
    module = sys.modules.get(key)
    if module is None:
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(key,None)
            raise
    return module


def _pdf_file(path: str, *, mode: str) -> Path:
    """Use Norm's configured file-access roots instead of unrestricted absolute paths."""
    import sys
    core=str(_root()/"core")
    if core not in sys.path:sys.path.insert(0,core)
    from norm_runtime.file_access_policy import load_file_access_policy,authorize_path
    policy=load_file_access_policy(_root(),capability="shared_browser")
    roots=policy.read_roots if mode=="read" else policy.write_roots
    return authorize_path(path,roots,access=mode,hardlock=True)


def _pdf_title(value: str) -> str:
    import re
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+',' ',str(value or ''))
    title = ' '.join(title.split()).strip(' .')[:95]
    return title or 'Conversation'


def browser_shared_pdf_title_context(source: str) -> dict:
    """Inspect an existing ChatGPT Exporter PDF for filename and user-turn context."""
    path=_pdf_file(source,mode="read")
    if not path.is_file():raise FileNotFoundError(str(path))
    return _pdf_engine().extract_title_context(path)


def browser_shared_pdf_export(format: str = "text", name: str = "",
                              source: str = "", output: str = "",
                              include_original: bool = False) -> dict:
    """Export PDF by format: text (JSONL archive), visual (active browser),
    dialogue (reflow existing ChatGPT Exporter PDF without losing native style).

    Text and visual remain compatible with their existing export functions.
    Dialogue is source-PDF based, and does NOT silently take a screenshot.
    """
    choice=str(format).strip().lower()
    if choice=="text":
        if source or output or include_original:
            raise ValueError('text format accepts archive name only; use dialogue for source PDFs')
        archive=_slug(name or 'chatgpt')
        if (_export_root()/(archive+'.pdf')).exists():
            raise FileExistsError('Existing text PDF preserved; choose a new archive name')
        return browser_shared_pdf(name or 'chatgpt')
    if choice=="visual":
        if source or output or include_original:
            raise ValueError('visual capture uses active browser; no source/output path arguments')
        return browser_shared_visual_pdf(name)
    if choice!="dialogue":
        raise ValueError('PDF format must be text, visual, or dialogue')
    if not source:raise ValueError('dialogue format requires --source original ChatGPT Exporter PDF')
    inp=_pdf_file(source,mode="read")
    if not inp.is_file():raise FileNotFoundError(str(inp))
    engine=_pdf_engine()
    title=_pdf_title(name or engine.source_title(inp))
    target=(Path(output) if output else inp.with_name(_pdf_title(name or engine.source_title(inp))+' - Rich Dialogue.pdf'))
    target=_pdf_file(str(target),mode="write")
    if target.exists():raise FileExistsError(f'Existing PDF preserved: {target}')
    report=target.with_suffix('.verification.json')
    if report.exists():raise FileExistsError(f'Existing verification report preserved: {report}')
    if target.suffix.lower()!='.pdf':raise ValueError('Output must have .pdf extension')
    source_hash=hashlib.sha256(inp.read_bytes()).hexdigest()
    result=engine.create(inp,target,title=title,include_original=bool(include_original))
    output_hash=hashlib.sha256(target.read_bytes()).hexdigest()
    metadata={**result,'source_sha256':source_hash,'output_sha256':output_hash,
              'verified_at':__import__('datetime').datetime.now().astimezone().isoformat(),
              'title':title,'naming_rule':'{title} - Rich Dialogue.pdf',
              'verification_method':'source region text-token coverage, native image SHA256, outline navigation'}
    pending=report.with_name(report.name+'.writing')
    if pending.exists():raise FileExistsError(f'Existing temporary report preserved: {pending}')
    try:
        with pending.open('x',encoding='utf8') as handle:
            json.dump(metadata,handle,ensure_ascii=False,indent=2)
            handle.write('\n');handle.flush();os.fsync(handle.fileno())
        if report.exists():raise FileExistsError(f'Existing report preserved: {report}')
        os.rename(pending,report)
    finally:
        pending.unlink(missing_ok=True)
    return {**result,'verification_report':str(report),'title':title,
            'source_sha256':source_hash,'output_sha256':output_hash}


def browser_shared_pdf_verify(source: str, output: str) -> dict:
    """Independently compare an existing rich-dialogue PDF against its source."""
    inp=_pdf_file(source,mode="read")
    target=_pdf_file(output,mode="read")
    if not inp.is_file() or not target.is_file():raise FileNotFoundError('Source and output PDFs must exist')
    report=target.with_suffix('.verification.json')
    checked={}
    if report.is_file():
        checked=json.loads(report.read_text(encoding='utf-8'))
        if checked.get('source_sha256')!=hashlib.sha256(inp.read_bytes()).hexdigest():
            raise ValueError('Source changed since the recorded PDF creation')
    result=_pdf_engine().audit(inp,target,
        reading_pages=int(checked['reading_pages']) if 'reading_pages' in checked else None,
        include_original=bool(checked.get('original_source_appended',False)))
    if checked and checked.get('output_sha256')!=hashlib.sha256(target.read_bytes()).hexdigest():
        result['issues'].append('Output changed since the recorded verification')
    result['passed']=not result['issues']
    return {**result,'source':str(inp),'pdf':str(target),
            'recorded_verification':bool(checked)}


def browser_shared_pdf_cleanup(directory: str = "", dry_run: bool = True,
                               older_than_hours: int = 24) -> dict:
    """Cleanup ONLY stale formatter *.partial-PID.pdf files; dry run by default."""
    import re
    import time
    base=_pdf_file(directory or str(_export_root()),mode="write")
    if not base.is_dir():raise NotADirectoryError(str(base))
    hours=int(older_than_hours)
    if not 1<=hours<=8760:raise ValueError('Age threshold must be 1 to 8760 hours')
    pattern=re.compile(r'.+\.partial-\d+\.pdf\Z',re.I)
    matches=[]
    for candidate in base.iterdir():
        if candidate.is_symlink() or not candidate.is_file() or not pattern.fullmatch(candidate.name):continue
        if time.time()-candidate.stat().st_mtime < hours*3600:continue
        matches.append(str(candidate))
    removed=[]
    if not dry_run:
        for name in matches:
            file=Path(name)
            if not file.is_symlink() and file.is_file() and pattern.fullmatch(file.name):
                file.unlink()
                removed.append(name)
    return {'directory':str(base),'dry_run':bool(dry_run),
            'candidates':matches,'removed':removed,
            'preserved':'source PDFs, completed PDF exports, verification reports, settings, plugins and databases'}
