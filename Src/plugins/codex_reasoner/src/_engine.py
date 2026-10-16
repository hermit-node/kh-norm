from __future__ import annotations

import configparser
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _runtime_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _settings() -> dict[str, Any]:
    root = _runtime_root()
    path = root / "config" / "plugins.ini"
    parser = configparser.ConfigParser(interpolation=None)
    if path.is_file():
        parser.read(path, encoding="utf-8-sig")
    section = "codex_reasoner"
    get = lambda key, default="": parser.get(section, key, fallback=default).strip()
    try:
        timeout = int(get("timeout_seconds", "1800") or "1800")
    except ValueError:
        timeout = 1800
    try:
        max_chars = int(get("max_input_chars", "60000") or "60000")
    except ValueError:
        max_chars = 60000
    return {
        "enabled": parser.getboolean(section, "enabled", fallback=True),
        "executable": get("executable", "codex") or "codex",
        "model": get("model", ""),
        "reasoning_effort": get("reasoning_effort", "high") or "high",
        "timeout_seconds": max(30, timeout),
        "max_input_chars": max(2000, min(max_chars, 250000)),
    }


def _command(executable: str, args: list[str]) -> list[str]:
    resolved = shutil.which(executable) or executable
    suffix = Path(resolved).suffix.lower()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", resolved, *args]
    return [resolved, *args]


def status() -> dict:
    cfg = _settings()
    if not cfg["enabled"]:
        return {"ok": False, "enabled": False, "reason": "Codex reasoning lane is disabled in plugins.ini"}
    try:
        proc = subprocess.run(
            _command(str(cfg["executable"]), ["--version"]),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
    except Exception as exc:
        return {"ok": False, "enabled": True, "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "enabled": True,
        "version": (proc.stdout or proc.stderr).strip(),
        "returncode": int(proc.returncode),
    }


def reason(*, question: str, context: str = "", mode: str = "analysis") -> dict:
    cfg = _settings()
    if not cfg["enabled"]:
        raise RuntimeError("Codex reasoning lane is disabled in plugins.ini")
    question = str(question or "").strip()
    if not question:
        raise ValueError("question is required")
    context = str(context or "")
    cap = int(cfg["max_input_chars"])
    if len(context) > cap:
        context = context[:cap] + "\n...<context truncated by Norm>"
    mode = str(mode or "analysis").strip().lower()
    if mode not in {"analysis", "architecture", "debug", "decision", "review", "decomposition"}:
        mode = "analysis"

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "recommended_actions": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "verification": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["summary", "recommended_actions", "risks", "verification", "confidence"],
        "additionalProperties": False,
    }
    prompt = f"""You are a reasoning specialist called by Norm. Norm is the only execution agent.
Do not modify files, run mutating commands, contact services, or claim actions were performed.
Reason only from the QUESTION and EVIDENCE supplied below. Return the requested structured JSON.
Recommendations must be concrete enough for Norm to execute and verify. If evidence is insufficient,
say what Norm should inspect next rather than inventing facts.

MODE: {mode}
QUESTION:
{question}

EVIDENCE FROM NORM:
{context}
"""

    with tempfile.TemporaryDirectory(prefix="norm-codex-") as temp_name:
        temp = Path(temp_name)
        schema_path = temp / "schema.json"
        output_path = temp / "last-message.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        args = [
            "exec", "--ignore-user-config", "-c", f'model_reasoning_effort="{str(cfg["reasoning_effort"]).strip()}"',
            "--sandbox", "read-only", "--ephemeral", "--ignore-rules",
            "--skip-git-repo-check", "--color", "never",
            "--output-schema", str(schema_path), "--output-last-message", str(output_path),
            "-C", str(temp), "-",
        ]
        if str(cfg["model"]).strip():
            args[1:1] = ["--model", str(cfg["model"]).strip()]
        try:
            proc = subprocess.run(
                _command(str(cfg["executable"]), args),
                input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=int(cfg["timeout_seconds"]),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Codex reasoning timed out after {cfg['timeout_seconds']} seconds") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-8000:]
            raise RuntimeError(f"Codex reasoning failed rc={proc.returncode}: {detail}")
        if not output_path.is_file():
            raise RuntimeError("Codex completed without writing the structured final message")
        raw = output_path.read_text(encoding="utf-8-sig").strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Codex final message was not valid JSON: {raw[:2000]}") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Codex final message was not a JSON object")
        return {
            "ok": True,
            "mode": mode,
            "reasoning": result,
            "policy": {
                "execution_owner": "Norm",
                "codex_sandbox": "read-only",
                "ephemeral": True,
                "evidence_supplied_by_norm_only": True,
            },
        }
