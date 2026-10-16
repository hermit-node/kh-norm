# norm_browser

Adds approved-origin JavaScript execution and reusable document-creation/ready scripts to the existing shared Qt browser. Native UI approval is required; remote browser RPC cannot grant permissions. Do not replace the existing persistent profile, caches, Playwright or MSS.

## Bundled extension: convo_export

Uses ChatGPT's authenticated conversation API when available and a visibly marked partial DOM fallback. The selectable exporter includes user/assistant filtering, formatting, timestamps, PDF, Markdown, JSON, ZIP with local assets and missing-asset records. No third-party PDF or upload service is used. The browser API is undocumented and may change.

`conversation_preview()` retrieves selection indices and coverage; `conversation_export(selected_indices=[...], fmt="zip")` writes local files. The browser's left-side Export UI invokes the same operations.


### r11r9 shared Qt updates
Browser RPC starts the original single-instance Qt helper on demand for explicit operations, not for read-only health/status. Existing API-first conversation extraction and local export are unchanged. Live Qt ChatGPT login and API behavior must be validated on KHzz.


### r21 local ChatGPT attachment export
Resolve each unique ChatGPT file ID through its authenticated first-party metadata endpoint. Fetch the signed asset bytes locally using the Python exporter (or bounded Qt fallback), embed ZIP/PDF images, and report separate history vs asset coverage. A partial export requires explicit opt-in. See `docs/R21_CHATGPT_MEDIA_EXPORT.md`.
