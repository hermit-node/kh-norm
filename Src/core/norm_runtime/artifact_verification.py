"""Deterministic validation for artifacts written by trusted tool results.

The caller provides *actual returned tool evidence*, never model-reported
artifact claims. The read-back is local, streamed, and checks a stable file
identity. A valid artifact file does not prove the user's content is complete.
"""
from __future__ import annotations
from pathlib import Path
from hashlib import sha256


def _hash_file(path: str, expected_hash: str = "", expected_size: int = 0) -> dict:
    f=Path(path)
    info={"path":path,"sha256":expected_hash,"verified":False}
    try:
        if not f.is_file():
            return info
        first=f.stat()
        if first.st_size<=0:
            return info
        if expected_size and first.st_size!=expected_size:
            return info
        digest=sha256()
        with f.open("rb") as handle:
            header=handle.read(8)
            digest.update(header)
            while True:
                chunk=handle.read(1024*1024)
                if not chunk:break
                digest.update(chunk)
        second=f.stat()
        if (first.st_size,first.st_mtime_ns,first.st_ino)!=(
            second.st_size,second.st_mtime_ns,second.st_ino):
            return info
        actual=digest.hexdigest()
        if expected_hash and actual.casefold()!=expected_hash.casefold():
            return info
        # Only claim PDF file validity if the observed bytes have a PDF header.
        if f.suffix.lower()==".pdf" and not header.startswith(b"%PDF-"):
            return info
        return {"path":path,"sha256":actual,"verified":True,
                "size_bytes":first.st_size,
                "verification_method":"independent_streaming_sha256_readback"}
    except (OSError,ValueError,PermissionError):
        return info


def snapshot(evidence: list[dict]) -> list[dict]:
    writes={}
    verified=set()
    generated={}
    for item in evidence:
        if not isinstance(item,dict):continue
        tool=str(item.get("tool") or "")
        result=item.get("result")
        if not isinstance(result,dict) or not result.get("ok"):
            continue
        inner=result.get("result")
        if isinstance(inner,dict) and ("plugin" in result or "plugin_fingerprint" in result):
            if not inner.get("ok",True):
                continue
            observed=inner
        else:
            observed=result
        path=str(observed.get("path") or "")
        digest=str(observed.get("sha256") or "")
        if tool == "write_file" and path and observed.get("unchanged") is True and observed.get("prewrite_verified") is True:
            # Existing identical file was inspected BEFORE the attempted write.
            # It was not mutated. Independently verify its current bytes instead.
            generated[path] = _hash_file(path, expected_hash=digest, expected_size=int(observed.get("bytes") or 0))
            generated[path]["verification_method"] = "verified_existing_identical_noop"
            continue
        if (tool in {"write_file","replace_text"} or observed.get("file_mutation")) and path:
            writes[path]=digest
            verified={x for x in verified if x[0]!=path}
            generated.pop(path,None)
        elif tool=="read_file" and path and digest and writes.get(path)==digest:
            verified.add((path,digest))
        # Plugins can produce files without going through native write_file.
        # Only a successful artifact-producing plugin return can authorize
        # independent readback, NEVER a model's description or a generic read.
        if tool.startswith("plugin_") and isinstance(observed,dict):
            artifact_path=str(observed.get("pdf") or observed.get("artifact_path")
                              or observed.get("output_file") or "")[:4096]
            if artifact_path:
                size=observed.get("size_bytes") or observed.get("file_size") or 0
                try:size=int(size)
                except (ValueError,TypeError):size=0
                generated[artifact_path]=_hash_file(
                    artifact_path,expected_hash=str(observed.get("sha256") or ""),
                    expected_size=max(0,size))
    snapshots=[
        {"path":path,"sha256":digest,"verified":(path,digest) in verified,
         "verification_method":"tool_write_then_matching_read"}
        for path,digest in sorted(writes.items())
    ]
    for path,value in sorted(generated.items()):
        if path not in writes:
            snapshots.append(value)
    return snapshots
