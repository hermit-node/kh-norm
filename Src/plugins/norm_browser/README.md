# norm_browser

Adds approved-origin JavaScript execution and reusable document-creation/ready scripts to the existing shared Qt browser. Native UI approval is required; remote browser RPC cannot grant permissions. Do not replace the existing persistent profile, caches, Playwright or MSS.

## Bundled extension: convo_export

Uses ChatGPT's authenticated conversation API when available and a visibly marked partial DOM fallback. The selectable exporter includes user/assistant filtering, formatting, timestamps, PDF, Markdown, JSON, ZIP with local assets and missing-asset records. No third-party PDF or upload service is used. The browser API is undocumented and may change.

`conversation_preview()` retrieves selection indices and coverage; `conversation_export(selected_indices=[...], fmt="zip")` writes local files. The browser's left-side Export UI invokes the same operations.
