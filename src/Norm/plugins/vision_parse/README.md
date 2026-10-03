# vision_parse

First-party PDF reading plugin for Norm. It renders PDF pages with PyMuPDF and asks Norm's configured local Ollama vision model to reconcile the visible page against the PDF text layer.

The rendered page is authoritative. The text layer is only a noisy hint and may contain mojibake, bad column ordering, missing characters, or OCR-like corruption. The plugin explicitly forbids inventing typo/regex variants to compensate for extraction errors.

`vision_parse(path, start_page=1, end_page=0, mode="hybrid", prompt="")` handles at most four pages per call and returns `next_page` for resumable traversal. `mode="text"` returns the native PDF text layer without vision; `hybrid` is the normal mode.

This folder is package-managed and hot-hydrated by Norm's plugin manager.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
