from __future__ import annotations

import configparser
import json
import tempfile
from pathlib import Path
from typing import Literal


def _runtime_root() -> Path:
    # .../Norm/plugins/vision_parse/src/main.py -> .../Norm
    return Path(__file__).resolve().parents[3]


def _ollama_settings() -> tuple[str, str]:
    root = _runtime_root()
    parser = configparser.ConfigParser()
    parser.read(root / "config" / "settings.ini", encoding="utf-8-sig")
    host = parser.get("network", "ollama_host", fallback="loopback").strip() or "loopback"
    port = parser.getint("network", "ollama_port", fallback=11434)
    if host.lower() in {"loopback", "localhost", "local"}:
        host = "127.0.0.1"
    elif host.lower() == "current":
        host = parser.get("network", "current_machine", fallback="127.0.0.1").strip() or "127.0.0.1"
    runtime_cfg = {}
    try:
        runtime_cfg = json.loads((root / "config" / "runtime.json").read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    model = str((runtime_cfg.get("ollama") or {}).get("model") or "norm").strip() or "norm"
    return f"http://{host}:{port}", model


def _vision_client():
    # Reuse Norm's client rather than owning a second HTTP implementation. This keeps
    # global cancellation and the degenerate-output guard effective for plugin calls.
    from norm_runtime.ollama_client import OllamaClient

    base_url, model = _ollama_settings()
    return OllamaClient(base_url, model=model, timeout_seconds=86400, activity_source="plugin:vision_parse")


def vision_parse(
    path: str,
    start_page: int = 1,
    end_page: int = 0,
    mode: Literal["hybrid", "vision", "text"] = "hybrid",
    prompt: str = "",
) -> dict:
    """Read up to four PDF pages with rendered-page AI vision; visible page evidence overrides a corrupt text layer."""
    pdf_path = Path(path).expanduser()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("vision_parse supports PDF files only")
    try:
        import pymupdf  # type: ignore
    except Exception as exc:
        raise RuntimeError("vision_parse requires PyMuPDF in the Norm environment") from exc

    mode = str(mode or "hybrid").strip().lower()
    if mode not in {"hybrid", "vision", "text"}:
        raise ValueError("mode must be hybrid, vision, or text")
    start_page = max(1, int(start_page or 1))
    user_focus = str(prompt or "").strip()
    max_total_chars = 393_216

    document = pymupdf.open(str(pdf_path))
    try:
        page_count = int(document.page_count)
        if page_count <= 0:
            raise ValueError("PDF has no pages")
        if start_page > page_count:
            raise ValueError(f"start_page {start_page} exceeds PDF page count {page_count}")
        requested_end = int(end_page or min(page_count, start_page + 3))
        resolved_end = min(page_count, max(start_page, requested_end), start_page + 3)
        page_slots = max(1, resolved_end - start_page + 1)
        max_chars_each = max(4000, max_total_chars // page_slots - 1500)
        pages: list[dict] = []
        client = None if mode == "text" else _vision_client()

        with tempfile.TemporaryDirectory(prefix="norm-vision-parse-") as td:
            scratch = Path(td)
            for page_number in range(start_page, resolved_end + 1):
                page = document.load_page(page_number - 1)
                native_text = str(page.get_text("text", sort=True) or "").strip()
                replacement_count = native_text.count("\ufffd")
                control_count = sum(1 for ch in native_text if ord(ch) < 32 and ch not in "\n\r\t")
                quality = "good"
                if not native_text:
                    quality = "empty"
                elif replacement_count or control_count:
                    quality = "suspect"

                if mode == "text":
                    reading = native_text
                    rendered = False
                else:
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), alpha=False)
                    image_path = scratch / f"page-{page_number:05d}.png"
                    pix.save(str(image_path))
                    rendered = True
                    draft = native_text[:14000]
                    focus = user_focus or (
                        "Faithfully read the page. Preserve exact names, dates, places, occupations, headings, "
                        "relationships, and meaningful prose. Add concise semantic notes only where useful."
                    )
                    vision_prompt = (
                        f"Read PDF page {page_number} of {page_count}. The rendered page image is authoritative. "
                        "The native PDF text-layer draft below is only an UNTRUSTED hint; it may contain bad encoding, "
                        "column-order errors, dropped characters, or OCR-like corruption. Correct text only when the "
                        "visible page supports the correction. NEVER invent alternate spellings, typo-regex variants, "
                        "names, dates, words, or facts merely to make a search succeed. If text is genuinely unreadable, "
                        "mark it [uncertain]. Preserve the visible source meaning rather than mechanically echoing corrupt extraction.\n\n"
                        f"TASK FOCUS:\n{focus}\n\n"
                        "Return plain UTF-8 text under these headings:\nTRANSCRIPTION\n...\nSEMANTIC NOTES\n...\n\n"
                        f"NATIVE TEXT-LAYER DRAFT (untrusted):\n{draft if draft else '[no usable text layer]'}"
                    )
                    reading = str(client.vision(vision_prompt, [str(image_path)], think=False, temperature=0.0)).strip()

                if len(reading) > max_chars_each:
                    reading = reading[: max_chars_each - 120] + "\n[vision_parse page output truncated; request this page alone for more]"
                pages.append({
                    "page": page_number,
                    "text_layer_chars": len(native_text),
                    "text_layer_quality": quality,
                    "rendered_for_vision": rendered,
                    "reading": reading,
                })
    finally:
        document.close()

    return {
        "path": str(pdf_path),
        "page_count": page_count,
        "start_page": start_page,
        "end_page": resolved_end,
        "pages": pages,
        "next_page": (resolved_end + 1) if resolved_end < page_count else None,
        "complete": resolved_end >= page_count,
        "mode": mode,
        "method": "vision_parse plugin: PyMuPDF text-layer + rendered-page Ollama vision" if mode == "hybrid" else mode,
    }
