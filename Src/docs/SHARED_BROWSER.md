# Norm Shared Browser — 0.55.9r9 prototype

## Run it

After installing, sign into Windows normally and double-click
C:\Norm\Show Shared Browser.cmd to expose the already-running
browser. The older Launch Browser.cmd now does the same thing.
Use tools\shared_browser\Start Hidden Browser.cmd to start
the hidden helper without creating another instance.
The helper runs in the logged-in desktop, not Windows service session 0.

Use Show Shared Browser.cmd in the Norm root, or browser_shared_show,
to reveal the same Chromium page without changing who controls it.
Take Control grants the human the page and pauses Norm; Return Control
to Norm releases that lock WITHOUT hiding the window. Hide/Show
never change the owner, cookies or DOM.

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

## Graceful complete shutdown

Run Safe-Shutdown-Norm.bat, not taskkill. The batch delegates to
tools/safe_shutdown_norm.py. It requests the normal activity API shutdown,
waits for Norm to drain and checkpoint work, then verifies the installed
process is gone and performs scoped orphan-worker/GUI cleanup. No alive
Norm service is force-killed by this BAT. Use --dry-run to inspect.
Default wait is 300 seconds; optionally --wait-seconds 900.
Norm's own shutdown finally closes both psycopg pools after stopping
N2 and the servers, and each Celery worker closes its own pool.
All three operator console hosts receive a shutdown marker.
This does NOT kill the PostgreSQL server or unrelated client connections.


## r11 independent control and visibility

browser_shared_show reveals the same Chromium tab without granting manual
control; browser_shared_hide only hides it. browser_shared_take_control
gives the human control and pauses Norm actions; browser_shared_release
returns control to Norm WITHOUT hiding. The toolbar also has explicit
Take Control and Return Control buttons. The X button hides the window.

Profile/cache live under configured paths.workspace_root/web/shared_browser.
The archives and PDF outputs are under its exports subfolder.
Empty tabs and invalid ChatGPT conversation URLs are rejected.
Title-only empty archives, unusable message collections and mixed-chat
JSONL files cannot generate a successful PDF. Successful PDF results
say partial_unverified until full conversation coverage is proven.

## Windows show shortcut (r11)

Double-click C:\Norm\Show Shared Browser.cmd to expose the existing
browser. This sends an authenticated local Show command, NOT an
independent Chromium browser/profile. If the browser is not running,
the normal interactive-session starter launches the helper first.
The older Launch Browser.cmd is now a compatibility alias.
