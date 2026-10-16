# Norm Browser Cooperation — 0.55.10r11 source candidate

## What is implemented

This is an additive extension of r10, not a redesign. The existing interactive Qt WebEngine browser, its persistent `NormShared` profile and cookie jar, existing HTTP disk cache and archive locations, GUI focus arbitration, visual PDF mode and pre-existing `web_browser`/Playwright and desktop GUI/MSS plugins remain intact.

`plugins/norm_browser/` is a schema-2 runtime plugin with a source-verified identity. It provides:

- `browser_cooperation_status()` — inspected page and permissions, installed scripts.
- `browser_script_execute(source, world="isolated")` — bounded custom JavaScript against a manually approved HTTPS origin; optional `world="main"`.
- `browser_script_register(name, source, world="isolated", timing="ready")` — named persistent injection at document readiness or creation. Scripts are profile-wide but use exact origin guards, and only become enabled after the human approves that origin using the browser's **Site scripts** action.
- `browser_script_remove(name)` — removes future injection. An already-installed page hook may remain until a reload.
- `browser_open(url,mode="shared")` — existing Qt shared browser by default, or existing independent Playwright plugin when mode is `playwright` (visible) or `headless`. Independent Playwright does **not** use the shared Qt cookie jar.

### Built-in `convo_export` extension

`plugins/norm_browser/src/extensions/convo_export/` contains a selectable ChatGPT export with **zero vendor processing or uploads**. The page adapter lives in `tools/shared_browser/conversation_js.py` and prefers the authenticated conversation API and active parent-chain over DOM snapshots. It includes an early, ChatGPT-origin-limited fetch-observer for authentication already present on the page; credentials never cross the local browser RPC or appear in exported JSON. If structured extraction fails, it explicitly marks DOM output partial.

The browser's left **Export** action opens a compact in-window selection pane. Each message has a checkbox. Bulk choices are **All / User / Assistant / None / Has media / Has code / Invert**. Choose the title, ZIP, PDF, Markdown or JSON, timestamps, model labels, attachments, Letter/A4 paper and comfortable/compact spacing, then **Export selected messages**. The saved location and number of unresolved assets are displayed, with **Open export folder**.

ZIP bundles `conversation.md`, `conversation.json`, `manifest.json` and `assets/`. Markdown and JSON exports with attachments put assets in a sibling folder. PDF is generated locally with ReportLab. Export refuses empty selections. Signed URLs are resolved from the current page when supported, downloaded through bounded HTTPS host checks, and omitted from the durable JSON. Every unresolved asset is recorded in `manifest.json` for ZIP (and counted in the UI for all formats). Unknown MIME types may not display inline in PDF, but their status is shown.

**Coverage is limited to the active conversation branch**, not every edited/regenerated branch. ChatGPT APIs are undocumented; this source candidate has not been validated against the current live site. Image placement in Markdown is appended to the corresponding turn, not guaranteed to retain pixel-exact inline positions. Other websites require separate adapters.

### Browser layout and Ask Norm

The existing URL navigation stays at top. Quick actions are on the left; the live page takes the remaining width; a collapsible **Ask Norm** input emerges from the bottom. This is **not a shell**, and it does not send raw command execution to the browser. It converts the typed natural-language request into a contextual prompt with a snapshot of the current page URL/title, written into the **existing Redis DB3 GUI ingress stream** for the normal N1/N2 dispatcher. If the existing Prompt Console consumer isn't available, the browser refuses submission instead of claiming success. It does not create a second Norm agent, stream, or dispatcher.

The browser does not deliberately raise or activate its window when Norm opens/takes control. Qt's `WA_ShowWithoutActivating` is set; actual foreground behavior should be checked on Windows, including native dialogs. User clicks behave normally.

### Authorization and boundaries

- Only the human browser UI can approve/revoke custom-script domains; RPC has no grant operation.
- Custom code runs inside the approved page's JavaScript contexts, **not** in Python. Browser page scripts never receive a `QWebChannel` to privileged Norm APIs.
- Existing human/Norm arbitration still blocks intrusive RPC operations when the human has control. Selective conversation reads remain available.
- The browser RPC remains loopback-only and bearer-token authenticated, with the original request/response bounds.
- No broad network enumeration, browser profile duplication or source repair is performed.
- Source ZIP contains no runtime profiles, browser cache or auth tokens.

### Operational checks before promotion

1. On KHzz, launch r11 source in the actual PySide6/Qt environment and confirm the shared browser starts, hides, appears without foreground focus theft and retains signed-in cookies.
2. Check Site scripts approve/revoke and named early/ready injection on one test HTTPS origin; check unwanted origins cannot execute custom scripts.
3. Open an existing ChatGPT conversation, select alternating turns and verify the chosen count and ordering across PDF, Markdown and ZIP. Verify missing-asset disclosure.
4. Test long conversations with virtualized DOM to distinguish API-complete vs DOM-partial and test image resolution, generated images, attached PDFs and Canvas textdocs; only the first two have initial dedicated normalization in this candidate.
5. Run a user/agent collision test: user actively types while N2 requests page navigation. Verify human priority and unchanged foreground window.
6. Confirm 12 GiB heap target, ~2 GiB Qt HTTP cache limit and 32 GiB archives via `browser_shared_status`, and verify the pre-existing independent Playwright and MSS capabilities still work.

## Offline tests

Run `python tools/test_browser_cooperation_r11.py` for Node JavaScript parsing/API simulation and local PDF/MD/JSON/ZIP generation. Run `python tools/test_shared_browser_r9.py` for the existing DOM/archive/PDF baseline. Offline tests do **not** imply a successful Windows Qt GUI or current ChatGPT API test.
