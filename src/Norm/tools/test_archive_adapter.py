from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from norm_runtime.archive_adapter import (
    archive_manifest,
    compare_archive_to_directory,
    hash_archive_member,
    read_archive_member,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="norm-archive-adapter-") as tmp:
        root = Path(tmp)
        folder = root / "folder"
        folder.mkdir()
        (folder / "a.txt").write_bytes(b"hello\n")
        (folder / "sub").mkdir()
        (folder / "sub" / "b.bin").write_bytes(b"abc123" * 1000)
        archive = root / "sample.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            handle.write(folder / "a.txt", "a.txt")
            handle.write(folder / "sub" / "b.bin", "sub/b.bin")

        manifest = archive_manifest(archive)
        assert manifest["file_count"] == 2
        assert manifest["total_uncompressed_bytes"] == 6 + 6000
        assert manifest["tree_size_fingerprint"]

        partial = read_archive_member(archive, "sub/b.bin", start_byte=3, max_bytes=10, mode="bytes_base64")
        assert partial["returned_bytes"] == 10
        assert partial["truncated"] is True

        hashed = hash_archive_member(archive, "a.txt")
        assert hashed["size"] == 6 and len(hashed["sha256"]) == 64

        comparison = compare_archive_to_directory(archive, folder)
        assert comparison["tree_size_match"] is True
        assert comparison["content_identical"] is True
        assert comparison["sha_files_checked"] == 2

        (folder / "a.txt").write_bytes(b"HELLO\n")
        mismatch = compare_archive_to_directory(archive, folder)
        assert mismatch["tree_size_match"] is True
        assert mismatch["content_identical"] is False
        assert mismatch["sha_mismatches"]

    print("PASS: archive manifest -> tree/size -> SHA -> selective member workflow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
