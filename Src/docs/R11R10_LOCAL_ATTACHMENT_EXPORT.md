# 0.55.10r11r10 — ChatGPT attachment export fix (2026-10-16)

**Base:** Exactly `Norm-0.55.10r11r9-Strict-Recovery-No-Celery-Browser-Compat-Portable-Source.zip`.
This is a **source-only** patch. No files on KHzz, saved conversations, browser cookies or existing plugins have been modified.

## Root cause

Norm's page-side attachment resolver only checked `download_url` but the ChatGPT
`/backend-api/files/download/<file_id>?conversation_id=...` endpoint can supply
the temporary signed link as `url`. That left each `file_...` reference without
an actual URL. Python's ZIP/PDF exporter only downloads assets with URLs, so
text was preserved while media attachments were missing.

## Narrow fixes

- `tools/shared_browser/conversation_js.py`: recognize `url`, `download_url`,
  `downloadUrl` and `public_url`; deduplicate `sediment://file_...` pointers
  and metadata `file_...` records by normalized ID; resolve each unique asset
  via the active ChatGPT session once; track resolution failures separately
  from conversation-history coverage.
- `plugins/norm_browser/src/extensions/convo_export/exporter.py`: download and
  embed referenced images/files locally in ZIP/PDF/MD/JSON assets; sniff file
  signatures to correct image extensions. Deduplicate file identities again at
  the local packaging boundary and strip temporary signed URLs from JSON.
- If the signed asset link cannot be fetched by Python, the logged-in Qt browser
  can fetch that link and hand its bytes to the local Python export worker in
  bounded 24 KiB chunks. No PDF/asset vendor server is involved.
- `tools/shared_browser/browser_ui.py`: explicit attachment counts and
  **Allow export when attachments cannot be included** checkbox, default OFF.
  A missing attachment stops the publication rather than silently generating
  a seemingly complete ZIP or PDF. The user may explicitly accept a partial
  export; a manifest documents every missing attachment and reason.
- Message branch completeness and asset completeness are separate. File
  attachment records with no file ID and no URL remain flagged as unresolved;
  this patch does not invent asset content.
- Existing local ReportLab PDF generation, no-Celery runtime, N1/N2 coordination,
  installer and persistent browser profile are unchanged.

## Tests performed on source candidate

- `python tools/test_browser_cooperation_r11.py` — existing API/ZIP/MD/JSON/PDF regression PASS.
  Fixture updated to provide `URL` to the simulated browser global scope.
- `python tools/test_shared_browser_assets_r11r10.py` — 531 synthetic messages,
  38 distinct image IDs (dedup from 76 pointers), metadata responses containing
  **`url`**, 38 JPEG files in ZIP, 38 embedded image placements in local PDF;
  unavailable direct URL recovered by the browser-transfer fallback; deliberate
  failure blocks a full export unless manually opted into partial fidelity.
- `python tools/test_shared_browser_chatgpt_r11r9.py` — shared browser source
  and plugin identity regression, including retained no-Celery behavior.
- Plugin source SHA-256 identities and both source-file hash ledgers are rebuilt.
- Live signed-in ChatGPT/QWebEngine, asset host CORS responses, and current API
  endpoint behavior **have not been verified on KHzz**. If the server changes
  API response fields or returns a protected asset URL inaccessible from both
  transport methods, the exporter reports a failure instead of falsely claiming
  full fidelity.

## KHzz acceptance procedure

1. Install from this source package through the existing Norm installer,
   preserving `workspace/web/shared_browser/profile` and user plugins.
2. Open the same ChatGPT chat in Norm Shared Browser, select Export and Load
   conversation. Confirm all available messages, image/media counts.
3. Leave **Include images and attachments** checked and leave **Allow export
   when attachments cannot be included** unchecked.
4. Export ZIP. Confirm the result says `Attachments: 38 / 38 | Missing: 0`
   for the previously inspected `Target Update 756 Remains` conversation
   (which has 38 distinct JPEG images, though it originally recorded 76 image
   and metadata references in total).
5. Open the ZIP and confirm actual image binaries under `assets/`, not just
   `[Image]` placeholders. Repeat with PDF and inspect visible images.
6. If it fails, the exact first missing ID/reason is now surfaced in the GUI.
   Supply that diagnostic to repair endpoint compatibility without changing
   unrelated components.
