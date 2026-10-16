# Norm Shared Browser — 0.55.9r9 prototype

## Run it

After installing, sign into Windows normally and use either
C:\Norm\tools\shared_browser\Start Hidden Browser.cmd or
C:\Norm\tools\shared_browser\Launch Browser.cmd.
The helper runs in the logged-in desktop, not Windows service session 0.

Browser_shared_show gives the human exclusive control of the same
Chromium webpage, with the original tabs, cookies and DOM intact.
Click Return Control to Norm (or use browser_shared_release) to unlock
and hide the window. Hide alone does not release the human lock.

Use browser_shared_open(url), wait for browser_shared_status(), and
use browser_shared_dom, browser_shared_bs_select, or
browser_shared_messages to extract HTML/text without mouse clicks.
Browser_shared_scroll uses DOM scrolling to trigger lazy loads.

Browser_shared_archive_loaded(name) incrementally writes currently-loaded
ChatGPT turns in 16K-character chunks to JSONL with a SQLite dedup index
under workspace/web/shared_browser/exports. Repeating the call appends
only new/changed content. Browser_shared_pdf(name) turns the JSONL into
searchable text PDF. The PDF is text-first; images, rich code/tables,
and entire-conversation completeness are not guaranteed.
The response explicitly marks entire_conversation_verified = false.
Long chats may still require multiple programmatic DOM loading passes.

## Memory

QtWebEngine is Chromium. This build accepts a 32-bit HTTP cache
limit of 2,147,483,647 bytes (~2 GiB); larger numbers were ignored.
The process also requests a 12 GiB JS heap ceiling (NOT preallocated RAM)
and permits up to 32 GiB of conversation archives. Large budgets alone
cannot prevent huge, rendered DOM trees from crashing Chromium.
The profile/cookies/cache under workspace/web/shared_browser persist
across show/hide and restart and are EXCLUDED from source packages.

## Playwright and native GUI

The existing web_browser plugin still operates Playwright separately.
We directly tested Playwright 1.63 attaching to QtWebEngine Chromium CDP:
the WebSocket connects, but Browser.setDownloadBehavior fails because
Qt does not support Playwright browser context management. It cannot
reliably manipulate this exact Qt tab. Use Qt runJavaScript/DOM on the
shared tab; use normal Playwright browser sessions for other work.

The existing desktop_gui plugin provides pywinauto and MSS read-only
facilities, with some native input tools deliberately disabled.
This release does not bypass N1 authorization or silently enable them.

The helper binds only 127.0.0.1:18767 and authenticates requests using
a unique per-install bearer token at state/shared_browser_token. Never
share this token. Opt-in CDP at 127.0.0.1:18768 is DISABLED by default.

## Celery

Only the Norm-local .venv/Scripts/pythonw.exe (or python.exe from the
same environment) may run Celery workers. The worker validates this
path. On actual Norm service shutdown, Norm checkpoints/stops N2 first,
then gracefully stops verified Norm-owned Celery workers, and force
stops verified stragglers. This does not terminate unrelated Python
processes. Closing just a Norm prompt console does not stop Norm service.

## Status

Qt DOM smoke tests passed. JSONL/SQLite/PDF/SoupSieve tests passed on
synthetic conversation data. The shared browser has NOT been tested
against a real logged-in ChatGPT conversation and is a prototype,
not an assertion that every very long conversation can be captured.
