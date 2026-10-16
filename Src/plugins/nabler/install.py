"""Optional local-script import registration for Nabler (repeatable and non-destructive).

The native Schema-2 plugin loads automatically; this helper is only needed
for standalone scripts using ``from nabler import table`` outside the plugin.
"""
from __future__ import annotations

from pathlib import Path


def register(root: Path | None = None) -> Path:
    package = Path(__file__).resolve().parent
    root = root or package.parents[1]
    src = package / "src"
    if not (src / "nabler.py").is_file():
        raise FileNotFoundError(src / "nabler.py")
    site = root / ".venv" / "Lib" / "site-packages"
    if not site.is_dir():
        raise FileNotFoundError(f"Norm venv site-packages not present: {site}")
    pth = site / "nabler-plugin.pth"
    wanted = str(src.resolve()) + "\n"
    if not pth.exists() or pth.read_text(encoding="utf-8") != wanted:
        pth.write_text(wanted, encoding="utf-8")
    return pth


if __name__ == "__main__":
    print("Registered Nabler for standalone Python imports:", register())
