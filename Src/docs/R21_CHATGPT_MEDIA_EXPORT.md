# r21 - ChatGPT media collection and local export (2026-10-16)

**Baseline:** the user-provided `Norm-0.55.10r20-Strict-Recovery-No-Celery-Browser-Compat-Portable-Source.zip`, not any stacked r11 or r12 candidate.
**Status:** source patch with offline regression evidence; **not** a live KHzz / signed-in Qt validation.

## Root causes confirmed from KHzz, not inferred from version names

The installed `C:\Norm` runtime inspected on 2026-10-16 reports **0.55.10r11r9**, not r20. Its most recent shared-browser ZIP (`Market_movement_forecast_20261016_144140_533357.zip`) contained 278 messages, 11 asset references, **0 actual asset files**, and 11 recorded failures, all `HTTP Error 403: Forbidden`. This shows a transport/authentication failure after metadata collection, not merely a blank `url` property. Do not treat r20 or r21 as installed or verified in the live ChatGPT session.

## Why the r20 source can lose attachments

`tools/shared_browser/conversation_js.py` read message metadata and `sediment://file_...` image pointers, but its file resolver accepted only `info.download_url`, overlooking the `url` property returned by the first-party ChatGPT file endpoint. Its local ZIP/PDF exporter accepted a missing URL as an attachment placeholder and still published the output. API message coverage was not evidence of image coverage.

## What's changed

- ChatGPT MainWorld reader deduplicates metadata and sediment pointers and stores generated `image_v2` display URLs when present. For each distinct file ID it requests `GET /backend-api/files/download/<id>?conversation_id=<id>`, accepts common signed-URL field forms, and retries with `message_id` when an actual message ID exists and the conversation query fails.
- Keeps temporary signed asset URLs in the live browser only until export. URL hosts are constrained to OpenAI asset hosts. Does **not** send image content to a third-party service.
- Local export worker fetches actual bytes to its temp export directory (Python HTTPS, comparable to the extension's background fetch, bypassing page CORS). When direct fetching fails with 403 or a network error, the exporter first invokes **QWebEnginePage.download() in the existing authenticated Chromium profile**, accepting only the expected OpenAI asset URL into a bounded disposable directory. It copies the bytes into export staging locally and deletes the transfer folder. Qt native downloads bypass page CORS restrictions (matching the useful part of the extension background fetch). The original page-JavaScript transfer remains a last fallback for first-party URLs.
- ZIP contains actual `assets/` files, not just JSON metadata; local ReportLab PDF embeds supported image types. MD/JSON have local sibling asset folders. File signatures prevent saving login/error HTML as images.
- Separately records selected branch history completeness, expected unique media, actual saved files/images and failure reasons. Missing assets **block export by default**; the user can explicitly opt into partial files in the Export panel.
- Additional offline checks cover API `url`, `download_url`, nested URL, `message_id` context retry, generated image, metadata/sediment dedup, 531 synthetic turns with 38 JPEGs, ZIP actual binaries, PDF embedded images, and missing attachment refusal.
- Existing browser profile and cookies, no-Celery operation, N1/N2/recovery, installer, on-demand browser startup, screenshots/OCR fallback, WeasyPrint, and user plugins are preserved. This is not a cleanup/redesign release.

## Actual live acceptance - required, not claimed

On a KHzz installation, use **Norm Shared Browser's** Export (not the separate Chrome extension), open the relevant conversation and load its history. Confirm the API snapshot and the media-resolution counts. For `Target Update 756 Remains`, earlier inspection found 531 user/assistant messages and 38 unique JPEGs (76 pointers before dedup); those are reference figures, not a guarantee of the current conversation state. Export ZIP with attachments enabled and partial export unchecked. Check `assets/` contains actual images and `manifest.json` reports all expected images saved; inspect the PDF pages. If any missing, the error should show the first failing ID/reason. Distinguish API endpoint failure, file URL resolution failure, direct HTTPS failure, and Qt/CORS failure. If the browser is logged out, authenticate interactively first. A **separate isolated PySide6 6.12 / QtWebEngine native-download smoke test on KHzz** completed an HTTP download and verified its binary bytes; it did not access ChatGPT or install the patch. The test raised a temp-profile cleanup error after completing the download; its exact isolated temporary folder was subsequently cleaned up. No live *authenticated ChatGPT* test of this release has been performed.
