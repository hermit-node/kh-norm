# vision_parse

First-party PDF reading plugin for Norm. It renders PDF pages with PyMuPDF and asks Norm's configured local Ollama vision model to reconcile the visible page against the PDF text layer.

The rendered page is authoritative. The text layer is only a noisy hint and may contain mojibake, bad column ordering, missing characters, or OCR-like corruption. The plugin explicitly forbids inventing typo/regex variants to compensate for extraction errors.

`vision_parse(path, start_page=1, end_page=0, mode="hybrid", prompt="")` handles at most ten pages per call and returns `next_page` for resumable traversal. Pages remain logically independent, but parsing is adaptive:

- clean full pages below about 5,000 native-text characters render at 1.5x (about 108 DPI);
- moderately dense clean full pages render at 2.2x (about 158 DPI);
- suspect text layers, very dense pages, and split crops render at 2.9x (about 209 DPI);
- dense two-column pages are split into left/right column crops;
- very dense single-column pages are split top/bottom;
- Ollama's `done_reason` and `eval_count` are preserved, with a dynamic `num_predict` ceiling sized to the page/crop;
- a full-page `done_reason=length` automatically falls back to split crops instead of silently returning an incomplete page;
- `token repeat limit reached` retries only the affected page/crop once with an anti-loop instruction and slight temperature nudge;
- a process-local 128-page LRU cache reuses completed reads for the same source page/model/mode/focus without creating a disk cache.

Each page result reports its render strategy, render scale, model-call count, retries, completion reasons, eval count, length-fallback status, and cache status. The top-level result reports total new vision calls and cache hits. `mode="text"` returns the native PDF text layer without vision; `hybrid` is the normal mode.

The 1.5x floor is deliberate: 1.0x is only about 72 DPI, which saves more pixels but makes ordinary body text needlessly fragile for the vision model. Raster area grows quadratically, so 1.5x is still less than half the pixel area of 2.2x while retaining substantially better glyph detail.

This folder is package-managed and hot-hydrated by Norm's plugin manager.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
