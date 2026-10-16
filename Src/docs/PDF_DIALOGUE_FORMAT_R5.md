# Chat PDF format — Shared Browser plugin (r5)

`browser_shared_pdf_export(format="text|visual|dialogue",name="",source="",output="",include_original=False)`
provides a single native Norm tool for selecting the output format.

- `text`: searchable ReportLab export of an existing Norm shared-browser JSONL archive.
  The original archive remains, and this format cannot prove entire chat history was loaded.
- `visual`: asynchronous full-scroll capture of the active Qt shared Chromium browser.
  Use `/pdf-status` for progress. This remains an image/hybrid OCR workflow.
- `dialogue`: **existing ChatGPT Exporter PDF input**; native PyMuPDF vector reflow,
  preserving styled code boxes, bullets, typography, image streams and chat bubbles.
  Output has date/user bookmarks; `include_original=True` appends original pages.

## Norm Prompt direct commands

    /pdf
    /pdf-chat "D:\...\original.pdf" --name "StegoSplit discussion"
    /pdf-dialogue "D:\...\original.pdf" --include-original
    /pdf-name "D:\...\original.pdf"
    /pdf-verify "D:\...\original.pdf" "D:\...\StegoSplit discussion - Rich Dialogue.pdf"
    /pdf-text "archived conversation name"
    /pdf-visual "Conversation topic"
    /pdf-status
    /pdf-cleanup "D:\...\PDFs" --older-hours 24
    /pdf-cleanup "D:\...\PDFs" --older-hours 24 --execute

The other PDF shortcuts remain direct/async where appropriate; not queued through Norm AI.
Slash command registration lives in `tools/norm_pdf_commands.py` and
`tools/norm_gui_prompt.py`. Restart **Norm Prompt only** after deploying to load
new slash dispatch; shared_browser plugin hot-loads on Norm's next refresh.

All added read/write paths use Norm's `shared_browser` capability-scoped file-access
policy. Paths outside it fail closed rather than bypassing authorization. To grant
access to an external folder, configure `[file_access_overrides]` with
`shared_browser.read_add` and `shared_browser.write_add` as appropriate;
do not disable the canonical policy.

The rich converter validates source native text token coverage, exact hashes for
distinct embedded source image streams, presence of user/date bookmarks, and full
original appendix text equality when selected. Reflow changes page location, so
pixel-coordinate equality is not claimed. A `.verification.json` includes both
source/output SHA-256. The converter refuses to publish failed audits or overwrite
originals and outputs. Cleanup matches only stale `.partial-PID.pdf` files, and
`/pdf-cleanup` is dry-run by default.

The original `browser_shared_pdf`, `browser_shared_visual_pdf`, existing settings,
and other plugins are retained. Source-only package; no service restart/install performed.
