"""Regression tests for page-level grayscale triage in the shared PDF reader."""
from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image, ImageDraw

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "vision_parse" / "src" / "main.py"
spec = importlib.util.spec_from_file_location("vision_parse_pdf_coverage_test", PLUGIN)
assert spec and spec.loader
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def _fake_vision_page(**_kwargs):
    return {
        "reading": "TRANSCRIPTION\\nRecovered by model\\nSEMANTIC NOTES\\nTest",
        "render_strategy": "full", "render_scale": 1.5,
        "vision_calls": 1, "vision_retries": 0,
        "vision_done_reasons": ["stop"], "vision_eval_count": 10,
        "length_fallback": False,
    }


class PdfInkCoverageTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        reader._PAGE_CACHE.clear()
        self.root = Path(self.td.name)

    def _make(self, name: str, kind: str, *, rotated: bool = False) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        if kind in {"native", "graphic", "image_overlay", "hidden"}:
            text = ("This is a normal, searchable PDF text layer with readable wording. " * 3).strip()
            page.insert_text((70, 100), text[:96], fontsize=11, render_mode=3 if kind == "hidden" else 0)
            page.insert_text((70, 118), text[96:], fontsize=11, render_mode=3 if kind == "hidden" else 0)
        if kind == "graphic":
            # A dark diagram outside the text bounding boxes is not explained.
            page.draw_rect(pymupdf.Rect(100, 210, 400, 400), color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
        if kind in {"scan", "image_overlay"}:
            img = Image.new("RGB", (650, 850), "white")
            draw = ImageDraw.Draw(img)
            for y in range(80, 400, 25):
                draw.text((40, y), "SCANNED CONTENT outside the PDF text layer", fill="black")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(page.rect, stream=buf.getvalue(), overlay=(kind != "image_overlay"))
        if rotated:
            page.set_rotation(90)
        path = self.root / name
        doc.save(path)
        doc.close()
        return path

    def _read(self, path, mode="hybrid"):
        with patch.object(reader, "_vision_client", return_value=object()), patch.object(reader, "_read_vision_page", side_effect=_fake_vision_page) as call:
            result = reader.vision_parse(str(path), mode=mode)
        return result, call

    def test_native_pdf_avoids_vision_and_reports_grayscale(self):
        result, call = self._read(self._make("native.pdf", "native"))
        p = result["pages"][0]
        self.assertEqual(result["vision_calls"], 0)
        self.assertEqual(p["hybrid_decision"], "native_text")
        self.assertGreaterEqual(p["grayscale_coverage"]["explained_page_fraction"], .99)
        self.assertGreaterEqual(p["grayscale_coverage"]["explained_ink_fraction"], .985)
        call.assert_not_called()

    def test_graphics_trigger_vision_despite_clean_text(self):
        result, call = self._read(self._make("graphic.pdf", "graphic"))
        self.assertEqual(result["vision_calls"], 1)
        self.assertEqual(result["pages"][0]["hybrid_decision"], "vision")
        self.assertIn(result["pages"][0]["grayscale_coverage"]["reason"],
                      {"unaccounted_ink_pixels", "unaccounted_page_pixels"})
        call.assert_called_once()

    def test_scanned_pdf_delegates_to_raster_ocr_before_vision(self):
        # The prior page-level vision fallback has become an OCR-first path.
        fake = {"reading":"The document was read with OCR", "ocr_status":"ok",
                "ocr_words":7,"ocr_trusted_words":7,"vision_regions":[],
                "render_strategy":"raster-ocr-verified","render_scale":0.0,
                "vision_calls":0,"vision_retries":0,"vision_done_reasons":[],
                "vision_eval_count":0,"length_fallback":False}
        with patch.object(reader,"_read_raster_with_ocr",return_value=fake) as ocr_call, \
             patch.object(reader,"_vision_client",side_effect=AssertionError("no model expected")):
            result=reader.vision_parse(str(self._make("scan.pdf", "scan")),mode="hybrid")
        p = result["pages"][0]
        self.assertEqual(p["hybrid_decision"], "ocr_only")
        self.assertEqual(p["grayscale_coverage"]["reason"], "suspect_or_empty_text_layer")
        self.assertEqual(result["vision_calls"],0)
        ocr_call.assert_called_once()

    def test_page_image_triggers_vision_even_with_overlay_text(self):
        result, _ = self._read(self._make("overlay.pdf", "image_overlay"))
        p = result["pages"][0]
        self.assertEqual(p["hybrid_decision"], "vision")
        self.assertEqual(p["grayscale_coverage"]["reason"], "substantive_embedded_image")

    def test_invisible_pdf_text_does_not_satisfy_coverage(self):
        result, _ = self._read(self._make("hidden.pdf", "hidden"))
        p = result["pages"][0]
        self.assertEqual(p["hybrid_decision"], "vision")
        self.assertEqual(p["grayscale_coverage"]["reason"], "native_text_has_no_visible_ink")

    def test_blank_page_skips_vision(self):
        result, call = self._read(self._make("blank.pdf", "blank"))
        p = result["pages"][0]
        self.assertEqual(p["hybrid_decision"], "native_text")
        self.assertEqual(p["grayscale_coverage"]["reason"], "blank_page_confirmed")
        call.assert_not_called()

    def test_rotated_native_page(self):
        result, call = self._read(self._make("rotated.pdf", "native", rotated=True))
        self.assertEqual(result["pages"][0]["hybrid_decision"], "native_text")
        call.assert_not_called()

    def test_forced_vision_and_text_override(self):
        path = self._make("forced.pdf", "native")
        vision, call = self._read(path, "vision")
        self.assertEqual(vision["vision_calls"], 1)
        call.assert_called_once()
        with patch.object(reader, "_page_ink_coverage", side_effect=AssertionError("should not render")):
            text = reader.vision_parse(str(path), mode="text")
        self.assertEqual(text["pages"][0]["hybrid_decision"], "forced_text")
        self.assertEqual(text["vision_calls"], 0)

    def test_cached_native_result_remains_text_only(self):
        path = self._make("cached.pdf", "native")
        first, _ = self._read(path)
        # _read does not clear the page cache; no model call even if repeated.
        second, call = self._read(path)
        self.assertEqual(first["vision_calls"], 0)
        self.assertEqual(second["vision_calls"], 0)
        self.assertTrue(second["pages"][0]["cache_hit"])
        self.assertFalse(second["pages"][0]["rendered_for_vision"])
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
