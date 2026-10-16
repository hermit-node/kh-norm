from __future__ import annotations

import configparser
import hashlib
import json
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal


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


_CACHE_MAX_PAGES = 128
_PAGE_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _cache_get(key: str) -> dict[str, Any] | None:
    value = _PAGE_CACHE.get(key)
    if value is None:
        return None
    _PAGE_CACHE.move_to_end(key)
    return dict(value)


def _cache_put(key: str, value: dict[str, Any]) -> None:
    _PAGE_CACHE[key] = dict(value)
    _PAGE_CACHE.move_to_end(key)
    while len(_PAGE_CACHE) > _CACHE_MAX_PAGES:
        _PAGE_CACHE.popitem(last=False)


def _page_cache_key(
    pdf_path: Path,
    page_number: int,
    mode: str,
    user_focus: str,
    model: str,
) -> str:
    stat = pdf_path.stat()
    raw = "\n".join([
        str(pdf_path.resolve()).lower(),
        str(stat.st_size),
        str(stat.st_mtime_ns),
        str(page_number),
        str(mode),
        str(model),
        user_focus,
        "vision-parse-adaptive-v2",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _output_budget(native_chars: int, *, split_parts: int = 1) -> int:
    if native_chars <= 0:
        return 4096
    per_part_chars = max(1, int(native_chars) // max(1, int(split_parts)))
    # Generous headroom: num_predict is a ceiling, so completed pages stop early.
    return max(2048, min(8192, int(per_part_chars / 2.6) + 1000))


def _vision_call(
    client,
    prompt: str,
    image_path: Path,
    *,
    temperature: float,
    num_predict: int,
) -> dict[str, Any]:
    if hasattr(client, "vision_result"):
        result = client.vision_result(
            prompt,
            [str(image_path)],
            think=False,
            temperature=temperature,
            num_predict=num_predict,
        )
        return {
            "content": str((result or {}).get("content") or "").strip(),
            "done_reason": str((result or {}).get("done_reason") or ""),
            "eval_count": int((result or {}).get("eval_count") or 0),
        }
    try:
        content = client.vision(
            prompt,
            [str(image_path)],
            think=False,
            temperature=temperature,
            num_predict=num_predict,
        )
    except TypeError as exc:
        if "num_predict" not in str(exc):
            raise
        content = client.vision(
            prompt,
            [str(image_path)],
            think=False,
            temperature=temperature,
        )
    return {
        "content": str(content).strip(),
        "done_reason": "",
        "eval_count": 0,
    }


def _vision_with_repeat_retry(
    client,
    prompt: str,
    image_path: Path,
    *,
    num_predict: int,
) -> dict[str, Any]:
    """Retry one crop once when Ollama aborts a degenerate repetition loop."""
    try:
        result = _vision_call(
            client,
            prompt,
            image_path,
            temperature=0.0,
            num_predict=num_predict,
        )
        result["retries"] = 0
        return result
    except RuntimeError as exc:
        if "token repeat limit reached" not in str(exc).lower():
            raise
        retry_prompt = (
            prompt
            + "\n\nRETRY NOTE: The prior transcription attempt fell into a repetition loop. "
            "Read this image again in normal document order. Never repeat a word, line, heading, "
            "or passage merely to fill output. Stop when the visible source ends."
        )
        result = _vision_call(
            client,
            retry_prompt,
            image_path,
            temperature=0.1,
            num_predict=num_predict,
        )
        result["retries"] = 1
        return result


def _layout_strategy(page, native_text: str) -> str:
    """Return full, columns, or horizontal for dense pages."""
    native_chars = len(native_text)
    if native_chars < 7000:
        return "full"
    width = float(page.rect.width)

    line_centers: list[float] = []
    try:
        layout = page.get_text("dict") or {}
        for block in layout.get("blocks") or []:
            for line in block.get("lines") or []:
                bbox = line.get("bbox") or ()
                spans = line.get("spans") or []
                line_text = "".join(str(span.get("text") or "") for span in spans).strip()
                if len(bbox) < 4 or not line_text:
                    continue
                x0, _y0, x1, _y1 = map(float, bbox[:4])
                line_width = x1 - x0
                if line_width <= 0 or line_width > width * 0.72:
                    continue
                line_centers.append((x0 + x1) / 2.0)
    except Exception:
        line_centers = []

    if line_centers:
        left = sum(1 for center in line_centers if center < width * 0.47)
        right = sum(1 for center in line_centers if center > width * 0.53)
        if left >= 5 and right >= 5 and (left + right) >= max(10, int(len(line_centers) * 0.68)):
            return "columns"

    # Fallback for PDFs whose line structure was flattened: look for an empty central
    # gutter in individual word positions.
    try:
        words = page.get_text("words") or []
    except Exception:
        words = []
    if len(words) >= 40:
        centers = [((float(word[0]) + float(word[2])) / 2.0) for word in words if len(word) >= 5 and str(word[4]).strip()]
        left = sum(1 for center in centers if center < width * 0.47)
        right = sum(1 for center in centers if center > width * 0.53)
        middle = max(0, len(centers) - left - right)
        if left >= 20 and right >= 20 and middle <= max(4, int(len(centers) * 0.05)):
            return "columns"

    if native_chars >= 11000:
        return "horizontal"
    return "full"


def _render_scale(native_text: str, quality: str, strategy: str) -> float:
    # Keep easy pages cheap without dropping all the way to a 72-DPI (1.0x)
    # full-page raster, where ordinary body text becomes needlessly fragile for vision.
    # Raster area scales quadratically: 1.5x uses less than half the pixels of 2.2x.
    if strategy != "full" or quality != "good" or len(native_text) >= 8000:
        return 2.9
    if len(native_text) < 5000:
        return 1.5
    return 2.2


def _render_crop(page, scratch: Path, page_number: int, label: str, scale: float, clip=None) -> Path:
    pix = page.get_pixmap(
        dpi=max(96, int(round(72.0 * float(scale)))),
        alpha=False,
        clip=clip,
    )
    image_path = scratch / f"page-{page_number:05d}-{label}.png"
    pix.save(str(image_path))
    return image_path


def _vision_prompt(
    *,
    page_number: int,
    page_count: int,
    focus: str,
    native_draft: str,
    crop_label: str = "",
) -> str:
    crop_note = (
        f" This image is the {crop_label} crop of that page. Transcribe ONLY text visible in this crop; "
        "do not infer or repeat text outside the crop."
        if crop_label
        else ""
    )
    return (
        f"Read PDF page {page_number} of {page_count}.{crop_note} The rendered image is authoritative. "
        "The native PDF text-layer draft below is only an UNTRUSTED hint; it may contain bad encoding, "
        "column-order errors, dropped characters, or OCR-like corruption. Correct text only when the "
        "visible image supports the correction. NEVER invent alternate spellings, typo-regex variants, "
        "names, dates, words, or facts merely to make a search succeed. If text is genuinely unreadable, "
        "mark it [uncertain]. Preserve the visible source meaning rather than mechanically echoing corrupt extraction.\n\n"
        f"TASK FOCUS:\n{focus}\n\n"
        "Return plain UTF-8 text under these headings:\nTRANSCRIPTION\n...\nSEMANTIC NOTES\n...\n\n"
        f"NATIVE TEXT-LAYER DRAFT (untrusted):\n{native_draft if native_draft else '[no usable text layer]'}"
    )


def _split_specs(page, strategy: str) -> list[tuple[str, Any]]:
    rect = page.rect
    rect_type = rect.__class__
    if strategy == "columns":
        middle = (float(rect.x0) + float(rect.x1)) / 2.0
        return [
            ("left column", rect_type(rect.x0, rect.y0, middle, rect.y1)),
            ("right column", rect_type(middle, rect.y0, rect.x1, rect.y1)),
        ]
    middle = (float(rect.y0) + float(rect.y1)) / 2.0
    return [
        ("top half", rect_type(rect.x0, rect.y0, rect.x1, middle)),
        ("bottom half", rect_type(rect.x0, middle, rect.x1, rect.y1)),
    ]


def _read_vision_page(
    *,
    page,
    page_number: int,
    page_count: int,
    native_text: str,
    quality: str,
    focus: str,
    scratch: Path,
    client,
) -> dict[str, Any]:
    strategy = _layout_strategy(page, native_text)
    scale = _render_scale(native_text, quality, strategy)
    retries = 0
    calls = 0
    eval_count = 0
    done_reasons: list[str] = []
    length_fallback = False

    def run_one(label: str, clip, draft: str, *, parts: int) -> dict[str, Any]:
        nonlocal retries, calls, eval_count
        image_path = _render_crop(
            page,
            scratch,
            page_number,
            label.replace(" ", "-"),
            scale,
            clip=clip,
        )
        prompt = _vision_prompt(
            page_number=page_number,
            page_count=page_count,
            focus=focus,
            native_draft=draft[:10000],
            crop_label="" if label == "full page" else label,
        )
        result = _vision_with_repeat_retry(
            client,
            prompt,
            image_path,
            num_predict=_output_budget(len(draft) or len(native_text), split_parts=parts),
        )
        calls += 1 + int(result.get("retries") or 0)
        retries += int(result.get("retries") or 0)
        eval_count += int(result.get("eval_count") or 0)
        done_reasons.append(str(result.get("done_reason") or ""))
        return result

    if strategy == "full":
        try:
            result = run_one("full page", None, native_text, parts=1)
            if str(result.get("done_reason") or "").lower() != "length":
                return {
                    "reading": str(result.get("content") or "").strip(),
                    "vision_retries": retries,
                    "vision_calls": calls,
                    "vision_done_reasons": done_reasons,
                    "vision_eval_count": eval_count,
                    "render_strategy": "full",
                    "render_scale": scale,
                    "length_fallback": False,
                }
            length_fallback = True
        except RuntimeError as exc:
            if "token repeat limit reached" not in str(exc).lower():
                raise
            # Two failed repetition attempts on the full page: reduce visual/output load.
            length_fallback = True
        strategy = "horizontal"
        scale = max(scale, 2.9)

    pieces: list[str] = []
    specs = _split_specs(page, strategy)
    for label, clip in specs:
        crop_text = str(page.get_text("text", sort=True, clip=clip) or "").strip()
        result = run_one(label, clip, crop_text, parts=len(specs))
        content = str(result.get("content") or "").strip()
        if str(result.get("done_reason") or "").lower() == "length":
            content += "\n[vision_parse crop reached its model output limit; request this page alone if exact tail text is required]"
        pieces.append(f"[{label.upper()}]\n{content}")

    return {
        "reading": "\n\n".join(pieces).strip(),
        "vision_retries": retries,
        "vision_calls": calls,
        "vision_done_reasons": done_reasons,
        "vision_eval_count": eval_count,
        "render_strategy": strategy,
        "render_scale": scale,
        "length_fallback": length_fallback,
    }


def vision_parse(
    path: str,
    start_page: int = 1,
    end_page: int = 0,
    mode: Literal["hybrid", "vision", "text"] = "hybrid",
    prompt: str = "",
) -> dict:
    """Read up to ten PDF pages with rendered-page AI vision; visible page evidence overrides a corrupt text layer."""
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
        requested_end = int(end_page or min(page_count, start_page + 9))
        resolved_end = min(page_count, max(start_page, requested_end), start_page + 9)
        page_slots = max(1, resolved_end - start_page + 1)
        max_chars_each = max(4000, max_total_chars // page_slots - 1500)
        pages: list[dict] = []
        vision_model = ""
        if mode == "text":
            client = None
        else:
            _base_url, vision_model = _ollama_settings()
            client = _vision_client()

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

                cache_hit = False
                if mode == "text":
                    page_result = {
                        "reading": native_text,
                        "vision_retries": 0,
                        "vision_calls": 0,
                        "vision_done_reasons": [],
                        "vision_eval_count": 0,
                        "render_strategy": "text-layer-only",
                        "render_scale": 0.0,
                        "length_fallback": False,
                    }
                    rendered = False
                else:
                    focus = user_focus or (
                        "Faithfully read the page. Preserve exact names, dates, places, occupations, headings, "
                        "relationships, and meaningful prose. Add concise semantic notes only where useful."
                    )
                    cache_key = _page_cache_key(
                        pdf_path,
                        page_number,
                        mode,
                        focus,
                        vision_model,
                    )
                    cached = _cache_get(cache_key)
                    if cached is not None:
                        page_result = cached
                        page_result["cache_source_vision_calls"] = int(page_result.get("vision_calls") or 0)
                        page_result["vision_calls"] = 0
                        page_result["vision_retries"] = 0
                        page_result["vision_eval_count"] = 0
                        cache_hit = True
                    else:
                        page_result = _read_vision_page(
                            page=page,
                            page_number=page_number,
                            page_count=page_count,
                            native_text=native_text,
                            quality=quality,
                            focus=focus,
                            scratch=scratch,
                            client=client,
                        )
                        _cache_put(cache_key, page_result)
                    rendered = True

                reading = str(page_result.get("reading") or "")
                if len(reading) > max_chars_each:
                    reading = reading[: max_chars_each - 120] + "\n[vision_parse page output truncated by tool return budget; request this page alone for more]"
                pages.append({
                    "page": page_number,
                    "text_layer_chars": len(native_text),
                    "text_layer_quality": quality,
                    "rendered_for_vision": rendered,
                    "cache_hit": cache_hit,
                    "render_strategy": str(page_result.get("render_strategy") or ""),
                    "render_scale": float(page_result.get("render_scale") or 0.0),
                    "vision_calls": int(page_result.get("vision_calls") or 0),
                    "cache_source_vision_calls": int(page_result.get("cache_source_vision_calls") or 0),
                    "vision_retries": int(page_result.get("vision_retries") or 0),
                    "vision_done_reasons": list(page_result.get("vision_done_reasons") or []),
                    "vision_eval_count": int(page_result.get("vision_eval_count") or 0),
                    "length_fallback": bool(page_result.get("length_fallback", False)),
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
        "vision_calls": sum(int(item.get("vision_calls") or 0) for item in pages),
        "cache_hits": sum(1 for item in pages if bool(item.get("cache_hit"))),
        "method": (
            "vision_parse plugin: adaptive PyMuPDF text-layer + rendered-page Ollama vision "
            "(layout-aware dense-page crops, truncation detection, in-process page cache)"
            if mode == "hybrid"
            else mode
        ),
    }
