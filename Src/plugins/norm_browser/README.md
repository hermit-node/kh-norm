# norm_browser

Adds approved-origin JavaScript execution and reusable document-creation/ready scripts to the existing shared Qt browser. Native UI approval is required; remote browser RPC cannot grant permissions. Do not replace the existing persistent profile, caches, Playwright or MSS.

## Bundled extension: convo_export

Uses ChatGPT's authenticated conversation API when available and a visibly marked partial DOM fallback. The selectable exporter includes user/assistant filtering, formatting, timestamps, PDF, Markdown, JSON, ZIP with local assets and missing-asset records. No third-party PDF or upload service is used. The browser API is undocumented and may change.

`conversation_preview()` retrieves selection indices and coverage; `conversation_export(selected_indices=[...], fmt="zip")` writes local files. The browser's left-side Export UI invokes the same operations.


### r11r9 shared Qt updates
Browser RPC starts the original single-instance Qt helper on demand for explicit operations, not for read-only health/status. Existing API-first conversation extraction and local export are unchanged. Live Qt ChatGPT login and API behavior must be validated on KHzz.


### r11r10 attached media
Conversation export resolves each unique ChatGPT `file_...` ID via the signed first-party asset metadata API (`url` or `download_url`). ZIP includes actual files under `assets/`; local PDF includes recognizable images. If Python direct fetch is denied, the authenticated Qt tab can transfer the bytes to the same local export worker in bounded chunks. No third-party PDF service. The UI fails on missing files by default; opt into partial export deliberately. Details: `docs/R11R10_LOCAL_ATTACHMENT_EXPORT.md`.
