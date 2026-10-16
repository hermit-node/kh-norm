"""Prevent false PDF success and fake ChatGPT extraction on an empty tab."""
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import json
import sys
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("r11_browser_plugin",root/"plugins/shared_browser/src/main.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
with TemporaryDirectory(prefix="norm-r11-pdf-") as tmp:
    dest=Path(tmp)/"exports"
    module._export_root=lambda:dest
    module._rpc=lambda action,**kw:{"url":"about:blank","total_loaded":0,"messages":[]}
    try: module.browser_shared_archive_loaded("invalid")
    except RuntimeError as exc:assert "ChatGPT conversation" in str(exc),str(exc)
    else:raise AssertionError("Archive accepted blank browser page")
    assert not dest.exists()
    dest.mkdir(parents=True)
    (dest/"chatgpt.jsonl").write_text("",encoding="utf8")
    pdf=dest/"chatgpt.pdf"
    pdf.write_bytes(b"PREEXISTING VALID FILE")
    try:module.browser_shared_pdf("chatgpt")
    except RuntimeError as exc:assert "PDF refused" in str(exc),str(exc)
    else:raise AssertionError("PDF accepted empty JSONL archive")
    assert pdf.read_bytes()==b"PREEXISTING VALID FILE"
    print("PASS blank browser and empty archive cannot produce success/overwrite")
    (dest/"chatgpt.jsonl").write_text(
        json.dumps({"role":"user","url":"https://chatgpt.com/c/one","text":"Hello"})+"\n"+
        json.dumps({"role":"assistant","url":"https://chatgpt.com/c/two","text":"Hello"})+"\n",
        encoding="utf8")
    try:module.browser_shared_pdf("chatgpt")
    except RuntimeError as exc:assert "mixed" in str(exc).lower(),str(exc)
    else:raise AssertionError("PDF accepted mixed conversations")
    assert pdf.read_bytes()==b"PREEXISTING VALID FILE"
    print("PASS mixed-conversation archive cannot silently create success")
    assert not module._valid_chat_url("about:blank")
    assert not module._valid_chat_url("https://example.com/c/someid")
    assert module._valid_chat_url("https://chatgpt.com/c/abc123")
    print("PASS ChatGPT conversation address validation")
