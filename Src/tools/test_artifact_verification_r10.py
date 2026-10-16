"""Artifact regression: N1 accepts independently readable generated PDF.

Filesystem read-back must verify actual bytes, never a model claim.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from hashlib import sha256
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"core"))
from norm_runtime.prompt_worker import PromptWorker

with TemporaryDirectory() as tmp:
    path=Path(tmp)/"sample.pdf"
    payload=b"%PDF-1.7\\n1 0 obj\\n<<>>\\nendobj\\ntrailer <<>>\\n%%EOF\\n"
    path.write_bytes(payload)
    digest=sha256(payload).hexdigest()
    evidence=[{"tool":"plugin_shared_browser__browser_shared_pdf",
               "result":{"ok":True,"plugin":"shared_browser",
                          "result":{"ok":True,"pdf":str(path),"messages":2}}}]
    artifacts=PromptWorker._artifact_snapshot(evidence)
    assert len(artifacts)==1,artifacts
    assert artifacts[0]["verified"] and artifacts[0]["sha256"]==digest,artifacts
    assert PromptWorker._final_artifact_state({
        "command":{"expected_output":{"type":"artifact_and_response"}},
        "artifacts":artifacts
    })
    print("PASS plugin-generated PDF independently hashed, N1 artifact gate accepts")
    path.write_bytes(b"not a real PDF")
    assert not PromptWorker._artifact_snapshot(evidence)[0]["verified"]
    print("PASS invalid PDF bytes fail deterministic readback")
    path.write_bytes(payload)
    bad=[{"tool":"plugin_shared_browser__browser_shared_pdf",
          "result":{"ok":True,"plugin":"shared_browser",
                    "result":{"ok":True,"pdf":str(path),"sha256":"0"*64}}}]
    assert not PromptWorker._artifact_snapshot(bad)[0]["verified"]
    print("PASS claimed hash mismatch blocks acceptance")
    bad2=[{"tool":"plugin_shared_browser__browser_shared_pdf",
          "result":{"ok":False,"plugin":"shared_browser","result":{"pdf":str(path)}}}]
    assert PromptWorker._artifact_snapshot(bad2)==[]
    print("PASS failed tool does not prove artifact")
    fake=[{"tool":"read_file","result":{"ok":True,"pdf":str(path),"sha256":digest}}]
    assert PromptWorker._artifact_snapshot(fake)==[]
    print("PASS arbitrary read path cannot masquerade as output")
    native=[{"tool":"write_file","result":{"ok":True,"path":str(path),"sha256":digest}},
            {"tool":"read_file","result":{"ok":True,"path":str(path),"sha256":digest}}]
    assert PromptWorker._artifact_snapshot(native)[0]["verified"]
    print("PASS previous native write/read sequence preserved")
    native_bad=[{"tool":"write_file","result":{"ok":True,"path":str(path),"sha256":digest}}]
    assert not PromptWorker._artifact_snapshot(native_bad)[0]["verified"]
    print("PASS unverified native write remains unverified")

real=Path(r"C:\\Norm\\workspace\\web\\shared_browser\\exports\\chatgpt.pdf")
if real.exists():
    observed=PromptWorker._artifact_snapshot([
        {"tool":"plugin_shared_browser__browser_shared_pdf",
         "result":{"ok":True,"plugin":"shared_browser",
                   "result":{"ok":True,"pdf":str(real)}}}])
    assert observed and observed[0]["verified"]
    print("PASS actual previously generated 22864-byte PDF hash validates", observed[0]["size_bytes"])
