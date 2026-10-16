"""Direct Norm Prompt slash commands for the verified vision_parse plugin.

The command handler never schedules an N2 task. Only the fixed public reader API
is callable; filesystem reads respect the shared, capability-scoped policy.
"""
from __future__ import annotations

import json
import shlex
import sys
import threading
from pathlib import Path
from typing import Callable


VISION_COMMANDS = frozenset({
    "/vision", "/vision-status", "/vision-parse", "/vision-read",
    "/vision-text", "/vision-force", "/vision-recheck",
})
_PARSE_LOCK = threading.Lock()


def _parts(line: str) -> list[str]:
    # Windows backslashes are literal; remove only matching outer quotes.
    return [word[1:-1] if len(word) >= 2 and word[0] == word[-1] and word[0] in "\"'" else word
            for word in shlex.split(line, posix=False)]


def _plugin(root: Path):
    root = Path(root).resolve()
    folder = root / "plugins" / "vision_parse"
    from norm_runtime.plugin_identity import verify_identity
    metadata = json.loads((folder / "plugin.json").read_text(encoding="utf-8-sig"))
    verify_identity(folder, metadata)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from plugins.vision_parse.src import main
    return main


def _approved_path(root: Path, value: str) -> Path:
    from norm_runtime.file_access_policy import load_file_access_policy, authorize_path
    policy = load_file_access_policy(root, capability="vision_parse")
    return authorize_path(value, policy.read_roots, access="read", hardlock=True)


def _parse_flags(args: list[str]) -> tuple[list[str], dict[str, str | bool]]:
    supported = {"--start", "--end", "--page", "--mode", "--focus", "--suspect", "--full", "--json"}
    simple = {"--full", "--json"}
    items: list[str] = []
    options: dict[str, str | bool] = {}
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item.startswith("--"):
            if item not in supported:
                raise ValueError(f"Unsupported option {item}. See /help vision-parse")
            if item in options:
                raise ValueError(f"Duplicate option {item}")
            if item in simple:
                options[item] = True
            else:
                idx += 1
                if idx >= len(args) or args[idx].startswith("--"):
                    raise ValueError(f"Option {item} requires a value")
                options[item] = args[idx]
        else:
            items.append(item)
        idx += 1
    return items, options


def _page_range(flags: dict[str, str | bool]) -> tuple[int, int]:
    if "--page" in flags and ("--start" in flags or "--end" in flags):
        raise ValueError("--page cannot be combined with --start or --end")
    if "--page" in flags:
        page = int(flags["--page"])
        if page < 1:
            raise ValueError("--page must be >= 1")
        return page, page
    start = int(flags.get("--start", 1))
    end = int(flags.get("--end", 0))
    if start < 1 or end < 0 or (end and end < start):
        raise ValueError("Require --start >= 1 and --end = 0 or --end >= --start")
    if end and end - start >= 10:
        raise ValueError("Maximum ten pages per call; use next_page for the following batch")
    return start, end


def _show_report(result: dict, options: dict[str, str | bool], out: Callable[[str], None]) -> None:
    if options.get("--json"):
        out(json.dumps(result, indent=2, ensure_ascii=False))
        return
    pages = result.get("pages") or []
    out(f"Read {result.get('path')} | mode={result.get('mode')} | "
        f"pages={len(pages)}/{result.get('page_count', '?')} | "
        f"new vision calls={result.get('vision_calls', 0)} | cache hits={result.get('cache_hits', 0)}")
    for page in pages:
        number = page.get("page", "?")
        out(f"--- Page {number}: {page.get('hybrid_decision', page.get('render_strategy', 'unknown'))} "
            f"| OCR={page.get('ocr_status') or 'n/a'} "
            f"| model calls={page.get('vision_calls', 0)} ---")
        coverage = page.get("grayscale_coverage")
        if isinstance(coverage, dict):
            explained = coverage.get("explained_page_fraction")
            ink = coverage.get("explained_ink_fraction")
            if isinstance(explained, (float, int)) and isinstance(ink, (float, int)):
                out(f"Coverage: page={explained:.2%}; visible ink={ink:.2%}")
        if page.get("text_anomalies"):
            out("Anomaly assessment: " + json.dumps(page["text_anomalies"], ensure_ascii=False))
        if page.get("text_repairs"):
            out("Local repairs: " + json.dumps(page["text_repairs"], ensure_ascii=False)[:1200])
        reading = str(page.get("reading") or "")
        # A bounded default prevents a ten-page read flooding the operator console.
        if not options.get("--full") and len(reading) > 4500:
            out(reading[:4500] + "\n[Preview limited to 4500 chars/page; retry with --full or --json]")
        else:
            out(reading or "[No extracted text]")
    if result.get("next_page"):
        out(f"Additional pages available: --start {result['next_page']}")


def _action(line: str, root: Path, out: Callable[[str], None]) -> None:
    tokens = _parts(line)
    command, args = tokens[0].lower(), tokens[1:]
    if command == "/vision":
        if args:
            raise ValueError("Usage: /vision (see /help vision-parse)")
        out("PDF/image reader: /vision-parse, /vision-text, /vision-force, /vision-recheck, /vision-status. See /help vision-parse")
        return
    vision = _plugin(root)
    if command == "/vision-status":
        if args:
            raise ValueError("Usage: /vision-status")
        out("vision_parse plugin source integrity verified; supported: PDF, PNG, JPEG, WebP, BMP, TIFF")
        try:
            import pytesseract
            out(f"Tesseract: {pytesseract.get_tesseract_version()}")
        except Exception as exc:
            out(f"Tesseract unavailable: {type(exc).__name__}: {exc}; hybrid may use Ollama vision")
        return

    items, flags = _parse_flags(args)
    if command == "/vision-recheck":
        if len(items) != 2:
            raise ValueError('Usage: /vision-recheck "PATH" "SUSPECT EXCERPT" [--page N]')
        if "--mode" in flags or "--suspect" in flags:
            raise ValueError("/vision-recheck uses hybrid mode and the quoted excerpt")
        flags["--suspect"] = items[1]
    elif len(items) != 1:
        raise ValueError(f'Usage: {command} "PATH" [--page N] [--mode hybrid|text|vision]')

    forced_mode = {"/vision-text": "text", "/vision-force": "vision", "/vision-recheck": "hybrid"}.get(command)
    selected_mode = str(flags.get("--mode", forced_mode or "hybrid")).lower()
    if selected_mode not in {"hybrid", "vision", "text"}:
        raise ValueError("--mode must be hybrid, vision, or text")
    if forced_mode and selected_mode != forced_mode:
        raise ValueError(f"{command} always uses mode={forced_mode}")
    if selected_mode == "text" and ("--focus" in flags or "--suspect" in flags):
        raise ValueError("--focus and --suspect require hybrid or vision mode")
    start, end = _page_range(flags)
    file = _approved_path(root, items[0])
    if not file.is_file():
        raise FileNotFoundError(file)
    if file.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        raise ValueError("Reader supports PDF, PNG, JPEG, WebP, BMP and TIFF files")
    out(f"Reading {file} (mode={selected_mode}, pages={start}..{end or 'next 10'}, direct plugin call)")
    result = vision.vision_parse(
        str(file), start_page=start, end_page=end, mode=selected_mode,
        prompt=str(flags.get("--focus", "")), suspect_excerpt=str(flags.get("--suspect", "")),
    )
    _show_report(result, flags, out)


def run_vision_command(line: str, root: Path, out: Callable[[str], None] = print) -> bool:
    """Run only allowlisted direct operator commands, without Redis/N2 dispatch."""
    command = line.split(maxsplit=1)[0].lower() if line.strip() else ""
    if command not in VISION_COMMANDS:
        return False
    if command in {"/vision", "/vision-status"}:
        try:
            _action(line, root, out)
        except Exception as exc:
            out(f"Vision command failed: {type(exc).__name__}: {exc}")
        return True
    if not _PARSE_LOCK.acquire(blocking=False):
        out("A direct vision read is already running in this console; no duplicate was started.")
        return True

    def work() -> None:
        try:
            _action(line, root, out)
        except Exception as exc:
            out(f"Vision command failed: {type(exc).__name__}: {exc}")
        finally:
            _PARSE_LOCK.release()

    try:
        threading.Thread(target=work, name="norm-vision-read", daemon=True).start()
    except Exception:
        _PARSE_LOCK.release()
        raise
    out("Direct vision read started (not queued). Keep Norm Prompt open for the result.")
    return True
