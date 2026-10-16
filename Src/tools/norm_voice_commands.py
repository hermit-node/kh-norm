"""Deterministic, local operator controls for the installed voice_profile plugin.

These bypass the model and the Redis prompt queue. Do not accept arbitrary plugin
names or method names from console text; only the allowlisted voice operations exist.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import sys
import threading
from pathlib import Path
from typing import Callable


# Long-running corpus builds are serialized within this console instance.
_BUILD_LOCK = threading.Lock()
VOICE_COMMANDS = frozenset({
    "/voice", "/voice-list", "/voice-active", "/voice-activate",
    "/voice-deactivate", "/voice-build", "/voice-build-chat",
    "/voice-rebuild", "/voice-add-text", "/voice-summary",
    "/voice-persona", "/voice-preview", "/voice-persona-preview",
    "/voice-search", "/voice-compose", "/voice-delete", "/voice-tune", "/voice-test",
})


def _parts(line: str) -> list[str]:
    # posix=False preserves Windows backslashes and accepts quoted paths.
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p
            for p in shlex.split(line, posix=False)]


def _plugin(root: Path):
    root = Path(root).resolve()
    folder = root / "plugins" / "voice_profile"
    from norm_runtime.plugin_identity import verify_identity
    metadata = json.loads((folder / "plugin.json").read_text(encoding="utf-8-sig"))
    verify_identity(folder, metadata)  # do not execute tampered plugin code
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from plugins.voice_profile.src import main
    return main


def _profile_name(voice, value: str) -> str:
    """Find existing names case-insensitively, without silently inventing a profile."""
    found = [str(row["profile_name"]) for row in voice.list_profiles().get("profiles", [])]
    if value in found:
        return value
    matches = [name for name in found if name.casefold() == value.casefold()]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Voice profile {value!r} not found. Use /voice-list.")


def _flags(args: list[str], *, supported: set[str]) -> tuple[list[str], dict[str, str | bool]]:
    positional: list[str] = []
    flags: dict[str, str | bool] = {}
    i = 0
    while i < len(args):
        part = args[i]
        if part.startswith("--"):
            if part not in supported:
                raise ValueError(f"Unsupported option {part}. Use /help voice-profiler.")
            if part in {"--no-model", "--no-persona", "--flat", "--force", "--confirm"}:
                flags[part] = True
            else:
                i += 1
                if i >= len(args) or args[i].startswith("--"):
                    raise ValueError(f"{part} requires a value")
                flags[part] = args[i]
        else:
            positional.append(part)
        i += 1
    return positional, flags


def _integer(flags: dict, name: str, default: int, lo: int, hi: int) -> int:
    value = int(flags.get(name, default))
    if value < lo or value > hi:
        raise ValueError(f"{name} must be in [{lo}, {hi}]")
    return value


def _print_summary(value: dict, out: Callable[[str], None]) -> None:
    out(json.dumps(value, ensure_ascii=False, indent=2))


def _active_status(voice, root: Path) -> dict:
    from norm_runtime.voice_profile_context import load_active_voice_context
    pointer = voice.active_profile()
    if not pointer.get("active"):
        return {"active": False, "profile_name": None, "prompt_valid": False}
    valid = bool(load_active_voice_context(root))
    result = dict(pointer)
    result["prompt_valid"] = valid
    try:
        wanted = voice.render_voice_prompt(
            pointer["profile_name"], anchor_count=int(pointer.get("anchor_count") or 0)
        )["prompt"]
        result["profile_changed_since_activation"] = (
            hashlib.sha256(wanted.encode("utf-8")).hexdigest() != pointer.get("prompt_sha256")
        )
    except (OSError, ValueError, KeyError, TypeError):
        result["profile_changed_since_activation"] = None
    return result


def _activate(voice, root: Path, name: str, anchors: int | None, force: bool, out: Callable[[str], None]) -> None:
    name = _profile_name(voice, name)
    prior = _active_status(voice, root)
    if anchors is None:
        # Repeated activation of the same name shouldn't silently revert 6 anchors to 3.
        anchors = (int(prior.get("anchor_count") or 0) if prior.get("profile_name") == name else 3)
    proposed = voice.render_voice_prompt(name, anchor_count=anchors)["prompt"]
    expected_sha = hashlib.sha256(proposed.encode("utf-8")).hexdigest()
    if (not force and prior.get("active") and prior.get("profile_name") == name
            and prior.get("anchor_count") == anchors and prior.get("prompt_valid")
            and prior.get("prompt_sha256") == expected_sha):
        out(f"Voice '{name}' already active and byte-for-byte current ({anchors} anchors). No rewrite needed.")
        return
    result = voice.activate_profile(name, anchor_count=anchors)
    out(f"Activated voice '{name}' with {result['anchor_count']} anchor(s); prompt SHA-256 {result['prompt_sha256'][:16]}…")


def _action(line: str, root: Path, out: Callable[[str], None]) -> None:
    tokens = _parts(line)
    command = tokens[0].lower()
    args = tokens[1:]
    voice = _plugin(root)
    if command == "/voice":
        out("Voice controls: /voice-list, /voice-active, /voice-activate NAME, /voice-deactivate. Full list: /help voice-profiler")
    elif command == "/voice-list":
        profiles = voice.list_profiles()
        rows = profiles.get("profiles") or []
        if not rows:
            out("No built voice profiles.")
        for row in rows:
            out(f"{'*' if row.get('active') else ' '} {row['profile_name']:<28} sources={row.get('sources', 0)} anchors={row.get('anchors', 0)}")
    elif command == "/voice-active":
        _print_summary(_active_status(voice, root), out)
    elif command == "/voice-activate":
        items, flags = _flags(args, supported={"--anchors", "--force"})
        if len(items) != 1:
            raise ValueError("Usage: /voice-activate NAME [--anchors 0..8] [--force]")
        anchors = _integer(flags, "--anchors", 3, 0, 8) if "--anchors" in flags else None
        _activate(voice, root, items[0], anchors, bool(flags.get("--force")), out)
    elif command == "/voice-deactivate":
        if args:
            raise ValueError("Usage: /voice-deactivate")
        active = voice.active_profile()
        if not active.get("active"):
            out("No voice profile is active; nothing changed.")
        else:
            voice.deactivate_profile()
            out(f"Deactivated '{active['profile_name']}'. Stored profile remains available.")
    elif command == "/voice-build":
        items, flags = _flags(args, supported={"--mode", "--anchors", "--no-model", "--no-persona", "--flat"})
        if len(items) != 2:
            raise ValueError('Usage: /voice-build NAME "PDF FOLDER" [--mode text|auto|hybrid|vision] [--anchors N]')
        mode = str(flags.get("--mode") or "text").lower()
        if mode not in {"text", "auto", "hybrid", "vision"}:
            raise ValueError("--mode must be text, auto, hybrid, or vision")
        name, folder = items
        out(f"Building '{name}' from {folder} in {mode} mode (not queued). This may take a while…")
        result = voice.build_from_pdf_folder(
            name, folder, extraction_mode=mode, recursive=not bool(flags.get("--flat")),
            use_model=not bool(flags.get("--no-model")),
            anchor_count=_integer(flags, "--anchors", 6, 1, 12),
            build_persona=not bool(flags.get("--no-persona")),
        )
        _print_summary(result, out)
        out("Build saved. If this profile was active, run /voice-activate NAME to refresh the live prompt.")
    elif command == "/voice-build-chat":
        items, flags = _flags(args, supported={"--project", "--messages", "--anchors", "--no-model"})
        if len(items) != 1:
            raise ValueError("Usage: /voice-build-chat NAME [--project ID] [--messages N] [--anchors N]")
        _print_summary(voice.build_from_chat_history(
            items[0], project_id=str(flags.get("--project") or ""),
            message_limit=_integer(flags, "--messages", 1000, 1, 10000),
            use_model=not bool(flags.get("--no-model")),
            anchor_count=_integer(flags, "--anchors", 6, 1, 12),
        ), out)
        out("If the profile was active, run /voice-activate NAME to refresh the live prompt.")
    elif command == "/voice-rebuild":
        items, flags = _flags(args, supported={"--anchors", "--no-model", "--no-persona"})
        if len(items) != 1:
            raise ValueError("Usage: /voice-rebuild NAME [--anchors N] [--no-model] [--no-persona]")
        _print_summary(voice.build_profile(
            _profile_name(voice, items[0]),
            use_model=not bool(flags.get("--no-model")),
            anchor_count=_integer(flags, "--anchors", 6, 1, 12),
            build_persona=not bool(flags.get("--no-persona")),
        ), out)
        out("If active, run /voice-activate NAME to refresh the live prompt.")
    elif command == "/voice-add-text":
        items, flags = _flags(args, supported={"--source"})
        if len(items) != 2:
            raise ValueError('Usage: /voice-add-text NAME "TEXT FILE" [--source LABEL]')
        file = Path(items[1]).resolve()
        from norm_runtime.file_access_policy import load_file_access_policy, authorize_path
        policy = load_file_access_policy(root, capability="voice_profile")
        authorize_path(file, policy.read_roots, access="read", hardlock=True)
        text = file.read_text(encoding="utf-8-sig")
        _print_summary(voice.ingest_text(items[0], str(flags.get("--source") or file), text), out)
        out("Text ingested. Run /voice-rebuild NAME before activation.")
    elif command in {"/voice-summary", "/voice-persona", "/voice-preview", "/voice-persona-preview"}:
        items, flags = _flags(args, supported={"--anchors"} if command == "/voice-preview" else set())
        if len(items) != 1:
            raise ValueError(f"Usage: {command} NAME")
        name = _profile_name(voice, items[0])
        fn = {
            "/voice-summary": voice.profile_summary,
            "/voice-persona": voice.persona_summary,
            "/voice-preview": voice.render_voice_prompt,
            "/voice-persona-preview": voice.render_persona_prompt,
        }[command]
        if command == "/voice-preview":
            out(fn(name, anchor_count=_integer(flags, "--anchors", 3, 0, 8))["prompt"])
        elif command == "/voice-persona-preview":
            out(fn(name)["prompt"])
        else:
            _print_summary(fn(name), out)
    elif command in {"/voice-search", "/voice-compose"}:
        if len(args) < 2:
            raise ValueError(f"Usage: {command} NAME QUERY")
        name = _profile_name(voice, args[0])
        query = " ".join(args[1:])
        _print_summary(voice.retrieve_context(name, query) if command == "/voice-search"
                       else voice.compose_context(name, query), out)
    elif command == "/voice-tune":
        items, flags = _flags(args, supported={"--preset", "--clear"})
        if not items:
            raise ValueError('Usage: /voice-tune NAME [--preset cockney | --clear | "STYLE TEXT"]')
        name = _profile_name(voice, items[0])
        if flags.get("--preset") and len(items) > 1:
            raise ValueError("Choose either preset or text")
        if flags.get("--clear") and len(items) > 1:
            raise ValueError("Choose either --clear or text")
        result = voice._operator_tune_voice(name, text=" ".join(items[1:]),
                                 preset=str(flags.get("--preset") or ""),
                                 clear=bool(flags.get("--clear")))
        _print_summary(result, out)
        if result.get("changed"):
            out(f"Run /voice-activate {name} to refresh the live prompt. No corpus rebuild needed.")
    elif command == "/voice-test":
        items, flags = _flags(args, supported={"--anchors"})
        if len(items) < 2:
            raise ValueError('Usage: /voice-test NAME "QUESTION" [--anchors 0..8]')
        result = voice._operator_test_voice(_profile_name(voice, items[0]), " ".join(items[1:]),
                                  anchor_count=_integer(flags, "--anchors", 3, 0, 8))
        _print_summary(result, out)
    elif command == "/voice-delete":
        items, flags = _flags(args, supported={"--confirm"})
        if len(items) != 1 or not flags.get("--confirm"):
            raise ValueError("Usage: /voice-delete NAME --confirm (must first deactivate an active profile)")
        _print_summary(voice.clear_profile(_profile_name(voice, items[0]), confirm=True), out)
    else:
        raise ValueError(f"Unknown voice command {command}")


def run_voice_command(line: str, root: Path, out: Callable[[str], None] = print) -> bool:
    """Return whether this was a recognized command. Build operations don't block input."""
    command = line.split(maxsplit=1)[0].lower() if line.strip() else ""
    if command not in VOICE_COMMANDS:
        return False
    if _BUILD_LOCK.locked() and command in {
        "/voice-activate", "/voice-deactivate", "/voice-delete", "/voice-add-text", "/voice-tune"
    }:
        out("A voice build is in progress; no profile mutation was attempted. Retry after it finishes.")
        return True
    if command in {"/voice-build", "/voice-build-chat", "/voice-rebuild", "/voice-test"}:
        if not _BUILD_LOCK.acquire(blocking=False):
            out("A voice build is already running in this console. No duplicate was started.")
            return True

        def task() -> None:
            try:
                _action(line, root, out)
            except Exception as exc:
                out(f"Voice command failed: {type(exc).__name__}: {exc}")
            finally:
                _BUILD_LOCK.release()

        threading.Thread(target=task, name="norm-voice-build", daemon=True).start()
        out("Voice build started locally; keep this console open until completion. Other commands remain available.")
    else:
        try:
            _action(line, root, out)
        except Exception as exc:
            out(f"Voice command failed: {type(exc).__name__}: {exc}")
    return True
