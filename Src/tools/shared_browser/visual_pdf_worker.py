"""Stitch viewport PNGs by measured scroll offsets and paginate at turn boundaries."""
from __future__ import annotations
import datetime as dt
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from reportlab.lib.pagesizes import letter
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
    # Preserve both compositor samples for each scroll position. Select the
    # globally most overlap-consistent sequence without altering the originals.
    with Image.open(frames[0]['path']) as first:
        scale_y=first.height / max(1,(job.get('clip') or (0,0,0,first.height))[3])
    positions=[int(round((f['y']-frames[0]['y'])*scale_y)) for f in frames]
    if positions[0]!=0 or any(b<=a for a,b in zip(positions,positions[1:])):
        raise ValueError('Non-monotonic scroll offsets')
    with Image.open(frames[0]['path']) as first:
        first_w,first_h=first.size
    if (positions[-1]+first_h)>340000 or first_w*(positions[-1]+first_h)>420000000:
        raise ValueError('Stitched image exceeds configured memory safety limit')
    alternatives=[[str(frame['path'])]+(
        [str(frame['alt_path'])] if frame.get('alt_path') else [])
        for frame in frames]
    previous=[]
    backrefs=[]
    cost=[0.0]*len(alternatives[0])
    shapes=set()
    for i,paths in enumerate(alternatives):
        candidates=[]
        for path in paths:
            with Image.open(path) as image:
                candidate=image.convert('RGB')
                candidate.load()
                candidates.append(candidate)
                shapes.add(candidate.size)
        if len(shapes)>1:
            raise ValueError('Browser viewport changed size mid-capture')
        if i==0:
            backrefs.append([-1]*len(candidates))
        else:
            dy=positions[i]-positions[i-1]
            updated=[float('inf')]*len(candidates)
            parent=[-1]*len(candidates)
            for j,im in enumerate(candidates):
                for k,prior in enumerate(previous):
                    if cost[k]==float('inf'):
                        continue
                    mean,p90=_overlap_error(prior,im,dy)
                    if mean>18 or p90>62:
                        continue
                    score=cost[k]+mean+.15*p90+(.03 if j else 0)
                    if score<updated[j]:
                        updated[j],parent[j]=score,k
            if all(x==float('inf') for x in updated):
                raise ValueError(
                    f'Frame {i+1}: both compositor samples disagree with all prior samples. '
                    'Text may have reflowed; refusing unverified PDF.')
            cost=updated
            backrefs.append(parent)
        previous=candidates
    best=min(range(len(cost)),key=lambda i:cost[i])
    chosen=[0]*len(frames)
    for i in range(len(frames)-1,-1,-1):
        chosen[i]=best
        if i>0:
            best=backrefs[i][best]
            if best<0:raise RuntimeError('Compositor path reconstruction failed')
    repaired_frames=sum(bool(choice) for choice in chosen)
    w,h=next(iter(shapes))
    full_height=positions[-1]+h
    if full_height > 340000 or w*full_height>420000000:
        raise ValueError('Stitched image exceeds configured memory safety limit')
    # Stream the chosen frames into the stitched image. Keep only one
    # viewport in memory at a time (plus the final stitched result).
    stitched=Image.new('RGB',(w,full_height),'white')
    for i,choice in enumerate(chosen):
        with Image.open(alternatives[i][choice]) as source:
            frame=source.convert('RGB')
            if i==0:
                stitched.paste(frame,(0,0))
            else:
                dy=positions[i]-positions[i-1]
                stitched.paste(frame.crop((0,h-dy,w,h)),
                               (0,positions[i]+h-dy))
    del previous
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
    diagnostics=[
        f"Conversation URL: {job['url']}",
        f"Suggested filename: {job['name']}",
        f"Viewports: {len(frames)}; repaired samples: {repaired_frames}; rendered height: {content.height}; cropped columns: {lo}:{hi}",
        "Hybrid PDF: visible native PDF text from OCR; raster only for images, diagrams, code and math.",
        "Capture reached current scrollbar bottom; entire historical conversation NOT verified.",
        ""
    ]
    for idx,(begin,end,why) in enumerate(pages,1):
        diagnostics.append(f'Page {idx:03d}: {begin:7d}..{end:7d} | {why}')
    from hybrid_ocr_pdf import write_hybrid_pdf
    clip=job.get('clip')
    report=write_hybrid_pdf(content,pages,pending_pdf,frames=frames,
                            positions=positions,clip=clip,crop_left=lo,
                            title=job.get('title') or job['name'],
                            tessdata=Path(__file__).parent/'tessdata',
                            cancel=lambda:bool(job.get('cancel')))
    ocr_words=report['ocr_words']
    diagnostics.append(
      f'Visible OCR words: {ocr_words}; preserved raster image regions: {report["image_regions"]}; '
      f'full-page raster images in PDF: {report["raster_full_pages"]}')
    diagnostics.append(f'Delayed compositor samples chosen: {repaired_frames}')
    pending_txt.write_text('\n'.join(diagnostics)+'\n',encoding='utf-8')
    if job.get('cancel'):
        raise RuntimeError('Capture cancelled; no completed export published')
    for temp,dest in ((pending_png,output_png),(pending_pdf,output_pdf),(pending_txt,output_txt)):
        if dest.exists():raise FileExistsError(str(dest))
        temp.rename(dest)
    shutil.rmtree(folder,ignore_errors=True)
    return {'pdf':str(output_pdf),'png':str(output_png),'page_breaks':str(output_txt),
            'pages':len(pages),'frames':len(frames),'ocr_words':ocr_words,'repaired_frames':repaired_frames,'image_regions':report['image_regions'],
            'forced_breaks':sum('REVIEW' in item[-1] for item in pages),
            'suggested_name':job['name'],'status':'partial_unverified',
            'entire_conversation_verified':False,
            'warning':'Current DOM bottom reached; virtualized/unloaded history cannot be independently verified.'}
