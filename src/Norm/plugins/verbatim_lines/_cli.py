from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _read_stdin() -> str:
    try:
        return sys.stdin.read()
    except KeyboardInterrupt:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a file if needed, then append stdin verbatim or insert it before a one-based line."
    )
    parser.add_argument("path", help="Target file")
    parser.add_argument("--line", type=int, default=None, help="Insert before this one-based line; omit to append.")
    args = parser.parse_args()
    if args.line is not None and args.line < 1:
        parser.error("--line must be 1 or greater")
    incoming = _read_stdin()
    if not incoming:
        print("No input received; file unchanged.")
        return 0
    target = Path(args.path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    if args.line is None:
        with target.open("a", encoding="utf-8", newline="") as handle:
            handle.write(incoming)
        print(f"Appended verbatim text to {target.resolve()}")
        return 0
    with target.open("r", encoding="utf-8", newline="") as handle:
        existing = handle.readlines()
    index = min(args.line - 1, len(existing))
    chunks = incoming.splitlines(keepends=True)
    if incoming and not chunks:
        chunks = [incoming]
    existing[index:index] = chunks
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.writelines(existing)
    print(f"Inserted verbatim text at line {index + 1}: {target.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
