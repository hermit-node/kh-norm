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


## Visual PDF export (installed integration, activate on next shared-browser start)
"Visual PDF" toolbar button or browser_shared_visual_pdf(name="") captures
the *same* authenticated Qt Chromium tab from top to bottom using DOM scroll
positions. It analyzes overlaps before publishing the stitched PNG and a
paginated PDF. Suggested filenames use recent USER turns (not assistant text);
Norm may supply a shorter, descriptive name through the plugin call.
Use browser_shared_visual_status and browser_shared_visual_cancel.
Exports live in configured workspace/web/shared_browser/exports. Existing
exports are never overwritten, and the text-first browser_shared_pdf tool
is unchanged. This is a visual image PDF (not searchable text), and ChatGPT's
virtualized/unloaded messages cannot be certified complete.
Capture needs a visible window and exclusive Norm control; navigating or
hiding the shared page while capturing is blocked.

## r5: Unified PDF export formats and operator slash shortcuts

The existing **Norm Shared Browser** plugin now owns all three PDF formats:
`browser_shared_pdf_export(format="text|visual|dialogue", name="", source="", output="", include_original=False)`.

* **text** uses the existing JSONL-to-ReportLab exporter, and is not advertised as fully historical unless source coverage is independently audited.
* **visual** triggers the existing asynchronous shared-browser capture; query progress with `/pdf-status`.
* **dialogue** reformats an **existing ChatGPT Exporter PDF**, using PyMuPDF native vector content (no OCR). Original fonts, drawings, shaded code, images, bullets and chat bubbles remain embedded. Source remains unchanged. User turns are bookmarked by date; `include_original=True` appends the original pages. Fails closed if text tokens or image bytes are missing, and publishes a JSON SHA256 verification report.

In Norm Prompt use `/pdf` to list shortcuts, `/pdf-chat "SOURCE.pdf" --name "TITLE"`, `/pdf-name`, `/pdf-verify`, `/pdf-visual`, `/pdf-status`, `/pdf-text`, and `/pdf-cleanup` (dry-run first). Slash operations execute locally instead of submitting model tasks. All new source file reads/writes use Norm's canonical `shared_browser` file-access policy.

Naming: `{title} - Rich Dialogue.pdf` beside the original or an explicit output path. There is no silent replacement of prior exports. `/pdf-chat` now lays the reading edition out like a clearer back-and-forth chat transcript while still preserving source vectors. For better naming, ask Norm to call the title-context tool first; filenames from `/pdf-chat` default to the source's cleaned title.
