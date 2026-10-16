"""Offline smoke for r13 shared-browser visual-PDF. Does NOT launch or modify browser."""
import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"/"shared_browser"))
sys.path.insert(0,str(ROOT/"core"))
from norm_runtime.plugin_identity import verify_identity
from visual_capture import _chat_url, infer_name
from visual_pdf_worker import build_visual_pdf
from PIL import Image,ImageDraw
from pypdf import PdfReader

verify_identity(ROOT/"plugins"/"shared_browser")
assert _chat_url("https://chatgpt.com/g/g-p-project/c/aabb-1234")
assert _chat_url("https://chatgpt.com/c/aabb-1234")
assert not _chat_url("https://not-chatgpt.com/c/aabb")
assert "norm" in infer_name(["Integrate visual PDF output with Norm shared web browser"],"Chat").lower()

with TemporaryDirectory(prefix="norm-r13-visual-") as path:
    base=Path(path)
    frames=base/"frames"
    frames.mkdir()
    w,h=900,4000
    im=Image.new("RGB",(w,h),"white")
    draw=ImageDraw.Draw(im)
    for idx,y in enumerate(range(35,h-200,320)):
        if idx%2:
            draw.rounded_rectangle((410,y,795,y+110),radius=18,fill=(232,243,254))
            draw.text((450,y+25),f"User question {idx}",fill="black")
        else:
            for k in range(9):
                draw.text((115,y+22*k),f"Assistant answer {idx}, line {k}",fill="black")
    positions=list(range(0,h-920,620))+[h-920]
    records=[]
    for i,p in enumerate(positions):
        f=frames/f"{i:04d}.png"
        im.crop((0,p,w,p+920)).save(f)
        records.append({"path":str(f),"y":p})
    job={"id":"smoketest123","name":"norm-visual-test","title":"Smoke test",
         "url":"https://chatgpt.com/c/test","folder":str(frames),"frames":records}
    out=build_visual_pdf(job,base)
    assert Image.open(out["png"]).height==h
    assert len(PdfReader(out["pdf"]).pages)==out["pages"]
    assert out["pages"]>=2 and out["frames"]==len(records)
    print(f"PASS: {out['frames']} viewports, {out['pages']} PDF pages; plugin identity valid")
