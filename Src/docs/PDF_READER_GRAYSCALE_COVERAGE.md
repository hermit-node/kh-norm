# PDF reader grayscale-coverage gate (0.54.3 source patch)

Implemented in `plugins/vision_parse/src/main.py`; consumers should call the same reader rather than maintain per-consumer heuristics. `mode=hybrid` performs one inexpensive grayscale raster inspection per page; only low-confidence pages run the vision model. Explicit `text` and `vision` remain forcing modes.

Thresholds: explained page pixels >= 99%; explained non-background ink pixels >= 98.5%; substantive embedded image area < 1%; native text glyph quality must be acceptable. The text footprint derives from **character-level geometry**, not broad paragraph boxes. Background level is inferred from the page's dominant bright grayscale level.

Images, graphics, scans, invisible text, visually unexplained marks, uncertain extraction, and poor geometry fall back to vision. Confirmed blank pages bypass it. Per-page `grayscale_coverage` exposes the pixel statistics and gate reason. Confidence is geometric, not semantic: false or broken Unicode mappings with plausible text geometry cannot be detected by this gate alone.

Regression tests: `python -m unittest discover -s tools -p test_pdf_ink_coverage.py -v`. Manual sample: clean text-only Cockney PDFs should return `hybrid_decision=native_text` with zero vision calls.


## OCR-first raster extension (0.54.3 overlay)

Scanned PDF pages and standalone raster writing samples use the same `vision_parse` entrypoint. Tesseract OCR detects confidently readable words before Ollama is invoked. The shared reader creates an alpha mask over these words, then sends only unaccounted image regions to the vision adapter after white-compositing the transparency for backend consistency. The model cannot be asked to reconstruct already-masked text; OCR output remains separately available for merging. A `suspect_excerpt` argument can re-expose and recheck a specific questionable OCR line. Pure native PDFs continue through geometric ink coverage. Tesseract itself must be installed separately on the Norm host; `pytesseract` is pinned in managed Python requirements.
