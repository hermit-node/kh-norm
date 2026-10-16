"""Small local /help index; plugin manuals are data, never executable code."""
from __future__ import annotations

from pathlib import Path


ALIASES = {"voice-profiler": "voice_profile", "voice-profile": "voice_profile",
           "vision": "vision_parse", "pdf-reader": "vision_parse"}


def plugin_help_files(root: Path) -> dict[str, Path]:
    """Only discover immediate plugin folders with an ordinary help.txt."""
    base = (Path(root) / "plugins").resolve()
    if not base.is_dir():
        return {}
    result: dict[str, Path] = {}
    for folder in sorted(base.iterdir(), key=lambda p: p.name.casefold()):
        if folder.is_symlink() or not folder.is_dir():
            continue
        manual = folder / "help.txt"
        if not manual.is_symlink() and manual.is_file() and manual.resolve().is_relative_to(base):
            result[folder.name] = manual
    return result


def get_help(root: Path, topic: str = "") -> str:
    topic = "-".join(str(topic or "").strip().lower().split()).replace("_", "-")
    if not topic:
        path = Path(root) / "docs" / "help_menu.txt"
        try:
            return path.read_text(encoding="utf-8-sig").rstrip()
        except OSError as exc:
            return f"Could not read Norm help: {exc}"
    installed = plugin_help_files(root)
    if topic in {"plugins", "plugin"}:
        if not installed:
            return "No plugins with help.txt are installed."
        lines = ["Plugins with help.txt:"]
        for name in installed:
            display = "voice-profiler" if name == "voice_profile" else name.replace("_", "-")
            lines.append(f"  {display:<23} /help {display}")
        return "\n".join(lines)
    key = ALIASES.get(topic, topic.replace("-", "_"))
    if key not in installed:
        return f"Unknown help topic '{topic}'. Use /help plugins to list available plugin manuals."
    try:
        return installed[key].read_text(encoding="utf-8-sig").rstrip()
    except OSError as exc:
        return f"Could not read {key} help: {exc}"
