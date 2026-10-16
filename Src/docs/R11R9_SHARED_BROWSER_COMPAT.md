# 0.55.10r11r9 — Shared Browser ChatGPT compatibility (2026-10-16)

**Base:** exact user-uploaded `Norm-0.55.10r11r8-Strict-Recovery-No-Celery-Portable-Source.zip`.
This remains a portable **source-only** update. No live installation or existing browser profile is modified.

## Targeted changes

- `Run-Norm.bat`: stop prelaunching hidden Qt Chromium at every Norm startup. Explicit browser commands lazily call the existing single-instance Windows-session starter. Health/status checks do not start the browser. `Show Shared Browser.cmd` and the existing persistent cookie/profile paths are unchanged.
- `tools/shared_browser/app.py`: Qt `newWindowRequested` opens OAuth/sign-in popups in separate visible windows **sharing the original NormShared QWebEngineProfile and cookies**, without raising/replacing the main tab.
- `browser_shared_type`: use Chromium's native `insertText` for ChatGPT's contenteditable/ProseMirror composer, and native input-value setters/events for normal React text fields. Never press Send implicitly. Report `typed:false` when editing is unsupported.
- `browser_shared_scroll`: prefer the scrollable ancestor of actual conversation turns instead of the largest scrollable region (often the sidebar). Preserve generic website fallback.
- `browser_shared_chatgpt_diagnostics`: read-only classification of ChatGPT UI as `conversation_visible`, `composer_visible`, `login`, `challenge`, or `page_without_chat` with DOM counts, no tokens or private conversation text.
- The legacy screenshot/stitched visual-PDF engine is **only imported/initialized when explicitly requested**; its 900 ms GUI progress timer is inactive otherwise. It is retained as an optional fallback; no existing archive/export/PDF paths or dependencies were removed.
- Distinguish HTTP errors from unavailable browser processes; do not relaunch Chromium for an application-level 400/403 response.
- Both modified schema-2 plugin identities, package hash ledgers, and source version are regenerated.

## Verification / limitations

- Passed offline JavaScript syntax and headless Chromium tests for contenteditable input, text input, sidebar-versus-conversation scroll selection, and diagnostic payload shape.
- Passed Norm's original offline conversation adapter and ZIP/MD/JSON/local PDF regression test.
- **Not yet verified against an authenticated ChatGPT session in KHzz's Qt WebEngine**. ChatGPT's current login/antibot compatibility cannot be guaranteed without that live test; popup support alone does not bypass sign-in challenges.
- The primary API-first extractor still uses undocumented ChatGPT endpoints. Original DOM/JSONL, visual/OCR, and native ReportLab/WeasyPrint facilities are preserved to avoid destructive redesign.
- No-Celery changes, N1/N2 task ownership, suppression/resumption, installer and user-plugin preservation remain unchanged.

## KHzz smoke test after installation

1. Start Norm and confirm **no hidden Qt browser is launched** without an explicit browser action.
2. Run `Show Shared Browser.cmd` and open `https://chatgpt.com/`; sign in in the same persistent profile, including popup login if needed.
3. Run `browser_shared_chatgpt_diagnostics()` and verify the reported UI state. If `challenge` or `login`, investigate the actual page before exporting.
4. Test `browser_shared_type('#prompt-textarea','test draft')` *without sending*, and `browser_shared_scroll(600)` on a long conversation.
5. Verify the API-first exporter reports full-history coverage separately from missing attachments; do not claim visual fidelity when assets are missing.
6. Run `python tools/test_browser_cooperation_r11.py` for the original offline regression. `python tools/test_shared_browser_chatgpt_r11r9.py` runs the additional static checks.

To keep the installed browser profile, do **not** remove the configured `workspace/web/shared_browser/profile` or `cache` folders. No credentials or session cookies are present in this source archive.
