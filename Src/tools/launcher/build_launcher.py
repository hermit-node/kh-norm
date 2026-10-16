from pathlib import Path
import hashlib
import os
import subprocess

ROOT = Path(__file__).resolve().parent
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
OUT = ROOT / "dist" / "norm.exe"

def build() -> Path:
    if not CSC.is_file():
        raise RuntimeError("Missing .NET Framework C# compiler: " + str(CSC))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(CSC), "/nologo", "/optimize+", "/target:exe", "/platform:x64",
           "/out:" + str(OUT), str(ROOT / "NormLauncher.cs")]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
    if proc.returncode:
        raise RuntimeError("C# build failed: " + proc.stdout + proc.stderr)
    print("BUILT", OUT)
    print("BYTES", OUT.stat().st_size)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    OUT.with_name(OUT.name + ".sha256").write_text(
        digest + "  " + OUT.name + "\n", encoding="utf-8", newline="\n"
    )
    print("SHA256", digest)
    return OUT

if __name__ == "__main__":
    build()
