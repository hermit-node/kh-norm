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
