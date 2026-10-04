from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core" / "requirements.txt"
LOCK = ROOT / "tools" / "requirements-lock.txt"


def names(path: Path) -> set[str]:
    result: set[str] = set()
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        base = line.split("==", 1)[0].split("[", 1)[0].strip().lower().replace("_", "-")
        result.add(base)
    return result


def main() -> int:
    core = names(CORE)
    lock = names(LOCK)
    direct = {
        "pytest", "redis", "psycopg", "psycopg-pool", "aiohttp", "pillow",
        "cryptography", "pymupdf", "numpy", "opencv-python", "kornia",
        "prompt-toolkit", "rich", "tzdata",
    }
    missing_core = sorted(direct - core)
    missing_lock = sorted(direct - lock)
    if missing_core or missing_lock:
        if missing_core:
            print("Missing from core/requirements.txt:", ", ".join(missing_core))
        if missing_lock:
            print("Missing from tools/requirements-lock.txt:", ", ".join(missing_lock))
        return 1

    settings = (ROOT / "config" / "settings.ini").read_text(encoding="utf-8-sig")
    if "torch_version =" not in settings or "torch_index_url =" not in settings:
        print("PyTorch must remain explicitly installer-managed through torch_version/torch_index_url.")
        return 1

    print("PASS: direct dependency manifests are complete; PyTorch remains installer-managed for CUDA selection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
