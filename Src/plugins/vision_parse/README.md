# vision_parse

## Generic media vision (v0.4.0 / Norm r26)

Alongside the unchanged PDF/OCR reader, the plugin exposes these native tools (not new slash commands):

- vision_media_backend_status and vision_media_face_models_setup — model readiness and explicit approved setup.
- vision_media_detect_faces and vision_media_scan_video_faces — stable face UUIDs, rectangles, landmarks and SFace embeddings.
- vision_media_scene_edges — full-timeline two-pass scene detection with a 30-second minimum and optional old-boundary rechecking.
- vision_media_classify_scene and vision_media_classify_video — descriptive categories through Norm’s shared vision client.
- vision_media_reconcile_people and vision_media_reconcile_people_file — propose merges without modifying library databases.

Media paths obey vision_parse read/write-root authorization. Profiles with different manual aliases never merge automatically, including via chains. Equal alias sets still require strong visual evidence. Names, aliases and acceptance decisions remain owned by Media Catalog. ONNX Runtime GPU and packaged FFmpeg are installed from requirements (CUDA remains optional at execution). YuNet/SFace model weights are downloaded only after explicit approval. No independent Ollama instance or guaranteed shared PyTorch memory allocator.

Details: ../../docs/R26_GENERIC_VISION_MEDIA.md

First-party PDF/image reader for Norm. Native PDFs use grayscale text-coverage triage. Scanned PDFs and PNG/JPEG/WebP/BMP/TIFF images attempt conventional OCR first; only uncovered/uncertain visual regions are sent to the configured Ollama vision model.

The rendered page is authoritative. The text layer is only a noisy hint and may contain mojibake, bad column ordering, missing characters, or OCR-like corruption. The plugin explicitly forbids inventing typo/regex variants to compensate for extraction errors.

`vision_parse(path, start_page=1, end_page=0, mode="hybrid", prompt="", suspect_excerpt="")` handles up to ten PDF pages or multi-frame TIFF frames per call and returns `next_page` for resumable traversal. Single-image files have one frame. All PDF/image consumers, including voice-profile training, can use this reader's mode decisions.

### Scanned PDF and photographed-writing pipeline

- Render or load the page to an RGB raster and run **Tesseract via pytesseract** first. OCR supplies words, confidence scores, line grouping, and image-pixel bounding boxes.
- Word boxes with confidence >=84 and no obvious Unicode/encoding artifacts receive a temporary **transparent alpha mask**. Low-confidence or suspicious words stay visible. The original source file is never modified.
- For the vision-model crop, compose those transparent areas against white. This is intentional: Ollama backends differ in alpha-channel handling, so the vision model sees a consistently blanked-out OCR area. The internal RGBA mask retains exact transparency geometry.
- Detect remaining dark/contrasting regions and selectively crop them. Text already handled by OCR is not sent again; uncaptured writing and graphics go to the vision adapter. When too many disconnected regions remain, one masked image is used instead, so smaller fragments are not lost.
- Merge accepted OCR words and focused vision observations in approximate geometric reading order. Return `vision_regions` with pixel coordinates, candidate readings and status, along with `ocr_status`, word counts, and coverage data for inspection.
- Pass `suspect_excerpt="quoted OCR line"` in hybrid mode when a passage is semantically incoherent despite high OCR confidence. Its whole OCR line is unmasked and rechecked locally, without invalidating adjacent recognized text. Unlocatable excerpts are reported rather than guessed.
- `mode="text"` means native text-only for PDFs and OCR-only for raster images. `mode="vision"` forces an unmasked full-vision read. Hybrid mode falls back to full vision if OCR is unavailable; OCR-only raises a clear error instead of silently using a vision model.

The Python environment needs `pytesseract`, Pillow, OpenCV and NumPy. The **Tesseract OCR executable is a separate optional installation**: make it available on PATH, install it in the normal Windows Program Files/Tesseract-OCR directory, or set `NORM_TESSERACT_PATH` to the executable location. The installer does not currently deploy that executable. If it is absent, hybrid image reads fall back to vision.

Important limits: confidence scores do not prove correct recognition, and a word-sized rectangular mask can conceal underlying photo/diagram content. A forced recheck is appropriate when readability, odd line ordering or hidden content matters. OCR/vision results are evidence, not proof; regional vision reconstructions can still hallucinate or miss small markings.

In **hybrid** mode, the reader renders a low-resolution **96 DPI grayscale** inspection image, inventories embedded raster images, and uses PyMuPDF's native character bounding boxes to mark pixels explained by text extraction. The rule is deliberately stricter than simply counting white background:

- At least **99% of page pixels** must be explained by background plus the extracted text footprint.
- At least **98.5% of visible non-background ink** must fall inside extracted text character geometry.
- Substantive embedded images, unaccounted diagrams/graphics, missing or suspect text layers, or invisible/misplaced extraction trigger vision.
- Confirmed blank pages skip vision; clean text PDFs return their native text unchanged.
- The response includes each page's `grayscale_coverage` metrics (`white_fraction`, `black_fraction`, `gray_fraction`, `explained_page_fraction`, `explained_ink_fraction`, residual ink, and image area), `hybrid_decision`, and normal model-call accounting.
- Explicit `mode="text"` still bypasses visual inspection and vision; explicit `mode="vision"` forces the vision path. Ollama is not initialized when hybrid pages pass triage.

This is a conservative raster-to-text correspondence heuristic, **not OCR or proof of accurate character encoding**. Unicode-mapped but wrong text, tiny visually significant symbols, gradients, and some unusual PDF layering may still require a forced vision run. Test suspicious cases with `mode="vision"`.

When vision is needed, parsing remains adaptive:

- clean full pages below about 5,000 native-text characters render at 1.5x (about 108 DPI);
- moderately dense clean full pages render at 2.2x (about 158 DPI);
- suspect text layers, very dense pages, and split crops render at 2.9x (about 209 DPI);
- dense two-column pages are split into left/right column crops;
- very dense single-column pages are split top/bottom;
- Ollama's `done_reason` and `eval_count` are preserved, with a dynamic `num_predict` ceiling sized to the page/crop;
- a full-page `done_reason=length` automatically falls back to split crops instead of silently returning an incomplete page;
- `token repeat limit reached` retries only the affected page/crop once with an anti-loop instruction and slight temperature nudge;
- a process-local 128-page LRU cache reuses completed reads for the same source page/model/mode/focus without creating a disk cache.

Each page result reports its render strategy, render scale, model-call count, retries, completion reasons, eval count, length-fallback status, and cache status. The top-level result reports total new vision calls and cache hits. `mode="text"` returns the native PDF text layer without rendering; `hybrid` is the coverage-gated default mode.

The 1.5x floor is deliberate: 1.0x is only about 72 DPI, which saves more pixels but makes ordinary body text needlessly fragile for the vision model. Raster area grows quadratically, so 1.5x is still less than half the pixel area of 2.2x while retaining substantially better glyph detail.

This folder is package-managed and hot-hydrated by Norm's plugin manager.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
