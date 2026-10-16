"""Hybrid visual PDF: native selectable OCR text and unmodified pixel cutouts
for complex DOM graphics. The stitched full screenshot is NOT embedded.
"""
from __future__ import annotations
from pathlib import Path
from io import BytesIO

import pymupdf
from PIL import Image


def _screen_visual_regions(frames, positions, clip, frame_size, crop_left, content_size):
    """Translate viewport DOM rectangles to stitched-image pixel coordinates."""
    if not clip:
        # Synthetic fixtures may supply image boxes in stitched-image coordinates.
        return list(frames[0].get("visual_regions", []))
    vx, vy, vw, vh=clip
    pxw, pxh=frame_size
    sx=pxw/max(1,vw)
    sy=pxh/max(1,vh)
    width,height=content_size
    out=[]
    for f,pos in zip(frames,positions):
        for region in f.get("visual_regions",[]):
            if not isinstance(region,dict):
                continue
            try:
                x=int(round((float(region["x"])-vx)*sx))-crop_left
                y=int(round((float(region["y"])-vy)*sy))+pos
                w=int(round(float(region["w"])*sx))
                h=int(round(float(region["h"])*sy))
            except (ValueError,TypeError,KeyError):
                continue
            x0=max(0,x);y0=max(0,y)
            x1=min(width,x+w);y1=min(height,y+h)
            if x1-x0<22 or y1-y0<22:continue
            rect=(x0,y0,x1,y1)
            if any(
                max(0,min(x1,rx1)-max(x0,rx0))
                *max(0,min(y1,ry1)-max(y0,ry0))
                >.70*(x1-x0)*(y1-y0)
                for rx0,ry0,rx1,ry1 in out
            ):continue
            out.append(rect)
    return out


def _intersects(rect,regions,margin=2):
    x0,y0,x1,y1=rect
    for a,b,c,d in regions:
        cx=max(0,min(x1,c+margin)-max(x0,a-margin))
        cy=max(0,min(y1,d+margin)-max(y0,b-margin))
        if cx*cy>0.20*max(1,(x1-x0)*(y1-y0)):
            return True
    return False


def write_hybrid_pdf(image, page_ranges, destination, *, frames=(),
                     positions=(), clip=None, crop_left=0, cancel=None,
                     title="Conversation", tessdata=None):
    """OCR visible words ONCE; preserve complex original raster regions ONCE.

    Raises on missing OCR or wholly blank recognition; no unverified output
    is published by the caller. Does not insert any full-page screenshot.
    """
    tessdata=Path(tessdata or Path(__file__).parent/"tessdata")
    if not (tessdata/"eng.traineddata").is_file():
        raise RuntimeError("Hybrid text reconstruction requires English OCR data")
    width,height=image.size
    if not page_ranges:raise ValueError("No printable conversation page ranges")
    regions=_screen_visual_regions(frames,positions,clip,(frames and
        (Image.open(frames[0]["path"]).size) or (width,height)),crop_left,(width,height))
    # Windows-provided Arial: a normal embedded Unicode font rather than
    # painting text back into the screenshot.
    # PDF Base-14 Helvetica is built in: no megabyte-scale Arial attachment
    # on each export. Unicode-only lines receive an embedded fallback later.
    font=pymupdf.Font(fontname="helv")
    fontfile=Path("C:/Windows/Fonts/arial.ttf")
    unicode_font=None
    page_w,page_h=612.0,792.0
    margin=30.0
    scale=(page_w-2*margin)/width
    doc=pymupdf.open()
    image_regions=0
    words=0
    total_ocr=0
    try:
        for first,last,_ in page_ranges:
            if cancel and cancel():raise RuntimeError("Hybrid PDF cancelled")
            crop=image.crop((0,first,width,last)).convert("RGB")
            page=doc.new_page(width=page_w,height=page_h)
            top=page_h-margin-(last-first)*scale
            if top<margin-2:raise ValueError("Page crop exceeds printable area")
            local_regions=[]
            for x0,y0,x1,y1 in regions:
                a=max(y0,first);b=min(y1,last)
                if a>=b:continue
                area=(x0,a-first,x1,b-first)
                if area[2]-area[0]<15 or area[3]-area[1]<15:continue
                # Not a full-page screenshot: only picture, math, code or
                # table content marked by DOM is retained as untouched pixels.
                tile=crop.crop(area)
                lossless=BytesIO()
                tile.save(lossless,format="PNG",optimize=True)
                png_data=lossless.getvalue()
                # Preserve original captured pixel dimensions. Photos can be
                # visually losslessly JPEG-compressed; sharp code stays PNG.
                photo=BytesIO()
                try:
                    tile.save(photo,format="JPEG",quality=86,subsampling=0)
                    jpeg_data=photo.getvalue()
                except OSError:
                    jpeg_data=b""
                image_bytes=(jpeg_data if jpeg_data and len(jpeg_data)<len(png_data)*.75
                             else png_data)
                dest=pymupdf.Rect(margin+area[0]*scale,top+area[1]*scale,
                                  margin+area[2]*scale,top+area[3]*scale)
                page.insert_image(dest,stream=image_bytes,keep_proportion=False)
                local_regions.append(area)
                image_regions+=1
            pix=pymupdf.Pixmap(pymupdf.csRGB,crop.width,crop.height,
                               crop.tobytes(),False)
            with pymupdf.open(stream=pix.pdfocr_tobytes(
                        language="eng",tessdata=str(tessdata)),filetype="pdf") as recognized:
                source=recognized[0]
                xx=crop.width/max(source.rect.width,1)
                yy=crop.height/max(source.rect.height,1)
                by_line={}
                for x0,y0,x1,y1,word,block,line,order in source.get_text("words"):
                    if not word.strip():continue
                    total_ocr+=1
                    rect=(x0*xx,y0*yy,x1*xx,y1*yy)
                    if _intersects(rect,local_regions):continue
                    by_line.setdefault((block,line),[]).append((rect,word,order))
                for linewords in by_line.values():
                    ordered=sorted(linewords,key=lambda entry:entry[2])
                    if not ordered:continue
                    bounds=[part[0] for part in ordered]
                    phrase=" ".join(x[1] for x in ordered)
                    left=min(x[0] for x in bounds);right=max(x[2] for x in bounds)
                    low=min(x[1] for x in bounds);bottom=max(x[3] for x in bounds)
                    # Fit real Unicode font to measured OCR line width, with a
                    # small margin for differences between OCR and Arial metrics.
                    wanted=min(18.,max(5.0,(bottom-low)*scale*.86))
                    unit=font.text_length(phrase,fontsize=1)
                    maximum=(right-left)*scale/max(unit,0.1)*.98
                    fontsize=max(3.,min(wanted,maximum))
                    if fontsize<3.4:continue
                    x=margin+left*scale
                    y=top+bottom*scale-.06*(bottom-low)*scale
                    native_name="helv"
                    if any(ord(char)>255 for char in phrase):
                        if not fontfile.is_file():
                            # Never drop unfamiliar glyphs; preserve the entire
                            # original line as a compact raster crop instead.
                            lx0=max(0,int(left)-2);ly0=max(0,int(low)-2)
                            lx1=min(crop.width,int(right)+3);ly1=min(crop.height,int(bottom)+3)
                            tile=crop.crop((lx0,ly0,lx1,ly1))
                            buffer=BytesIO();tile.save(buffer,format="PNG",optimize=True)
                            page.insert_image(
                                pymupdf.Rect(margin+lx0*scale,top+ly0*scale,
                                             margin+lx1*scale,top+ly1*scale),
                                stream=buffer.getvalue(),keep_proportion=False)
                            image_regions+=1
                            continue
                        if unicode_font is None:
                            unicode_font=pymupdf.Font(fontfile=str(fontfile))
                        page.insert_font(fontname="normunicode",fontfile=str(fontfile))
                        native_name="normunicode"
                        unit=unicode_font.text_length(phrase,fontsize=1)
                        fontsize=min(fontsize,(right-left)*scale/max(unit,0.1)*.98)
                    page.insert_text((x,y),phrase,fontname=native_name,
                                     fontsize=fontsize,color=(.07,.07,.07))
                    words+=len(ordered)
            del pix
        if words<2:
            raise ValueError(
                "Hybrid OCR found too little real text; source snapshots preserved but no misleading PDF published")
        doc.set_metadata({"title":title,"subject":
            "Hybrid screenshot-based conversation PDF: OCR text and original visual-region crops; completeness not audited"})
        if unicode_font is not None:
            doc.subset_fonts()
        doc.save(str(destination),garbage=4,deflate=True)
    finally:
        doc.close()
    return {"ocr_words":words,"ocr_detected_words":total_ocr,
            "native_text_lines":None,"image_regions":image_regions,
            "raster_full_pages":0}
