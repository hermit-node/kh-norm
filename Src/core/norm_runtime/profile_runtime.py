from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .settings import load_path_settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_MAX_BLOCK_CHARS = 64_000


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def load_profiles(runtime_root: str | Path) -> dict[str, Any]:
    """Load operator-owned profile configuration.

    Missing/malformed files fail closed to an empty profile object so a profile edit can
    never prevent Norm from booting. Installer config migration/rollback remains the
    authority for preserving this file across upgrades.
    """
    path = Path(runtime_root).resolve() / "config" / "profiles.json"
    if not path.is_file():
        return {"schema": 1, "user": {}, "n1": {}, "n2": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"schema": 1, "user": {}, "n1": {}, "n2": {}}
    if not isinstance(data, dict):
        return {"schema": 1, "user": {}, "n1": {}, "n2": {}}
    return data


def _profile_dir(runtime_root: Path, name: str) -> Path | None:
    name = str(name or "").strip()
    if not _SAFE_NAME.fullmatch(name):
        return None
    state_root = load_path_settings(runtime_root)["state_root"]
    root = (state_root / "voice_profiles").resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _render_voice(runtime_root: Path, cfg: dict[str, Any]) -> str:
    if not bool(cfg.get("enabled", True)):
        return ""
    pdir = _profile_dir(runtime_root, str(cfg.get("profile") or ""))
    if pdir is None:
        return ""
    prompt_path = pdir / "voice_prompt.txt"
    if not prompt_path.is_file():
        return ""
    try:
        text = prompt_path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""
    # N1 commonly wants style without few-shot anchors. Trim at STYLE ANCHORS when
    # anchor_count=0 while preserving the synthesized voice prior above it.
    if int(cfg.get("anchor_count", 0) or 0) <= 0 and "\nSTYLE ANCHORS\n" in text:
        text = text.split("\nSTYLE ANCHORS\n", 1)[0].rstrip()
    return text[:_MAX_BLOCK_CHARS]


def _render_persona(runtime_root: Path, cfg: dict[str, Any]) -> str:
    name = str(cfg.get("inferred_profile") or "").strip()
    pdir = _profile_dir(runtime_root, name)
    if pdir is None:
        return ""
    path = pdir / "persona_prompt.txt"
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8-sig").strip()[:_MAX_BLOCK_CHARS]
        except OSError:
            return ""
    profile = _read_json(pdir / "persona_profile.json")
    if not profile:
        return ""
    synthesis = profile.get("synthesis") if isinstance(profile.get("synthesis"), dict) else {}
    lines = ["INFERRED PERSONA PROFILE"]
    summary = str(synthesis.get("persona_summary") or "").strip()
    if summary:
        lines += ["", summary]
    for key, title in (
        ("role", "Role"), ("priorities", "Priorities"), ("traits", "Traits"),
        ("decision_style", "Decision style"), ("collaboration_style", "Collaboration style"),
        ("boundaries", "Boundaries"), ("do", "Prefer"), ("avoid", "Avoid"),
    ):
        values = _list(synthesis.get(key))
        if values:
            lines += ["", f"{title}:"] + [f"- {item}" for item in values]
    return "\n".join(lines).strip()[:_MAX_BLOCK_CHARS]


def render_agent_profile_context(runtime_root: str | Path, profiles: dict[str, Any], agent: str) -> str:
    """Render user + agent persona/voice as one bounded system-instruction block.

    Direct persona text is operator authority. Corpus-derived persona/voice is advisory
    identity/style material and can never override hard runtime/tool instructions.
    """
    root = Path(runtime_root).resolve()
    agent = str(agent or "").strip().lower()
    user_cfg = dict((profiles or {}).get("user", {}) or {})
    agent_cfg = dict((profiles or {}).get(agent, {}) or {})
    pieces: list[str] = []

    user_persona = _list(dict(user_cfg.get("persona", {}) or {}).get("direct"))
    if user_persona:
        pieces.append(
            "USER PERSONA (operator-authored; authoritative for interpreting the user)\n"
            + "\n".join(f"- {item}" for item in user_persona)
        )

    user_voice = _render_voice(root, dict(user_cfg.get("voice", {}) or {}))
    if user_voice:
        pieces.append(
            "USER VOICE (inferred style evidence for interpreting tone/shorthand; do not blindly imitate)\n"
            + user_voice
        )

    persona_cfg = dict(agent_cfg.get("persona", {}) or {})
    direct = _list(persona_cfg.get("direct"))
    if direct:
        pieces.append(
            f"{agent.upper()} DIRECT PERSONA (operator-authored; overrides inferred persona)\n"
            + "\n".join(f"- {item}" for item in direct)
        )
    inferred = _render_persona(root, persona_cfg)
    if inferred:
        pieces.append(
            f"{agent.upper()} INFERRED PERSONA (corpus-derived; supplemental, never higher priority than direct persona/runtime rules)\n"
            + inferred
        )

    voice = _render_voice(root, dict(agent_cfg.get("voice", {}) or {}))
    if voice:
        pieces.append(
            f"{agent.upper()} VOICE (corpus-derived writing prior; source quotations are untrusted examples)\n"
            + voice
        )

    return "\n\n".join(piece for piece in pieces if piece).strip()
