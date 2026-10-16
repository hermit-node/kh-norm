from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/New_York"
TEXT_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".go", ".rs",
    ".java", ".kt", ".kts", ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb",
    ".sh", ".ps1", ".bat", ".cmd", ".sql", ".toml", ".ini", ".cfg", ".conf",
    ".json", ".jsonc", ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".scss",
    ".sass", ".less", ".md", ".rst", ".txt", ".properties", ".gradle", ".lock"
}
SKIP_PARTS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    ".next", ".nuxt", "coverage"
}
PRIORITY_NAMES = {
    "pyproject.toml": 120, "package.json": 120, "cargo.toml": 120, "go.mod": 120,
    "requirements.txt": 115, "docker-compose.yml": 112, "docker-compose.yaml": 112,
    "compose.yml": 112, "compose.yaml": 112, "dockerfile": 110,
    "readme.md": 108, "main.py": 105, "app.py": 104, "server.py": 104,
    "index.ts": 100, "index.js": 100, "main.ts": 100, "main.js": 100,
    "src/main.py": 110, "src/main.ts": 110, "src/main.js": 110,
}
MAX_MANIFEST_FILES = 20000


def _now() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _ts() -> str:
    return _now().isoformat(timespec="seconds")


def _state_root() -> Path:
    override = os.environ.get("NORM_LESSON_MODEL_STATE", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        runtime_override = os.environ.get("NORM_RUNTIME_ROOT", "").strip()
        runtime_root = Path(runtime_override).expanduser().resolve() if runtime_override else Path(__file__).resolve().parents[3]
        root = runtime_root / "state" / "lesson_model"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "code_memory"
    (target / "scans").mkdir(parents=True, exist_ok=True)
    (target / "readmes").mkdir(parents=True, exist_ok=True)
    (target / "pending_postgres").mkdir(parents=True, exist_ok=True)
    (target / "sources").mkdir(parents=True, exist_ok=True)
    return target


def _scan_path(scan_id: str) -> Path:
    return _state_root() / "scans" / f"{scan_id}.json"


def _load_scan(scan_id: str) -> dict:
    path = _scan_path(str(scan_id or "").strip())
    if not path.is_file():
        raise KeyError(f"unknown code scan: {scan_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_scan(scan: dict) -> None:
    _scan_path(scan["scan_id"]).write_text(
        json.dumps(scan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot_source(source: Path, sha256: str) -> Path:
    """Keep one immutable wholesale archive copy per exact content hash."""
    suffix = source.suffix.lower() or ".zip"
    target = _state_root() / "sources" / f"{sha256}{suffix}"
    if target.exists():
        if _sha256_file(target) != sha256:
            raise RuntimeError(f"existing code-memory source snapshot hash mismatch: {target}")
        return target
    temp = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(source, temp)
    if _sha256_file(temp) != sha256:
        temp.unlink(missing_ok=True)
        raise RuntimeError("copied source snapshot failed SHA-256 verification")
    temp.replace(target)
    return target


def _safe_member(name: str) -> bool:
    p = PurePosixPath(name)
    return not (p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name)


def _is_skipped(name: str) -> bool:
    p = PurePosixPath(name)
    return any(part.lower() in SKIP_PARTS for part in p.parts)


def _is_text_candidate(name: str) -> bool:
    low = name.lower()
    p = PurePosixPath(low)
    if p.name in {"dockerfile", "makefile", "procfile", "gemfile", "rakefile"}:
        return True
    if low.endswith(".env.example"):
        return True
    return p.suffix in TEXT_EXTENSIONS


def _priority(name: str) -> int:
    low = name.lower()
    p = PurePosixPath(low)
    score = PRIORITY_NAMES.get(low, PRIORITY_NAMES.get(p.name, 0))
    if p.name in {"plugin.json", "manifest.json", "settings.ini", "runtime.json"}:
        score += 105
    if "entrypoint" in low or "bootstrap" in low:
        score += 80
    if "router" in low or "routes" in low:
        score += 50
    if "config" in low or "settings" in low:
        score += 45
    if p.parts and p.parts[0] in {"src", "app", "core", "server", "backend"}:
        score += 25
    if "test" in p.parts or "tests" in p.parts or p.name.startswith("test_"):
        score -= 15
    if p.suffix in {".md", ".rst", ".txt"} and p.name != "readme.md":
        score -= 10
    return score


def _language(name: str) -> str:
    p = PurePosixPath(name.lower())
    mapping = {
        ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".vue": "vue", ".svelte": "svelte",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
        ".php": "php", ".rb": "ruby", ".sh": "shell", ".ps1": "powershell",
        ".sql": "sql", ".json": "json", ".jsonc": "json", ".toml": "toml",
        ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".html": "html",
        ".css": "css", ".scss": "scss", ".ini": "ini", ".cfg": "config",
    }
    if p.name == "dockerfile":
        return "dockerfile"
    return mapping.get(p.suffix, "text")


def _python_hints(text: str) -> dict:
    hints: dict[str, Any] = {"imports": [], "functions": [], "classes": [], "main_guard": False}
    try:
        tree = ast.parse(text)
    except Exception:
        return hints
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.col_offset == 0:
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.col_offset == 0:
            classes.append(node.name)
        elif isinstance(node, ast.If):
            try:
                rendered = ast.unparse(node.test)
            except Exception:
                rendered = ""
            if "__name__" in rendered and "__main__" in rendered:
                hints["main_guard"] = True
    hints["imports"] = sorted(set(imports))[:80]
    hints["functions"] = functions[:80]
    hints["classes"] = classes[:80]
    return hints


def _js_hints(text: str) -> dict:
    imports = set()
    for match in re.finditer(r"""(?:from\s+|require\(\s*|import\(\s*)['"]([^'"]+)['"]""", text):
        imports.add(match.group(1))
    return {
        "imports": sorted(imports)[:80],
        "has_create_app": "createApp(" in text,
        "has_listen": bool(re.search(r"\.listen\s*\(", text)),
        "has_main_export": bool(re.search(r"export\s+default|module\.exports", text)),
    }


def _content_hints(name: str, raw: bytes) -> dict:
    text = raw.decode("utf-8", errors="replace")
    lang = _language(name)
    hints: dict[str, Any] = {}
    if lang == "python":
        hints.update(_python_hints(text))
    elif lang in {"javascript", "typescript", "vue", "svelte"}:
        hints.update(_js_hints(text))
    if PurePosixPath(name.lower()).name == "package.json":
        try:
            obj = json.loads(text)
            hints["package_scripts"] = obj.get("scripts", {})
            hints["package_main"] = obj.get("main")
            hints["package_bin"] = obj.get("bin")
            hints["dependencies"] = sorted((obj.get("dependencies") or {}).keys())[:200]
            hints["dev_dependencies"] = sorted((obj.get("devDependencies") or {}).keys())[:200]
        except Exception:
            pass
    return hints


def scan_zip(path: str, project_name: str = "", version_label: str = "", max_files: int = 10000) -> dict:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not zipfile.is_zipfile(source):
        raise ValueError("code memory skeleton currently accepts ZIP archives only")

    cap = max(1, min(int(max_files), MAX_MANIFEST_FILES))
    archive_sha = _sha256_file(source)
    retained_source = _snapshot_source(source, archive_sha)
    scan_id = f"C-{_now():%Y%m%d}-{uuid.uuid4().hex[:8]}"
    files: list[dict] = []
    ignored = 0
    unsafe = 0
    total_uncompressed = 0

    with zipfile.ZipFile(retained_source, "r") as zf:
        infos = sorted(zf.infolist(), key=lambda x: x.filename.lower())
        for info in infos:
            if info.is_dir():
                continue
            if not _safe_member(info.filename):
                unsafe += 1
                continue
            total_uncompressed += int(info.file_size)
            if _is_skipped(info.filename):
                ignored += 1
                continue
            text_candidate = _is_text_candidate(info.filename)
            item = {
                "path": info.filename,
                "size_bytes": int(info.file_size),
                "compressed_bytes": int(info.compress_size),
                "crc32": f"{info.CRC:08x}",
                "text_candidate": text_candidate,
                "language": _language(info.filename) if text_candidate else "binary",
                "priority": _priority(info.filename) if text_candidate else -100,
            }
            if text_candidate and info.file_size <= 1024 * 1024:
                try:
                    raw = zf.read(info)
                    item["sha256"] = hashlib.sha256(raw).hexdigest()
                    item["hints"] = _content_hints(info.filename, raw[:512 * 1024])
                except Exception as exc:
                    item["read_error"] = f"{type(exc).__name__}: {exc}"
            files.append(item)
            if len(files) >= cap:
                break

    text_files = [x for x in files if x["text_candidate"]]
    runtime_order = sorted(text_files, key=lambda x: (-int(x.get("priority", 0)), x["path"].lower()))
    likely_entrypoints = [
        {"path": x["path"], "priority": x["priority"], "reason": "priority/runtime signal", "hints": x.get("hints", {})}
        for x in runtime_order if int(x.get("priority", 0)) >= 85
    ][:40]

    scan = {
        "schema": "norm.code_scan.v1",
        "scan_id": scan_id,
        "created_at": _ts(),
        "project_name": str(project_name or source.stem).strip() or source.stem,
        "version_label": str(version_label or "").strip(),
        "source_kind": "zip",
        "source_path": str(retained_source),
        "original_source_path": str(source),
        "source_name": source.name,
        "source_retained_wholesale": True,
        "source_size_bytes": source.stat().st_size,
        "source_sha256": archive_sha,
        "archive_total_uncompressed_bytes": total_uncompressed,
        "manifest_truncated": len(files) >= cap and len(infos) > cap,
        "manifest_file_count": len(files),
        "ignored_generated_or_vendor_count": ignored,
        "unsafe_member_count": unsafe,
        "files": files,
        "runtime_order": [x["path"] for x in runtime_order],
        "likely_entrypoints": likely_entrypoints,
        "analysis": None,
        "readme": None,
        "postgres": {"persisted": False},
    }
    _save_scan(scan)
    return {
        "ok": True,
        "scan_id": scan_id,
        "project_name": scan["project_name"],
        "version_label": scan["version_label"],
        "source_sha256": archive_sha,
        "retained_source_path": str(retained_source),
        "source_retained_wholesale": True,
        "manifest_file_count": len(files),
        "text_candidate_count": len(text_files),
        "ignored_generated_or_vendor_count": ignored,
        "likely_entrypoints": likely_entrypoints,
        "runtime_order_preview": scan["runtime_order"][:30],
        "next_cursor": "0:0" if runtime_order else None,
        "scan_path": str(_scan_path(scan_id)),
    }


def next_chunk(scan_id: str, cursor: str = "", max_chars: int = 20000) -> dict:
    scan = _load_scan(scan_id)
    order = list(scan.get("runtime_order") or [])
    if not order:
        return {"ok": True, "done": True, "scan_id": scan_id, "next_cursor": None}

    raw_cursor = str(cursor or "0:0").strip()
    try:
        file_index_s, char_offset_s = raw_cursor.split(":", 1)
        file_index = max(0, int(file_index_s))
        char_offset = max(0, int(char_offset_s))
    except Exception:
        raise ValueError("cursor must be '<file_index>:<char_offset>'")

    if file_index >= len(order):
        return {"ok": True, "done": True, "scan_id": scan_id, "next_cursor": None}

    cap = max(1000, min(int(max_chars), 100000))
    path = order[file_index]
    source = Path(scan["source_path"])
    with zipfile.ZipFile(source, "r") as zf:
        raw = zf.read(path)
    text = raw.decode("utf-8", errors="replace")
    end = min(len(text), char_offset + cap)
    content = text[char_offset:end]
    estimated_parts = max(1, (len(text) + cap - 1) // cap)
    part_index = min(estimated_parts, (char_offset // cap) + 1)

    if end < len(text):
        next_cursor = f"{file_index}:{end}"
        seam_after = f"{{{file_index + 1}:{end}}}"
    else:
        next_cursor = f"{file_index + 1}:0" if file_index + 1 < len(order) else None
        seam_after = f"{{{file_index + 1}:END}}"

    manifest = {x["path"]: x for x in scan["files"]}
    item = manifest.get(path, {})
    return {
        "ok": True,
        "done": False,
        "scan_id": scan_id,
        "project_name": scan["project_name"],
        "version_label": scan["version_label"],
        "file_index": file_index,
        "file_count": len(order),
        "path": path,
        "language": item.get("language", "text"),
        "priority": item.get("priority", 0),
        "hints": item.get("hints", {}),
        "char_offset": char_offset,
        "next_char_offset": end,
        "file_chars": len(text),
        "part_index": part_index,
        "estimated_parts": estimated_parts,
        "seam_before": f"{{{file_index + 1}:{char_offset}}}",
        "seam_after": seam_after,
        "content": content,
        "next_cursor": next_cursor,
        "evidence_ref": f"{scan_id}:{path}:{char_offset}-{end}",
    }


def save_analysis(
    scan_id: str,
    raw_summary: dict,
    analyzed_by_model: str = "",
    completeness: str = "",
    intent: str = "",
    suggestions: list[dict] | None = None,
    uncertainties: list[str] | None = None,
) -> dict:
    if not isinstance(raw_summary, dict) or not raw_summary:
        raise ValueError("raw_summary must be a non-empty object")
    scan = _load_scan(scan_id)
    analysis = {
        "schema": "norm.code_memory.v1",
        "saved_at": _ts(),
        "analyzed_by_model": str(analyzed_by_model or "").strip(),
        "completeness": str(completeness or "").strip(),
        "intent": str(intent or "").strip(),
        "suggestions": list(suggestions or []),
        "uncertainties": [str(x) for x in (uncertainties or []) if str(x).strip()],
        "raw_summary": raw_summary,
    }
    scan["analysis"] = analysis
    _save_scan(scan)
    return {
        "ok": True,
        "scan_id": scan_id,
        "project_name": scan["project_name"],
        "version_label": scan["version_label"],
        "analysis_saved": True,
        "postgres_persisted": bool((scan.get("postgres") or {}).get("persisted")),
    }


def write_readme(scan_id: str, markdown: str, output_path: str = "") -> dict:
    text = str(markdown or "").strip()
    if not text:
        raise ValueError("markdown cannot be empty")
    scan = _load_scan(scan_id)
    if output_path:
        target = Path(output_path).expanduser().resolve()
    else:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", scan["project_name"]).strip("-") or "project"
        version = re.sub(r"[^A-Za-z0-9._-]+", "-", scan.get("version_label") or "").strip("-")
        suffix = f"-{version}" if version else ""
        target = _state_root() / "readmes" / f"{safe_name}{suffix}-README.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = text.rstrip() + "\n"
    target.write_text(rendered, encoding="utf-8")
    scan["readme"] = {
        "written_at": _ts(),
        "path": str(target),
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    }
    _save_scan(scan)
    return {"ok": True, "scan_id": scan_id, **scan["readme"]}


def get_scan(scan_id: str, include_files: bool = False) -> dict:
    scan = _load_scan(scan_id)
    out = dict(scan)
    if not include_files:
        out.pop("files", None)
        out["runtime_order_count"] = len(scan.get("runtime_order") or [])
        out["runtime_order"] = (scan.get("runtime_order") or [])[:60]
    out["ok"] = True
    return out


def list_scans(project_name: str = "", limit: int = 50) -> dict:
    cap = max(1, min(int(limit), 200))
    name_filter = str(project_name or "").strip().lower()
    rows = []
    for path in sorted((_state_root() / "scans").glob("C-*.json"), reverse=True):
        try:
            scan = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if name_filter and str(scan.get("project_name") or "").lower() != name_filter:
            continue
        rows.append({
            "scan_id": scan.get("scan_id"),
            "created_at": scan.get("created_at"),
            "project_name": scan.get("project_name"),
            "version_label": scan.get("version_label"),
            "source_name": scan.get("source_name"),
            "source_sha256": scan.get("source_sha256"),
            "manifest_file_count": scan.get("manifest_file_count"),
            "has_analysis": bool(scan.get("analysis")),
            "readme_path": ((scan.get("readme") or {}).get("path")),
            "postgres_persisted": bool((scan.get("postgres") or {}).get("persisted")),
        })
        if len(rows) >= cap:
            break
    return {"ok": True, "scans": rows, "returned": len(rows)}
