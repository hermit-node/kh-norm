"""Stitch viewport PNGs by measured scroll offsets and paginate at turn boundaries."""
from __future__ import annotations
import datetime as dt
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from speaker_pdf import analyze,paginate

Image.MAX_IMAGE_PIXELS=None

def _bounds(img):
    # Select the occupied conversation columns, leaving 24px breathing space.
    w,h=img.size
    density=np.zeros(w,dtype=np.float64)
    sampled=0
    for y in (0,h//4,h//2,3*h//4,max(0,h-850)):
        view=np.asarray(img.crop((0,y,w,min(h,y+800))).convert('RGB'))
        if not view.size:continue
        density+=(np.min(view,axis=2)<180).mean(axis=0)
        sampled+=1
    density/=max(sampled,1)
    active=np.flatnonzero(density>.0015)
    if len(active)<60:return 0,w
    lo=max(0,int(active[0])-32)
    hi=min(w,int(active[-1])+33)
    if hi-lo < w*.35:return 0,w
    return lo,hi

def _overlap_error(previous,current,dy):
    h=previous.height
    n=h-dy
    if n<60:raise ValueError('Not enough overlap; refusing PDF')
    # Find differences only where page content can appear; static margins
    # do not overwhelm the registration score.
    left=int(previous.width*.15);right=int(previous.width*.85)
    top=np.asarray(previous.crop((left,dy,right,h)).resize((300,min(n,320))).convert('RGB'),dtype=np.int16)
    bot=np.asarray(current.crop((left,0,right,n)).resize((300,min(n,320))).convert('RGB'),dtype=np.int16)
    diff=np.abs(top-bot)
    return float(diff.mean()),float(np.percentile(diff,90))

def build_visual_pdf(job,base):
    folder=Path(job['folder'])
    frames=job['frames']
    if not frames:raise ValueError('Capture had no scroll frames')
    images=[]
    for f in frames:
        im=Image.open(f['path'])
        im.load()
        images.append(im.convert('RGB'))
    w,h=images[0].size
    if any(i.size!=(w,h) for i in images):
        raise ValueError('Browser viewport changed size mid-capture')
    positions=[int(round(f['y']-frames[0]['y'])) for f in frames]
    if positions[0]!=0 or any(b<=a for a,b in zip(positions,positions[1:])):
        raise ValueError('Non-monotonic scroll offsets')
    full_height=positions[-1]+h
    if full_height > 340000 or w*full_height>420000000:
        raise ValueError('Stitched image exceeds configured memory safety limit')
    for index,(prev,curr) in enumerate(zip(images,images[1:]),1):
        displacement=positions[index]-positions[index-1]
        mean,p90=_overlap_error(prev,curr,displacement)
        if mean>18 or p90>62:
            raise ValueError(
                f'Frame {index+1}: overlap disagrees (mean={mean:.1f},p90={p90:.1f}); '
                'page may have reflowed or lazy content was not ready')
    stitched=Image.new('RGB',(w,full_height),'white')
    stitched.paste(images[0],(0,0))
    for i in range(1,len(images)):
        dy=positions[i]-positions[i-1]
        stitched.paste(images[i].crop((0,h-dy,w,h)),(0,positions[i]+h-dy))
    del images
    lo,hi=_bounds(stitched)
    content=stitched.crop((lo,0,hi,full_height))
    now=dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    base=Path(base)/'exports'
    base.mkdir(parents=True,exist_ok=True)
    prefix=f"{job['name']}_{now}_{job['id']}"
    output_pdf=base/(prefix+'.pdf')
    output_png=base/(prefix+'.png')
    output_txt=base/(prefix+'.breaks.txt')
    if any(x.exists() for x in (output_pdf,output_png,output_txt)):
        raise FileExistsError('Visual export filename already exists')
    pending_png=folder/'stitched.png'
    pending_pdf=folder/'pages.pdf'
    pending_txt=folder/'breaks.txt'
    content.save(pending_png,optimize=False)
    ink,bubbles,gaps,turns=analyze(content,0,content.width)
    pw,ph=letter;margin=30
    scale=(pw-2*margin)/content.width
    rows=int((ph-2*margin)/scale)
    pages=paginate(ink,bubbles,gaps,turns,rows)
    doc=canvas.Canvas(str(pending_pdf),pagesize=letter,pageCompression=1)
    doc.setTitle(job['title'] or job['name'])
    doc.setSubject('Visual ChatGPT conversation capture; DOM scroll bottom observed, completeness not audited')
    diagnostics=[
        f"Conversation URL: {job['url']}",
        f"Suggested filename: {job['name']}",
        f"Viewports: {len(frames)}; rendered height: {content.height}; cropped columns: {lo}:{hi}",
        "Capture reached the DOM's current bottom; entire historical conversation NOT verified.",
        ""
    ]
    for idx,(begin,end,why) in enumerate(pages,1):
        if job.get('cancel'):
            raise RuntimeError('Capture cancelled; no completed export published')
        crop=content.crop((0,begin,content.width,end))
        size=(end-begin)*scale
        doc.drawImage(ImageReader(crop),margin,ph-margin-size,
                      width=pw-2*margin,height=size)
        doc.setFont('Helvetica',8)
        doc.setFillGray(.45)
        doc.drawRightString(pw-margin,max(10,margin-12),str(idx))
        doc.showPage()
        diagnostics.append(f'Page {idx:03d}: {begin:7d}..{end:7d} | {why}')
    doc.save()
    if job.get('cancel'):
        raise RuntimeError('Capture cancelled; no completed export published')
    pending_txt.write_text('\n'.join(diagnostics)+'\n',encoding='utf-8')
    if job.get('cancel'):
        raise RuntimeError('Capture cancelled; no completed export published')
    for temp,dest in ((pending_png,output_png),(pending_pdf,output_pdf),(pending_txt,output_txt)):
        if dest.exists():raise FileExistsError(str(dest))
        temp.rename(dest)
    shutil.rmtree(folder,ignore_errors=True)
    return {'pdf':str(output_pdf),'png':str(output_png),'page_breaks':str(output_txt),
            'pages':len(pages),'frames':len(frames),
            'forced_breaks':sum('REVIEW' in item[-1] for item in pages),
            'suggested_name':job['name'],'status':'partial_unverified',
            'entire_conversation_verified':False,
            'warning':'Current DOM bottom reached; virtualized/unloaded history cannot be independently verified.'}
