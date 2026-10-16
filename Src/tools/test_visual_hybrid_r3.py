"""r3: hybrid PDF must contain native visible searchable text + only image crops,
with smaller size than a full-page screenshot PDF (no double-render).
"""
import sys,tempfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
import numpy as np
import pymupdf
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/shared_browser'))
from hybrid_ocr_pdf import write_hybrid_pdf
from visual_capture import SCROLL_JS,REGIONS_JS,VisualCapture
assert 'loaders:' in SCROLL_JS and 'at_top:' in SCROLL_JS and 'at_bottom:' in SCROLL_JS
assert 'img,svg,canvas,video,pre,table' in REGIONS_JS
browser_ui=(ROOT/'tools/shared_browser/app.py').read_text('utf8')
assert "result['state']=='seeking_top'" in browser_ui
assert 'Finding the oldest conversation turn' in browser_ui
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',24)
with tempfile.TemporaryDirectory(prefix='norm-r3-hybrid-') as td:
    base=Path(td)
    img=Image.new('RGB',(900,1000),'white')
    draw=ImageDraw.Draw(img)
    for i in range(10):
        draw.text((95,40+i*35),f'User conversation message {i} discusses useful information and context.',
                  fill=(18,18,18),font=font)
    rng=np.random.default_rng(4212)
    pixels=rng.integers(0,256,size=(240,420,3),dtype=np.uint8)
    art=Image.fromarray(pixels,'RGB')
    img.paste(art,(185,465))
    draw.text((100,760),'This is additional searchable native text after the image.',
              fill=(20,20,20),font=font)
    path=base/'source.png';img.save(path)
    frames=[{'path':str(path),'y':0,'visual_regions':[
        {'x':185,'y':465,'w':420,'h':240}
    ]}]
    jobpages=[(0,1000,'synthetic')]
    hybrid=base/'hybrid.pdf'
    report=write_hybrid_pdf(img,jobpages,hybrid,frames=frames,positions=[0],
                            clip=(0,0,900,1000),crop_left=0,title='Test',
                            tessdata=ROOT/'tools/shared_browser/tessdata')
    old=base/'screenshot.pdf'
    c=canvas.Canvas(str(old),pagesize=(612,792),pageCompression=1)
    c.drawImage(ImageReader(img),30,792-30-(1000*552/900),552,1000*552/900)
    c.showPage();c.save()
    with pymupdf.open(hybrid) as doc:
        assert len(doc)==1
        content=doc[0].get_text()
        assert 'conversation' in content.lower() and 'searchable' in content.lower(),repr(content)
        images=doc[0].get_images(full=True)
        assert len(images)==1,[(im[2],im[3]) for im in images]
        assert images[0][2]==420 and images[0][3]==240
        chars=sum(len(t) for t in doc[0].get_text('words'))
    assert report['ocr_words']>25 and report['image_regions']==1 and report['raster_full_pages']==0,report
    assert hybrid.stat().st_size < old.stat().st_size,(hybrid.stat().st_size,old.stat().st_size)
    print('PASS hybrid SEARCHABLE visible text, exactly 1 cropped image, zero full-page screenshot layers')
    print('PDF_BYTES hybrid=',hybrid.stat().st_size,'full_raster=',old.stat().st_size,
          'SAVINGS=',round(100*(1-hybrid.stat().st_size/old.stat().st_size),1),'%',
          'OCR_WORDS=',report['ocr_words'])
