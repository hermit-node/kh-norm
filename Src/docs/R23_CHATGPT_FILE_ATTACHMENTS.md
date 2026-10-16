# 0.55.10r23 — ChatGPT non-image attachment export

**Baseline:** exact user-supplied `Norm-0.55.10r22-Strict-Recovery-No-Celery-Browser-Assets-Step-Boundary-Portable-Source.zip`. Source-only candidate; running KHzz Norm is untouched.

## Scope

- The shared ChatGPT browser API extractor now includes file references in message content parts, metadata `files`, and structured nested file payloads in addition to `metadata.attachments` and existing image pointers. File IDs are deduplicated before signed-URL resolution. `inline=false` is requested during the first-party download URL lookup.
- Assistant replies with `sandbox:/mnt/data/...` Markdown links now record a non-image attachment reference even when the conversation API exposes no file ID. **Such references are explicitly unresolved**: no invented signed URL or assumption of shared local filesystem access. Default strict export refuses to claim the attachment was saved; the opt-in partial mode records the failure in `manifest.json`.
- The binary file download cap is 256 MiB per attachment, 512 MiB per export. **Image cap remains 24 MiB** (including content signatures for mislabeled image files). Asset bytes are preserved without resizing/re-encoding. The legacy browser-JavaScript fallback retains its 24 MiB bound to avoid keeping huge base64 blobs in the page; larger files require the direct/Qt-native transfer path.
- Non-image attachment filenames retain a sanitized source basename, with their original bytes in ZIP exports. PDF exports place downloaded ZIP/PDF/DOCX/XLSX/etc. attachments in a sibling `_assets` folder with a provenance manifest; the PDF references that folder. Existing Markdown and JSON external assets remain supported.
- HTML/JSON authentication errors masquerading as files are rejected. Any failed download produces a structured missing-asset entry, not an assertion of complete media coverage.

## What this doesn't do

- It cannot fetch an assistant-generated `sandbox:` link without a real file ID, allowed signed URL, or other supported native download mechanism. A clearly reported missing attachment is preferable to a false successful export.
- It doesn't alter N1/N2 prompts, Redis/PostgreSQL, recovery, suppression, installer, or existing images.
- No live ChatGPT signed-in test was performed; ChatGPT's backend file schema and URL access vary by conversation/client, so real-world export fidelity requires validation.

## Validation

- `python tools/test_nonimage_export_r23.py` — API discovery/deduplication, `inline=false`, unresolved sandbox honesty, 25 MiB ZIP and CSV bytes inside exported ZIP, PDF file sidecar, fake HTML rejection, strict missing-file refusal, and >24 MiB mislabeled image refusal with partial-mode failure record.
- `python tools/test_browser_media_extension_parity_r21.py` — legacy image export parity.
- `python tools/test_shared_browser_assets_r21.py` — image exports, security host restrictions, direct and browser fallback.
- `python tools/test_shared_browser_native_asset_r21.py` — native authenticated transfer path.
- Python source compile check. All of the above passed in the isolated environment. No live Windows Qt browser or real authenticated downloads were run.
