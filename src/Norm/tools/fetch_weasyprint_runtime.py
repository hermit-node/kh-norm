from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

URL="https://github.com/Kozea/WeasyPrint/releases/download/v70.0/weasyprint-windows-onedir.zip"
EXPECTED_SHA256="ab1151f210b4e6bb7aa7a79e91a67e8ddb760094c107bfda55241b6aaefe7d53"
HERE=Path(__file__).resolve().parent
DEFAULT_DEST=HERE/"weasyprint"/"runtime"

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()

def main() -> int:
    parser=argparse.ArgumentParser(description="Fetch the verified WeasyPrint 70.0 Windows runtime used by Norm.")
    parser.add_argument("--archive",type=Path)
    parser.add_argument("--destination",type=Path,default=DEFAULT_DEST)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="norm-weasyprint-") as td:
        work=Path(td)
        archive=args.archive.resolve() if args.archive else work/"weasyprint-windows-onedir.zip"
        if not args.archive:
            print("Downloading",URL)
            urllib.request.urlretrieve(URL,archive)
        actual=sha256(archive)
        if actual.lower()!=EXPECTED_SHA256:
            raise SystemExit(f"SHA-256 mismatch: expected {EXPECTED_SHA256}, got {actual}")
        extract=work/"extract"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract)
        source=extract/"onedir"/"weasyprint"
        if not (source/"weasyprint.exe").is_file():
            raise SystemExit("Upstream archive layout not recognized.")
        destination=args.destination.resolve()
        shutil.rmtree(destination,ignore_errors=True)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(source,destination)
        print("Installed verified runtime:",destination)
        print("SHA256="+actual)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
