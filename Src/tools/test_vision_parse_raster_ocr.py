"""OCR-first image/scanned-PDF regression tests, independent of a live Ollama service."""
from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image, ImageDraw, ImageFont

MODULE = Path(__file__).resolve().parents[1] / "plugins/vision_parse/src/main.py"
spec = importlib.util.spec_from_file_location("norm_raster_ocr_reader", MODULE)
assert spec and spec.loader
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

FONT = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


class VisionMock:
    def __init__(self):
        self.read_images = []

    def vision_result(self, prompt, paths, **kwargs):
        import PIL.Image
        im = PIL.Image.open(paths[0])
        # Text recognized by OCR has been blanked out of the region fed to vision.
        self.read_images.append((prompt, im.copy()))
        return {"content": "An outlined diagram.", "done_reason": "stop", "eval_count": 12}


class RasterOCRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reader._PAGE_CACHE.clear()
        self.root = Path(self.tmp.name)
        self.font = ImageFont.truetype(str(FONT), 26) if FONT.exists() else ImageFont.load_default()

    def make_image(self, *, diagram=False):
        im = Image.new("RGB", (960, 600), "white")
        d = ImageDraw.Draw(im)
        for k, line in enumerate(("The delivery arrives at half past ten.",
                                 "Everything on the invoice must match.",
                                 "Nobody gets to skip the checks.")):
            d.text((45, 55 + 42*k), line, font=self.font, fill="black")
        if diagram:
            d.rectangle([550, 290, 850, 495], outline="black", width=7)
        return im

    def test_clean_png_ocr_only(self):
        path = self.root / "clean.png"
        self.make_image().save(path)
        with patch.object(reader, '_vision_client', side_effect=AssertionError('model not required')):
            result = reader.vision_parse(str(path))
        p = result['pages'][0]
        self.assertEqual(result['vision_calls'], 0)
        self.assertEqual(p['hybrid_decision'], 'ocr_only')
        self.assertIn('invoice must match', p['reading'])
        self.assertGreater(p['ocr_trusted_words'], 10)

    def test_clean_jpg_text_mode_uses_ocr_not_vision(self):
        path = self.root / "clean.jpg"
        self.make_image().save(path, quality=95)
        with patch.object(reader, '_vision_client', side_effect=AssertionError('model not required')):
            result = reader.vision_parse(str(path), mode='text')
        self.assertEqual(result['pages'][0]['ocr_status'], 'ok')
        self.assertEqual(result['vision_calls'], 0)

    def test_masked_ocr_and_region_vision(self):
        path = self.root / "diagram.png"
        im = self.make_image(diagram=True)
        im.save(path)
        ocr = reader._ocr_image(im)
        masked = reader._masked_raster_regions(im,ocr['words'])
        self.assertTrue(masked['regions'])
        self.assertEqual(masked['rgba'].getpixel((70, 72))[3], 0)
        self.assertNotEqual(masked['rgba'].getpixel((600, 290))[3], 0)
        fake = VisionMock()
        with patch.object(reader, '_vision_client', return_value=fake):
            result = reader.vision_parse(str(path))
        p = result['pages'][0]
        self.assertGreaterEqual(result['vision_calls'], 1)
        self.assertEqual(p['hybrid_decision'], 'ocr_plus_vision')
        self.assertIn('invoice must match', p['reading'])
        self.assertIn('outlined diagram', p['reading'])
        self.assertTrue(fake.read_images)
        self.assertFalse(any('delivery arrives' in q.lower() for q,_ in fake.read_images),
                         'Vision prompt should not require copying OCR body text verbatim')

    def test_raster_scan_pdf_ocr_before_model(self):
        image = self.make_image()
        b = io.BytesIO()
        image.save(b, format='PNG')
        doc = pymupdf.open()
        page = doc.new_page(width=480, height=300)
        page.insert_image(page.rect, stream=b.getvalue())
        path = self.root/'scan.pdf'
        doc.save(path)
        doc.close()
        with patch.object(reader, '_vision_client', side_effect=AssertionError('model not required')):
            result = reader.vision_parse(str(path), mode='hybrid')
        self.assertIn('invoice must match', result['pages'][0]['reading'])
        self.assertEqual(result['pages'][0]['hybrid_decision'], 'ocr_only')
        self.assertEqual(result['vision_calls'], 0)

    def test_hybrid_missing_ocr_falls_back_to_vision(self):
        path=self.root/'missing.png'
        self.make_image().save(path)
        fake=VisionMock()
        with patch.object(reader,'_ocr_image',return_value={'available':False,'reason':'test_missing','words':[],'text':''}),\
             patch.object(reader,'_vision_client',return_value=fake):
            result = reader.vision_parse(str(path))
        self.assertIn('unavailable', result['pages'][0]['ocr_status'])
        self.assertEqual(result['vision_calls'],1)
        with patch.object(reader,'_ocr_image',return_value={'available':False,'reason':'test_missing','words':[],'text':''}):
            with self.assertRaisesRegex(RuntimeError,'OCR-only'):
                reader.vision_parse(str(path), mode='text')

    def test_explicit_suspect_ocr_line_forces_local_recheck(self):
        path = self.root/'suspect.png'
        self.make_image().save(path)
        fake = VisionMock()
        with patch.object(reader, '_vision_client', return_value=fake):
            result = reader.vision_parse(str(path), mode='hybrid',
                                         suspect_excerpt='Everything on the invoice must match')
        p = result['pages'][0]
        self.assertTrue(p['explicit_excerpt_matched'])
        self.assertEqual(p['hybrid_decision'], 'ocr_plus_vision')
        self.assertGreater(result['vision_calls'], 0)
        self.assertIn('The delivery arrives', p['reading'])

    def test_semantic_suspicion_does_not_force_correct_ocr_to_be_deleted(self):
        im=self.make_image()
        o=reader._ocr_image(im)
        m=reader._masked_raster_regions(im,o['words'])
        self.assertEqual(m['remaining_ink_pixels'],0)
        self.assertEqual(len(m['regions']),0)

if __name__ == '__main__':
    unittest.main()
