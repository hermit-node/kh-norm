from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _read_stdin_lines() -> list[str]:
    lines: list[str] = []
    try:
        for line in sys.stdin:
            lines.append(line if line.endswith(("\n", "\r")) else line + "\n")
    except KeyboardInterrupt:
        pass
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a file if needed, then append stdin verbatim or insert it starting at a 1-based line number."
    )
    parser.add_argument("path", help="Target file")
    parser.add_argument(
        "--line",
        type=int,
        default=None,
        help="Insert before this 1-based line number. Omit to append.",
    )
    args = parser.parse_args()

    target = Path(args.path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)

    if args.line is not None and args.line < 1:
        parser.error("--line must be 1 or greater")

    print(f"Target: {target.resolve()}")
    print("Paste/type content, then Ctrl+Z + Enter (Windows) or Ctrl+D (Unix) to finish.")
    incoming = _read_stdin_lines()
    if not incoming:
        print("No input received; file unchanged.")
        return 0

    if args.line is None:
        with target.open("a", encoding="utf-8", newline="") as handle:
            handle.writelines(incoming)
        print(f"Appended {len(incoming)} line(s).")
        return 0

    with target.open("r", encoding="utf-8", newline="") as handle:
        existing = handle.readlines()
    index = min(args.line - 1, len(existing))
    existing[index:index] = incoming
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.writelines(existing)
    print(f"Inserted {len(incoming)} line(s) at line {index + 1}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())