from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_MAX_ACTIVE_PROMPT_CHARS = 64_000
_MAX_ACTIVE_PROMPT_BYTES = 256_000


def load_active_voice_context(runtime_root: str | Path) -> str:
    """Load a validated active style prompt without executing plugin code.

    Missing, malformed, oversized, or hash-mismatched state fails closed to
    an empty string so a damaged profile cannot prevent Norm from starting.
    """
    root = Path(runtime_root).resolve()
    state_root = (root / "state" / "voice_profiles").resolve()
    pointer_path = state_root / "active.json"
    prompt_path = state_root / "active_prompt.txt"
    if not pointer_path.is_file() or not prompt_path.is_file():
        return ""

    try:
        if pointer_path.stat().st_size > 32_768:
            return ""
        pointer = json.loads(pointer_path.read_text(encoding="utf-8-sig"))
        if not isinstance(pointer, dict):
            return ""
        profile_name = str(pointer.get("profile_name") or "")
        expected = str(pointer.get("prompt_sha256") or "")
        if not _SAFE_NAME.fullmatch(profile_name):
            return ""
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            return ""

        profile_dir = (state_root / profile_name).resolve()
        if not profile_dir.is_relative_to(state_root) or not (profile_dir / "voice_profile.json").is_file():
            return ""

        if prompt_path.stat().st_size > _MAX_ACTIVE_PROMPT_BYTES:
            return ""
        prompt = prompt_path.read_text(encoding="utf-8-sig")
        if len(prompt) > _MAX_ACTIVE_PROMPT_CHARS:
            return ""
        actual = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if actual != expected:
            return ""
        return prompt.strip()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ""
