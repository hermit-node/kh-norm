#!/usr/bin/env python3
"""Turn-aware pagination for a long ChatGPT PNG screenshot. Never replaces inputs."""
from __future__ import annotations
import argparse
import bisect
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.utils import ImageReader


def spans(mask: np.ndarray, min_len: int = 1):
    b = np.r_[False, mask, False]
    starts = np.flatnonzero(np.diff(b.astype('i1')) == 1)
    ends = np.flatnonzero(np.diff(b.astype('i1')) == -1)
    return [(int(a), int(z)) for a, z in zip(starts, ends) if z-a >= min_len]


def merge_near(runs, max_gap=82):
    merged = []
    for a,b in runs:
        if merged and a-merged[-1][1] <= max_gap:
            merged[-1]=(merged[-1][0], b)
        else:
            merged.append((a,b))
    return merged


def analyze(im, x0, x1):
    h = im.height
    ink = np.zeros(h, dtype=np.int16)
    blue = np.zeros(h, dtype=np.int16)
    for y in range(0,h,1400):
        end=min(h,y+1400)
        arr=np.asarray(im.crop((x0,y,x1,end)).convert('RGB'))
        ink[y:end]=(np.min(arr,axis=2)<180).sum(axis=1)
        r,g,b = arr[:,:,0],arr[:,:,1],arr[:,:,2]
        blue_mask=(r>=229)&(r<=235)&(g>=239)&(g<=246)&(b>=250)&(b<=255)
        blue[y:end]=blue_mask[:,max(0,int((x1-x0)*.37)):].sum(axis=1)
    bubbles=merge_near(spans(blue>=35,14))
    gaps=spans(ink<2,12)
    # A turn boundary is the whitespace immediately preceding the next blue message.
    turn_boundaries=[]
    for a,b in bubbles:
        previous=[(s,e) for s,e in gaps if a-125<=s<a and e<=a+2]
        if previous:
            s,e=previous[-1]
            point=(s+e)//2
        else:
            point=max(0,a-8)
        turn_boundaries.append((point,a,b))
    return ink, bubbles, gaps, turn_boundaries


def paginate(ink,bubbles,gaps,turns,max_height):
    h=len(ink); pages=[]; p=0
    while p < h:
        limit=min(h,p+max_height)
        if limit==h:
            pages.append((p,h,'END')); break
        cut=None; mode=None
        t=[(boundary,start,end) for boundary,start,end in turns
           if p+int(max_height*.50)<=boundary<=limit-12]
        if t:
            cut=t[-1][0]; mode='BETWEEN TURNS'
        else:
            candidates=[]
            for a,b in gaps:
                if a < p+int(max_height*.73) or a>limit-12:
                    continue
                c=min(limit-3,(a+b)//2)
                if any(u-3<=c<=v+3 for u,v in bubbles): continue
                candidates.append((c,min(b-a,65)))
            if candidates:
                cut=max(candidates,key=lambda x:x[0]+.40*x[1])[0]
                mode='WITHIN LONG TURN / WHITESPACE'
            else:
                lo=p+int(max_height*.60); hi=limit-8
                smooth=np.convolve(ink[max(0,lo-7):min(h,hi+7)],np.ones(15)/15,'same')
                cut=lo+int(np.argmin(smooth[7:7+max(1,hi-lo)]))
                mode='FORCED / REVIEW'
        for u,v in bubbles:
            if u-3 < cut < v+3:
                if u-8 > p + int(max_height*.35):
                    cut=u-8; mode='BEFORE USER TURN'
                elif v+8 <=limit:
                    cut=v+8; mode='AFTER USER TURN'
                else:
                    mode='FORCED THROUGH USER BUBBLE / REVIEW'
                break
        if cut <= p+30:
            cut=limit; mode='FORCED / REVIEW'
        pages.append((p,cut,mode)); p=cut
    return pages


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('image',type=Path)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--crop',default='260:1045',help='Source x range to print, default 260:1045')
    ap.add_argument('--paper',choices=['letter','a4'],default='letter')
    ap.add_argument('--margin',type=float,default=30)
    args=ap.parse_args()
    source=args.image
    target=args.output or source.with_name(source.stem+'_speaker_paginated.pdf')
    if not source.is_file():ap.error(f'No image: {source}')
    if target.exists():ap.error(f'Will not overwrite existing PDF: {target}')
    report=target.with_suffix('.breaks.txt')
    if report.exists():ap.error(f'Will not overwrite existing report: {report}')
    x0,x1=map(int,args.crop.split(':'))
    Image.MAX_IMAGE_PIXELS=None  # Explicitly trust local user-selected screenshot.
    start=time.time()
    im=Image.open(source)
    if im.mode!='RGB':im=im.convert('RGB')
    if not (0<=x0<x1<=im.width):ap.error('Bad x crop')
    pw,ph=letter if args.paper=='letter' else A4
    width_pt=pw-2*args.margin
    height_pt=ph-2*args.margin
    scale=width_pt/(x1-x0)
    max_height=int(height_pt/scale)
    print('Source:',im.width,'x',im.height, 'Crop:',x0,x1,'Max rows/page:',max_height,flush=True)
    ink,bubbles,gaps,turns=analyze(im,x0,x1)
    pages=paginate(ink,bubbles,gaps,turns,max_height)
    print('Message bubbles:',len(bubbles),'PDF pages:',len(pages),'Analysis seconds:',round(time.time()-start,1),flush=True)
    pdf=canvas.Canvas(str(target),pagesize=(pw,ph),pageCompression=1)
    pdf.setTitle(source.stem + ' - chat conversation, paginated')
    lines=[f'Source {source}',f'Size {im.size}; crop {x0}:{x1}; page px {max_height}',
           f'Blue user-message groups detected: {len(bubbles)}', '']
    for idx,(lo,hi,mode) in enumerate(pages,1):
        page=im.crop((x0,lo,x1,hi))
        drawn=(hi-lo)*scale
        pdf.drawImage(ImageReader(page), args.margin, ph-args.margin-drawn,
                      width=width_pt,height=drawn)
        pdf.setFont('Helvetica',8);pdf.setFillGray(.45)
        pdf.drawRightString(pw-args.margin,max(10,args.margin-12),str(idx))
        pdf.showPage()
        lines.append(f'Page {idx:03}: Y {lo:6} - {hi:6}   {mode}')
        if idx%20==0:print('  pages saved:',idx,flush=True)
    pdf.save()
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('PDF:',target, 'MB:',round(target.stat().st_size/1048576,2),flush=True)
    print('Break report:',report,flush=True)
    print('Forced/review:',sum('REVIEW' in m for _,_,m in pages),flush=True)
    print('Elapsed seconds:',round(time.time()-start,1),flush=True)


if __name__=='__main__':
    main()
