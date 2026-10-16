"""Offline archive -> searchable PDF test; never logs into websites."""
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util,sys,json
root=Path(__file__).resolve().parents[1]
p=root/"plugins/shared_browser/src/main.py"
spec=importlib.util.spec_from_file_location("shared_browser_test",p)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
with TemporaryDirectory(prefix="norm-browser-r9-") as d:
    tmp=Path(d)
    (tmp/"tools/shared_browser").mkdir(parents=True)
    mod._root=lambda:tmp
    mod._export_root=lambda:tmp/"exports"
    text="Long text " * 2500
    cache={}
    def rpc(action,**kwargs):
        if action=="messages":
            start=int(kwargs.get("start") or 0)
            limit=int(kwargs.get("count") or 20)
            messages=[
                {"position":0,"role":"user","message_id":"a","text":"Hello","text_length":5},
                {"position":1,"role":"assistant","message_id":"b",
                 "text":text[:16000],"text_length":len(text)},
            ]
            return {"url":"https://chatgpt.com/c/test","total_loaded":2,
                    "messages":messages[start:start+limit]}
        if action=="message_piece":
            return {"found":True,"text":text[int(kwargs["start"]):int(kwargs["start"])+int(kwargs["length"])]}
        if action=="html":return {"url":"https://example.test","html":"<main><div class='test'>Sample</div></main>"}
        raise AssertionError(action)
    mod._rpc=rpc
    a=mod.browser_shared_archive_loaded("testing")
    assert a["new_messages"]==2,a
    assert a["currently_loaded"]==2
    b=mod.browser_shared_archive_loaded("testing")
    assert b["new_messages"]==0,b
    rows=[json.loads(x) for x in Path(a["jsonl"]).read_text("utf8").splitlines()]
    assert rows[1]["text"]==text
    assert mod.browser_shared_bs_select(".test")["items"][0]["text"]=="Sample"
    pdf=mod.browser_shared_pdf("testing")
    assert pdf["messages"]==2
    assert Path(pdf["pdf"]).read_bytes()[:5]==b"%PDF-"
    import re
    assert len(Path(pdf["pdf"]).read_bytes())>2000
    print("PASS Qt DOM archive chunks, sqlite dedup, BeautifulSoup/SoupSieve CSS, searchable PDF")
