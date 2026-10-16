"""r12 regression: PDF exporter actually renders source messages using ReportLab."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/shared_browser/src/main.py"
spec = importlib.util.spec_from_file_location("norm_r12_shared_browser_smoke", PLUGIN)
assert spec and spec.loader
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

with TemporaryDirectory(prefix="norm-r12-reportlab-") as td:
    root = Path(td)
    plugin._export_root = lambda: root
    url = "https://chatgpt.com/c/r12-smoke"
    rows = [
      {"position":1,"role":"user","message_id":"u","text":"R12 UNIQUE USER SOURCE 4167","url":url},
      {"position":2,"role":"assistant","message_id":"a","text":"R12 UNIQUE ASSISTANT SOURCE 9784 " + "PDF content page. " * 1200,"url":url}
    ]
    (root / "r12check.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    output = plugin.browser_shared_pdf("r12check")
    assert output["ok"] and output["messages"] == 2 and output["content_chars"] >= 300
    assert output["status"] == "partial_unverified"
    pdf = Path(output["pdf"])
    assert pdf.read_bytes().startswith(b"%PDF-") and pdf.stat().st_size > 1500
    with pymupdf.open(pdf) as doc:
        text = "\n".join(page.get_text() for page in doc)
        assert len(doc) >= 2, len(doc)
        assert "R12 UNIQUE USER SOURCE 4167" in text, text[:800]
        assert "R12 UNIQUE ASSISTANT SOURCE 9784" in text, text[:800]
    print("PASS r12 ReportLab PDF; 2 messages, multi-page, searchable source strings verified")
