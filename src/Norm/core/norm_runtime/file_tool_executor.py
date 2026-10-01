from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib import request as urlrequest

import psycopg
import redis as redis_lib

from .secret_redaction import redact, is_secret_file
from .command_runner import run_bounded
from .deletion_queue import RedisDeletionQueue
from .resource_status import full_context_status, impaired_context_status
from .storage_context import StorageContext
from .plugin_manager import PluginManager
from .task_storage import TaskStorageLimitReached, TaskStorageManager


class StagedWriteError(PermissionError):
    def __init__(self, message: str, *, staged_path: str, note_path: str, attempts: int) -> None:
        super().__init__(message)
        self.staged_path = staged_path
        self.note_path = note_path
        self.attempts = attempts


class FileToolExecutor:
    TOOL_NAMES = {
        "list_directory",
        "read_file",
        "make_directory",
        "write_file",
        "replace_text",
        "delete_file",
        "analyze_image",
        "vision_image",
        "check_connection",
        "run_command",
        "append_task_note",
    }

    def __init__(
        self,
        allowed_roots: list[str],
        *,
        backup_root: str,
        audit_log: str,
        max_read_bytes: int = 25_165_824,
        max_tool_return_bytes: int = 393_216,
        max_write_bytes: int = 5_242_880,
        blocked_write_staging_root: str | None = None,
        write_retry_count: int = 3,
        write_retry_delay_seconds: float = 0.25,
        image_enabled: bool = False,
        image_python: str | None = None,
        image_analyzer_script: str | None = None,
        image_output_root: str | None = None,
        image_profile_budgets: dict[str, Any] | None = None,
        image_max_input_bytes: int = 25_000_000,
        vision_client: Any | None = None,
        deletion_queue: RedisDeletionQueue | None = None,
        storage_config: dict[str, Any] | None = None,
        connection_config: dict[str, Any] | None = None,
        shell_enabled: bool = False,
        shell_executable: str = "powershell.exe",
        shell_timeout_seconds: int = 120,
        shell_max_output_chars: int = 20000,
        verbatim_helper: str | None = None,
        plugin_root: str | None = None,
        plugin_registry_file: str | None = None,
        temp_root: str | None = None,
        workspace_root: str | None = None,
        task_storage_config: dict[str, Any] | None = None,
    ) -> None:
        if not allowed_roots:
            raise ValueError("at least one file-tool root is required")
        self.allowed_roots = tuple(Path(item).resolve() for item in allowed_roots)
        self.backup_root = Path(backup_root).resolve()
        self.audit_log = Path(audit_log).resolve()
        # max_read_bytes is now a processing-buffer size, not a source-file ceiling.
        self.max_read_bytes = max(1_048_576, int(max_read_bytes))
        self.max_tool_return_bytes = max(16_384, int(max_tool_return_bytes))
        self.max_write_bytes = int(max_write_bytes)
        self._hash_cache: dict[tuple[str, int, int], str] = {}
        default_stage = self.backup_root.parent.parent / "docs" / "blocked-writes"
        self.blocked_write_staging_root = Path(blocked_write_staging_root).resolve() if blocked_write_staging_root else default_stage.resolve()
        self.write_retry_count = max(0, int(write_retry_count))
        self.write_retry_delay_seconds = max(0.0, float(write_retry_delay_seconds))
        self.image_enabled = bool(image_enabled)
        self.image_python = Path(image_python).resolve() if image_python else None
        self.image_analyzer_script = Path(image_analyzer_script).resolve() if image_analyzer_script else None
        self.image_output_root = Path(image_output_root).resolve() if image_output_root else None
        self.image_max_input_bytes = max(1_048_576, int(image_max_input_bytes))
        self.vision_client = vision_client
        self.deletion_queue = deletion_queue
        self.storage = StorageContext(storage_config) if storage_config else None
        self.connection_config = dict(connection_config or {})
        self.authority_config = dict(self.connection_config.get("authority") or {})
        self.shell_enabled = bool(shell_enabled)
        self.shell_executable = str(shell_executable or "powershell.exe")
        self.shell_timeout_seconds = max(1, int(shell_timeout_seconds))
        self.shell_max_output_chars = max(1000, int(shell_max_output_chars))
        self.verbatim_helper = Path(verbatim_helper).resolve() if verbatim_helper else None
        self.plugin_manager = PluginManager(plugin_root, plugin_registry_file) if plugin_root else None
        self.task_storage = (
            TaskStorageManager(temp_root, workspace_root, task_storage_config)
            if temp_root and workspace_root else None
        )
        self._storage_context_for_call: dict[str, Any] | None = None
        defaults = {
            "light": {"target_seconds": 300, "max_seconds": 480},
            "medium": {"target_seconds": 1200, "max_seconds": 1800},
            "high": {"target_seconds": 3600, "max_seconds": 4200},
        }
        supplied = image_profile_budgets or {}
        self.image_profile_budgets = {
            name: {
                "target_seconds": int((supplied.get(name) or {}).get("target_seconds", values["target_seconds"])),
                "max_seconds": int((supplied.get(name) or {}).get("max_seconds", values["max_seconds"])),
            }
            for name, values in defaults.items()
        }
        if self.image_enabled:
            # Image analysis is an optional capability. Do not make construction of the
            # whole tool executor depend on its private Python/analyzer files: plain text
            # tasks and unrelated tools must remain usable when image support is absent or
            # temporarily misconfigured. The analyzer-specific paths are checked lazily in
            # _analyze_image().
            if self.image_output_root is None:
                raise ValueError("image_output_root is required when image tools are enabled")
            if not any(self.image_output_root == root or self.image_output_root.is_relative_to(root) for root in self.allowed_roots):
                raise PermissionError("image_output_root must be inside an allowed root")

    def instructions(self) -> str:
        roots = ", ".join(str(root) for root in self.allowed_roots)
        image_help = ""
        if self.image_enabled:
            budgets = ", ".join(
                f"{name} target~{values['target_seconds']}s max={values['max_seconds']}s"
                for name, values in self.image_profile_budgets.items()
            )
            image_help = (
                "\nImage tools are enabled.\n"
                "- analyze_image(path, profile): create brightness/contrast/saturation variants, edge-relative JPEG artifact probabilities, geometric line consensus, transparent logical layers, an artifact-suppressed view, and a pixel-faithful reconstruction. "
                f"Profiles are analysis budgets, not minimum waits: {budgets}. Stop early when evidence converges; do not invent useless tweaks to fill time.\n"
                "- vision_image(paths, prompt): inspect one to six original/derived images with the local Ollama vision model. Prefer comparing the original with multiple derived views and treat cross-variant agreement as stronger evidence than any single preprocessing choice.\n"
                "Do not call a derived feature real merely because one aggressive variant reveals it. Preserve the original and use artifact probability as uncertainty, not as destructive truth."
            )
        return (
            "You have direct file tools inside these allowed roots only: " + roots + ".\n"
            + ("Relative local-file paths use the configured primary Samba storage when responsive and automatically fall back to the configured backup storage when needed. Writes go only to the selected responsive location and are never mirrored. Storage connections are queried only when the requested file path requires them. If a required connection is unresponsive, the tool result carries resource_status JSON with the connection, reason, and 'generating with impaired context'.\n" if self.storage else "")
            + "Use the supplied native tools whenever a file operation is needed. "
            "Tool results here preserve execution values. Terminal/audit/checkpoint copies may contain [REDACTED]; that marker is never a real credential or path. Re-read ordinary configuration when needed; never reuse a redacted placeholder.\n"
            "Never dump .env or credential files. Load credentials internally and pass them through process environment variables; never echo literal secrets.\n"
            "Available tools:\n"
            "- list_directory(path): bounded directory listing.\n"
            "- read_file(path, start_line?, end_line?, start_byte?, max_bytes?): streams UTF-8 text from arbitrarily large source files; results are bounded and return continuation cursors plus sha256.\n"
            "- append_task_note(content, category?): append durable internal Markdown notes in automatically rotated <=5 MiB chunks for the active task.\n"
            "- make_directory(path): creates a directory inside an allowed root.\n"
            "- write_file(path, content, expected_sha256?): creates a UTF-8 file; existing files "
            "require the sha256 returned by read_file.\n"
            "- replace_text(path, old_text, new_text, expected_sha256, expected_occurrences?): hash-checked exact edit.\n"
            "- delete_file(path, expected_sha256, reason): move a file into the deletion queue; permanent purge waits for graceful shutdown.\n"
            + (("- run_command(command, cwd?, timeout_seconds?, stdin_text?): execute a PowerShell command for running/tests/inspection; returns exit_code, stdout, and stderr. Prefer native file tools for file edits/deletes.\n" + (f"- For multiline or quote-heavy command work, use the operator helper at {self.verbatim_helper} with stdin_text containing the complete script (stdin is otherwise closed), then run that script and verify its result; do not build large nested PowerShell quoting expressions.\n" if self.verbatim_helper else "")) if self.shell_enabled else "")
            + image_help
            + (("\n" + self.plugin_manager.instructions()) if self.plugin_manager else "")
            + "\nDeletion is available only inside allowed roots and is reversible until graceful shutdown. Never claim a file changed unless a tool result says ok=true. "
            "If a write reports staged=true, the target was NOT changed: do not retry that write again in the same step. "
            "Report the staged_path and note_path so the blocked edit can be recovered. "
            "After tool results, call more tools if needed or answer the user normally."
        )

    def schemas(self) -> list[dict[str, Any]]:
        def tool(name: str, description: str, properties: dict, required: list[str]):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }

        path = {
            "type": "string",
            "description": "Absolute path inside an allowed root, or a relative path.",
        }
        schemas = [
            tool(
                "list_directory",
                "List up to 200 entries in an allowed directory.",
                {"path": path},
                ["path"],
            ),
            tool(
                "read_file",
                "Stream bounded UTF-8 text from an arbitrarily large source file and return continuation cursors plus its observed SHA-256 hash. Source size is not the response limit.",
                {
                    "path": path,
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "start_byte": {"type": "integer", "minimum": 0},
                    "max_bytes": {"type": "integer", "minimum": 1024},
                    "include_sha256": {"type": "boolean", "description": "Force a full-file SHA-256 scan. Large sources omit it by default to preserve streaming behavior."},
                },
                ["path"],
            ),
            tool(
                "append_task_note",
                "Append internal Markdown notes for the active task. Notes automatically rotate before 5 MiB per physical file and are suitable for durable extraction/summary chunks.",
                {
                    "content": {"type": "string"},
                    "category": {"type": "string"},
                },
                ["content"],
            ),
            tool(
                "make_directory",
                "Create a directory and its parents inside an allowed root.",
                {"path": path},
                ["path"],
            ),
            tool(
                "write_file",
                "Create a UTF-8 file. To replace an existing file, supply its current SHA-256 from read_file.",
                {
                    "path": path,
                    "content": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                },
                ["path", "content"],
            ),
            tool(
                "replace_text",
                "Perform a hash-checked exact text replacement in an existing UTF-8 file.",
                {
                    "path": path,
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                    "expected_occurrences": {"type": "integer"},
                },
                ["path", "old_text", "new_text", "expected_sha256"],
            ),
            tool(
                "delete_file",
                "Queue a hash-checked file deletion. The file is moved to protected trash immediately and permanently purged during graceful shutdown.",
                {
                    "path": path,
                    "expected_sha256": {"type": "string"},
                    "reason": {"type": "string"},
                },
                ["path", "expected_sha256", "reason"],
            ),
            tool(
                "check_connection",
                "Maintenance-only targeted health check. Query exactly one named connection and do not check unrelated connections.",
                {"target": {"type": "string", "enum": ["ca8d", "workspace", "postgres", "redis", "prompt_queue", "ollama", "tailscale", "dropbox"]}},
                ["target"],
            ),
        ]
        if self.shell_enabled:
            schemas.append(tool(
                "run_command",
                "Execute a PowerShell command for running code, tests, or inspection. Returns observed exit_code/stdout/stderr. Prefer native file tools for edits and deletion.",
                {"command": {"type": "string"}, "cwd": path, "timeout_seconds": {"type": "integer", "minimum": 1}, "stdin_text": {"type": "string", "description": "Optional complete UTF-8 stdin. Otherwise stdin is closed; interactive input is unavailable."}},
                ["command"],
            ))
        if self.image_enabled:
            schemas.extend([
                tool(
                    "analyze_image",
                    "Analyze an image deterministically with GPU/CPU image processing. Produces brightness/contrast/saturation variants, edge-relative JPEG artifact probability, geometric consensus, transparent logical layers, artifact-suppressed view, and reconstruction. Light/medium/high are target/max analysis budgets and may finish early on convergence.",
                    {
                        "path": path,
                        "profile": {"type": "string", "enum": ["light", "medium", "high"]},
                    },
                    ["path"],
                ),
                tool(
                    "vision_image",
                    "Inspect one to six image files with the local Ollama vision model. Compare original and derived views when possible; report stable cross-view evidence and uncertainty rather than trusting one aggressive preprocessing variant.",
                    {
                        "paths": {"type": "array", "items": path, "minItems": 1, "maxItems": 6},
                        "prompt": {"type": "string"},
                    },
                    ["paths", "prompt"],
                ),
            ])
        if self.plugin_manager is not None:
            schemas.extend(self.plugin_manager.schemas())
        return schemas

    def set_task_context(self, task_id: str | None, step_id: str | None = None) -> None:
        if self.task_storage is not None:
            self.task_storage.bind(task_id, step_id)

    def _require_authority(self) -> None:
        if not bool(self.authority_config.get("required", False)):
            return
        host = str(self.authority_config.get("host") or "").strip()
        port = int(self.authority_config.get("port") or 0)
        if not host or not port:
            raise RuntimeError("authority network is required but not configured")
        try:
            with socket.create_connection((host, port), timeout=1.5):
                pass
        except OSError as exc:
            raise RuntimeError(f"authority network unavailable at {host}:{port}") from exc

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        self._storage_context_for_call = None
        result: dict[str, Any]
        try:
            self._require_authority()
            if name in self.TOOL_NAMES:
                method = getattr(self, f"_{name}")
                result = method(arguments)
            elif self.plugin_manager is not None and self.plugin_manager.has_tool(name):
                result = self.plugin_manager.execute(name, arguments)
            else:
                raise ValueError(f"unsupported tool: {name}; deletion is not available")
            result.update({"ok": True, "tool": name})
        except TaskStorageLimitReached as exc:
            result = {
                "ok": False,
                "tool": name,
                "error": f"{type(exc).__name__}: {exc}",
                "park_required": True,
                "park_reason": "task_storage_limit",
                "checkpoint_zip": exc.checkpoint_zip,
                "task_storage": {"used_bytes": exc.used_bytes, "limit_bytes": exc.limit_bytes, "full": True},
            }
        except StagedWriteError as exc:
            result = {
                "ok": False,
                "tool": name,
                "error": f"{type(exc).__name__}: {exc}",
                "staged": True,
                "staged_path": exc.staged_path,
                "note_path": exc.note_path,
                "attempts": exc.attempts,
            }
        except Exception as exc:
            result = {
                "ok": False,
                "tool": name,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if self._storage_context_for_call is not None:
            result["storage_context"] = self._storage_context_for_call
            context_status = self._storage_context_for_call.get("context_status")
            if isinstance(context_status, dict):
                result["resource_status"] = context_status
        if name == "check_connection" and result.get("queried") and result.get("responsive") is False:
            result["resource_status"] = impaired_context_status(
                str(result.get("target") or "unknown"), str(result.get("error") or result.get("note") or "unresponsive")
            )
        # Execution/model data stays intact; audit receives a separate safe copy.
        self._audit(name, redact(arguments), redact(result), started)
        return result

    def _resolve(self, raw_path: Any) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("path must be a non-empty string")
        candidate = Path(raw_path.strip())
        if not candidate.is_absolute():
            if self.storage is not None:
                active_root, storage_context = self.storage.select_root()
                self._storage_context_for_call = storage_context
                if active_root is None:
                    raise RuntimeError("primary and backup local storage are unavailable")
                candidate = active_root / candidate
            else:
                candidate = self.allowed_roots[0] / candidate
        elif self.storage is not None and self.storage.primary_root is not None:
            primary_root = self.storage.primary_root
            if candidate == primary_root or candidate.is_relative_to(primary_root):
                self._storage_context_for_call = self.storage.context_for_source("primary")
                primary_status = self._storage_context_for_call.get("primary") or {}
                if primary_status.get("responsive") is False:
                    raise ConnectionError(primary_status.get("error") or "ca8d storage is unresponsive")
        resolved = candidate.resolve(strict=False)
        if not any(resolved == root or resolved.is_relative_to(root) for root in self.allowed_roots):
            raise PermissionError(f"path is outside allowed roots: {resolved}")
        return resolved

    def _run_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.shell_enabled:
            raise RuntimeError("shell tool is disabled")
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        cwd_raw = arguments.get("cwd")
        cwd = self._resolve(cwd_raw) if cwd_raw else self.allowed_roots[0]
        if not cwd.is_dir():
            raise NotADirectoryError(cwd)
        requested_timeout = int(arguments.get("timeout_seconds", self.shell_timeout_seconds))
        timeout = max(1, min(requested_timeout, self.shell_timeout_seconds))
        result = run_bounded(
            [self.shell_executable, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=str(cwd), timeout=timeout, max_output_chars=self.shell_max_output_chars,
            stdin_text=arguments.get("stdin_text"),
        )
        return {"command": command, "cwd": str(cwd), **result}

    def _check_connection(self, arguments: dict[str, Any]) -> dict[str, Any]:
        target = str(arguments.get("target") or "").strip().lower()
        aliases = {"ca8d_smb": "ca8d", "local": "workspace", "postgresql": "postgres", "pg": "postgres"}
        target = aliases.get(target, target)
        if target in {"ca8d", "workspace"}:
            if self.storage is None:
                return {"target": target, "configured": False, "queried": False, "responsive": False, "note": "storage connection is not configured"}
            source = "primary" if target == "ca8d" else "backup"
            status = self.storage.check_source(source, force=True)
            return {"target": target, "configured": True, "queried": True, **status}
        if target == "dropbox":
            return {"target": target, "configured": False, "queried": False, "responsive": False, "note": "Dropbox is not a native Norm runtime connection yet."}
        if target == "tailscale":
            try:
                cp = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=3, check=False)
                return {"target": target, "configured": True, "queried": True, "responsive": cp.returncode == 0, "error": None if cp.returncode == 0 else cp.stderr.strip()[-500:]}
            except Exception as exc:
                return {"target": target, "configured": True, "queried": True, "responsive": False, "error": f"{type(exc).__name__}: {exc}"}
        cfg = self.connection_config
        if target == "postgres":
            pg = cfg.get("postgres") or {}
            conninfo = str(pg.get("conninfo") or "")
            if not conninfo:
                return {"target": target, "configured": False, "queried": False, "responsive": False}
            try:
                with psycopg.connect(conninfo, connect_timeout=3) as conn, conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
                return {"target": target, "configured": True, "queried": True, "responsive": True}
            except Exception as exc:
                return {"target": target, "configured": True, "queried": True, "responsive": False, "error": f"{type(exc).__name__}: {exc}"}
        if target in {"redis", "prompt_queue"}:
            rcfg = cfg.get(target) or {}
            if not rcfg:
                return {"target": target, "configured": False, "queried": False, "responsive": False}
            try:
                client = redis_lib.Redis(host=rcfg.get("host", "127.0.0.1"), port=int(rcfg.get("port", 6379)),
                                         db=int(rcfg.get("db", 0)), socket_connect_timeout=2, socket_timeout=2)
                ok = bool(client.ping())
                return {"target": target, "configured": True, "queried": True, "responsive": ok}
            except Exception as exc:
                return {"target": target, "configured": True, "queried": True, "responsive": False, "error": f"{type(exc).__name__}: {exc}"}
        if target == "ollama":
            base_url = str(cfg.get("ollama_base_url") or "")
            if not base_url:
                return {"target": target, "configured": False, "queried": False, "responsive": False}
            try:
                with urlrequest.urlopen(base_url.rstrip("/") + "/api/tags", timeout=2) as response:
                    ok = response.status == 200
                return {"target": target, "configured": True, "queried": True, "responsive": ok}
            except Exception as exc:
                return {"target": target, "configured": True, "queried": True, "responsive": False, "error": f"{type(exc).__name__}: {exc}"}
        raise ValueError("unknown connection target")

    def _sha256(self, path: Path) -> str:
        stat = path.stat()
        key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
        cached = self._hash_cache.get(key)
        if cached:
            return cached
        digest = hashlib.sha256()
        with path.open("rb", buffering=self.max_read_bytes) as handle:
            for chunk in iter(lambda: handle.read(self.max_read_bytes), b""):
                digest.update(chunk)
        value = digest.hexdigest()
        # Keep cache bounded and invalidate naturally on size/mtime changes.
        if len(self._hash_cache) > 128:
            self._hash_cache.clear()
        self._hash_cache[key] = value
        return value

    def _list_directory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(arguments.get("path", "."))
        if not path.is_dir():
            raise NotADirectoryError(path)
        entries = []
        for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:200]:
            entries.append(
                {
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return {"path": str(path), "entries": entries, "truncated": len(entries) == 200}

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(arguments.get("path"))
        if is_secret_file(path):
            raise PermissionError("Secret files must be loaded internally; raw reads are disabled")
        if not path.is_file():
            raise FileNotFoundError(path)

        stat = path.stat()
        size = int(stat.st_size)
        force_hash = bool(arguments.get("include_sha256", False))
        sha = self._sha256(path) if (force_hash or size <= self.max_read_bytes) else None
        source_fingerprint = hashlib.sha256(
            f"{path}\0{size}\0{int(stat.st_mtime_ns)}".encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        if self.task_storage is not None and self.task_storage.task_id:
            self.task_storage.register_source(path, sha256=sha)

        requested = int(arguments.get("max_bytes") or self.max_tool_return_bytes)
        return_cap = max(1024, min(requested, self.max_tool_return_bytes))
        start_byte_arg = arguments.get("start_byte")
        start_line = max(1, int(arguments.get("start_line", 1)))
        end_line_raw = arguments.get("end_line")
        end_line = int(end_line_raw) if end_line_raw is not None else None
        if end_line is not None and end_line < start_line:
            raise ValueError("end_line must be at or after start_line")

        chunks: list[bytes] = []
        returned = 0
        lines_returned = 0
        truncated = False
        next_start_line: int | None = None
        total_lines: int | None = None

        with path.open("rb", buffering=self.max_read_bytes) as handle:
            if start_byte_arg is not None:
                requested_start_byte = max(0, int(start_byte_arg))
                handle.seek(min(requested_start_byte, size))
                effective_start_byte = handle.tell()
                data = handle.read(min(return_cap, max(0, size - effective_start_byte)))
                chunks.append(data)
                returned = len(data)
                next_byte = handle.tell()
                truncated = next_byte < size
                # Byte-cursor mode deliberately does not pretend to know source line numbers.
                effective_start_line = None
            else:
                effective_start_byte = 0
                current_line = 1
                while current_line < start_line:
                    line = handle.readline()
                    if not line:
                        break
                    current_line += 1
                effective_start_byte = handle.tell()
                effective_start_line = current_line
                while True:
                    if end_line is not None and current_line > end_line:
                        break
                    line_start = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if returned and returned + len(line) > return_cap:
                        handle.seek(line_start)
                        truncated = True
                        next_start_line = current_line
                        break
                    if not returned and len(line) > return_cap:
                        line = line[:return_cap]
                        handle.seek(line_start + len(line))
                        truncated = True
                    chunks.append(line)
                    returned += len(line)
                    lines_returned += 1
                    current_line += 1
                    if returned >= return_cap:
                        truncated = handle.tell() < size
                        if truncated:
                            next_start_line = current_line
                        break
                next_byte = handle.tell()
                if not truncated and next_byte < size and (end_line is None or current_line <= end_line):
                    truncated = True
                    next_start_line = current_line

        raw = b"".join(chunks)
        text = raw.decode("utf-8", errors="replace")
        # Counting an entire gigantic source on every chunk defeats streaming. For files
        # within one processing buffer, expose total_lines; otherwise leave it unknown.
        if size <= self.max_read_bytes:
            try:
                with path.open("rb", buffering=self.max_read_bytes) as counter:
                    total_lines = sum(1 for _ in counter)
            except OSError:
                total_lines = None

        processing = {"park_required": False}
        if self.task_storage is not None and self.task_storage.task_id:
            processing = self.task_storage.add_processed_bytes(returned)

        return {
            "path": str(path),
            "sha256": sha,
            "source_fingerprint": source_fingerprint,
            "size": size,
            "source_size_bytes": size,
            "source_size_limited": False,
            "processing_buffer_bytes": self.max_read_bytes,
            "max_return_bytes": self.max_tool_return_bytes,
            "returned_bytes": returned,
            "start_byte": int(effective_start_byte),
            "next_byte": int(next_byte),
            "start_line": effective_start_line,
            "end_line": (effective_start_line + lines_returned - 1) if effective_start_line is not None and lines_returned else None,
            "next_start_line": next_start_line,
            "total_lines": total_lines,
            "truncated": bool(truncated),
            "content": text,
            **processing,
        }

    def _append_task_note(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.task_storage is None or not self.task_storage.task_id:
            raise RuntimeError("append_task_note requires an active worker task context")
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        category = str(arguments.get("category") or "notes").strip() or "notes"
        path = self.task_storage.append_note(content, category=category)
        return {
            "path": str(path),
            "bytes": len(content.encode("utf-8")),
            "task_storage": self.task_storage.capacity(),
        }

    def _make_directory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(arguments.get("path"))
        existed = path.exists()
        if existed and not path.is_dir():
            raise NotADirectoryError(path)
        path.mkdir(parents=True, exist_ok=True)
        return {"path": str(path), "created": not existed}

    def _analyze_image(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.image_enabled:
            raise RuntimeError("image analysis is disabled")
        if self.image_python is None or not self.image_python.is_file():
            raise FileNotFoundError(f"image Python runtime not found: {self.image_python}")
        if self.image_analyzer_script is None or not self.image_analyzer_script.is_file():
            raise FileNotFoundError(f"image analyzer script not found: {self.image_analyzer_script}")
        assert self.image_output_root is not None
        self.image_output_root.mkdir(parents=True, exist_ok=True)
        path = self._resolve(arguments.get("path"))
        if not path.is_file():
            raise FileNotFoundError(path)
        # Source-file size is not a hard ceiling. Image decoding is handled by the
        # analyzer; the configured image value is a processing-buffer hint only.
        source_sha = self._sha256(path)
        if self.task_storage is not None and self.task_storage.task_id:
            self.task_storage.register_source(path, sha256=source_sha)
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            raise ValueError(f"unsupported image extension: {path.suffix}")
        profile = str(arguments.get("profile") or "light").strip().lower()
        if profile not in self.image_profile_budgets:
            raise ValueError("profile must be light, medium, or high")
        budget = self.image_profile_budgets[profile]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if self.task_storage is not None and self.task_storage.task_id:
            self.task_storage.ensure_capacity()
            output_base = self.task_storage.task_root / "assets" / "image-analysis"
        else:
            output_base = self.image_output_root
        output = output_base / f"{stamp}-{uuid.uuid4().hex[:8]}-{path.stem}-{profile}"
        output.mkdir(parents=True, exist_ok=False)
        completed = subprocess.run(
            [str(self.image_python), str(self.image_analyzer_script), str(path), "--output", str(output), "--profile", profile],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, int(budget["max_seconds"]) + 30),
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()[-4000:]
            raise RuntimeError(f"image analyzer failed exit={completed.returncode}: {stderr}")
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("image analyzer returned no JSON")
        result = json.loads(lines[-1])
        if not isinstance(result, dict):
            raise RuntimeError("image analyzer returned invalid result")
        result["path"] = str(path)
        result["output_dir"] = str(output)
        result["budget"] = budget
        if self.task_storage is not None and self.task_storage.task_id:
            self.task_storage.register_asset(
                output, role="image_analysis_derivatives", source=path, reproducible=True,
                recipe=f"image_analyzer profile={profile}", retention="reproducible_cache",
            )
            capacity = self.task_storage.capacity()
            processing = self.task_storage.add_processed_bytes(path.stat().st_size)
            result["task_storage"] = capacity
            result.update(processing)
            if capacity.get("full") and not result.get("park_required"):
                checkpoint = self.task_storage.create_checkpoint_zip(reason="task_storage_limit")
                result["park_required"] = True
                result["checkpoint_zip"] = str(checkpoint)
                result["park_reason"] = "task_storage_limit"
        return result

    def _vision_image(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.image_enabled:
            raise RuntimeError("image tools are disabled")
        if self.vision_client is None or not hasattr(self.vision_client, "vision"):
            raise RuntimeError("vision client is unavailable")
        raw_paths = arguments.get("paths")
        if not isinstance(raw_paths, list) or not 1 <= len(raw_paths) <= 6:
            raise ValueError("paths must contain one to six image paths")
        resolved: list[str] = []
        total_bytes = 0
        for raw in raw_paths:
            path = self._resolve(raw)
            if not path.is_file():
                raise FileNotFoundError(path)
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                raise ValueError(f"unsupported image extension: {path.suffix}")
            total_bytes += path.stat().st_size
            resolved.append(str(path))
        if total_bytes > self.image_max_input_bytes * 2:
            raise ValueError("combined vision image payload is too large")
        prompt = arguments.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        answer = self.vision_client.vision(prompt.strip(), resolved, think=True, temperature=0.1)
        return {"paths": resolved, "answer": answer, "image_count": len(resolved)}

    def _write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_write_bytes:
            raise ValueError("content exceeds write limit")
        expected = arguments.get("expected_sha256")
        created = not path.exists()
        if path.exists():
            if not path.is_file():
                raise IsADirectoryError(path)
            actual = self._sha256(path)
            if not isinstance(expected, str) or expected.lower() != actual:
                raise ValueError("existing file requires its current expected_sha256")
            self._backup(path, actual)
        if self.task_storage is not None and self.task_storage.task_id:
            try:
                path.relative_to(self.task_storage.task_root)
                self.task_storage.ensure_capacity(len(encoded))
            except ValueError:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.normtmp-{uuid.uuid4().hex}")
        temporary.write_bytes(encoded)
        context = arguments.get("_write_context") if isinstance(arguments.get("_write_context"), dict) else {"operation": "write_file"}
        self._replace_with_retries(temporary, path, expected, context)
        result = {
            "path": str(path),
            "created": created,
            "bytes": len(encoded),
            "sha256": self._sha256(path),
        }
        if self.task_storage is not None and self.task_storage.task_id:
            try:
                path.relative_to(self.task_storage.task_root)
            except ValueError:
                pass
            else:
                self.task_storage.register_asset(path, role="task_file", reproducible=False, retention="review")
                result["task_storage"] = self.task_storage.capacity()
        return result

    def _replace_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(arguments.get("path"))
        if not path.is_file():
            raise FileNotFoundError(path)
        actual_hash = self._sha256(path)
        expected_hash = arguments.get("expected_sha256")
        if not isinstance(expected_hash, str) or expected_hash.lower() != actual_hash:
            raise ValueError("expected_sha256 does not match the current file")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ValueError("old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise ValueError("new_text must be a string")
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(old_text)
        expected_count = int(arguments.get("expected_occurrences", 1))
        if occurrences != expected_count:
            raise ValueError(f"expected {expected_count} matches but found {occurrences}")
        return self._write_file(
            {
                "path": str(path),
                "content": text.replace(old_text, new_text),
                "expected_sha256": actual_hash,
                "_write_context": {
                    "operation": "replace_text",
                    "old_text": old_text,
                    "new_text": new_text,
                    "expected_occurrences": expected_count,
                },
            }
        ) | {"replacements": occurrences}

    def _delete_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.deletion_queue is None:
            raise RuntimeError("deletion queue is unavailable")
        path = self._resolve(arguments.get("path"))
        if not path.is_file():
            raise FileNotFoundError(path)
        expected = arguments.get("expected_sha256")
        if not isinstance(expected, str) or not expected:
            raise ValueError("delete_file requires expected_sha256 from read_file")
        actual = self._sha256(path)
        if expected.lower() != actual:
            raise ValueError("expected_sha256 does not match the current file")
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("delete_file requires a non-empty reason")
        return self.deletion_queue.stage_file(path, expected_sha256=actual, reason=reason)

    def _replace_with_retries(self, temporary: Path, path: Path, expected_sha256: Any, context: dict[str, Any]) -> None:
        attempts = self.write_retry_count + 1
        last_error: PermissionError | None = None
        for attempt in range(attempts):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(self.write_retry_delay_seconds * (attempt + 1))
        staged_path, note_path = self._stage_blocked_write(temporary, path, expected_sha256, context, last_error, attempts)
        raise StagedWriteError(
            f"target remained write-blocked after {attempts} attempts; desired content staged instead",
            staged_path=str(staged_path), note_path=str(note_path), attempts=attempts,
        )

    def _stage_blocked_write(self, temporary: Path, target: Path, expected_sha256: Any, context: dict[str, Any], error: Exception | None, attempts: int) -> tuple[Path, Path]:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        stage_dir = self.blocked_write_staging_root / f"{stamp}-{uuid.uuid4().hex[:8]}-{target.name}"
        stage_dir.mkdir(parents=True, exist_ok=False)
        staged_path = stage_dir / f"desired-{target.name}"
        shutil.copy2(temporary, staged_path)
        desired_hash = self._sha256(staged_path)
        note_path = stage_dir / "README.md"
        detail = json.dumps(context, ensure_ascii=False, indent=2)
        note_path.write_text(
            "# Norm blocked write fallback\n\n"
            f"Target: `{target}`\n\nOperation: `{context.get('operation', 'write_file')}`\n\n"
            f"Attempts: {attempts} (initial attempt plus {self.write_retry_count} retries)\n\n"
            f"Reason: `{type(error).__name__ if error else 'PermissionError'}: {error}`\n\n"
            f"Expected target SHA-256: `{expected_sha256 or 'new file'}`\n\n"
            f"Desired content SHA-256: `{desired_hash}`\n\n"
            f"Staged desired file: `{staged_path}`\n\n## Intended operation details\n\n```json\n{detail}\n```\n",
            encoding="utf-8",
        )
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return staged_path, note_path

    def _backup(self, path: Path, sha256: str) -> None:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.backup_root / f"{stamp}-{sha256[:12]}-{path.name}"
        shutil.copy2(path, backup)

    def _audit(
        self,
        name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        started: float,
    ) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": started,
            "tool": name,
            "requested_path": arguments.get("path"),
            "ok": result.get("ok", False),
            "resolved_path": result.get("path"),
            "error": result.get("error"),
        }
        with self.audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
