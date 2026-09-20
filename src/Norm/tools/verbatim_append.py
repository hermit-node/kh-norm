from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a file if needed, then append stdin lines verbatim until Ctrl+C/EOF."
    )
    parser.add_argument("path", help="File to create/append to")
    args = parser.parse_args()

    target = Path(args.path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)

    print(f"Appending to: {target.resolve()}")
    print("Enter/paste lines. Ctrl+C or Ctrl+Z then Enter ends input.")

    try:
        with target.open("a", encoding="utf-8", newline="") as handle:
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                handle.write(line + "\n")
                handle.flush()
    except KeyboardInterrupt:
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())