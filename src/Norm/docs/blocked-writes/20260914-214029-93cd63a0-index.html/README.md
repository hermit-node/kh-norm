# Norm blocked write fallback

Target: `D:\LOCAL_Share\Code Projects\pogicraft.github.io\index.html`

Operation: `replace_text`

Attempts: 4 (initial attempt plus 3 retries)

Reason: `PermissionError: [WinError 5] Access is denied: 'D:\\LOCAL_Share\\Code Projects\\pogicraft.github.io\\.index.html.normtmp-682cbaa93a544937b3c896feef05eabf' -> 'D:\\LOCAL_Share\\Code Projects\\pogicraft.github.io\\index.html'`

Expected target SHA-256: `b602f206b2566fe2cd12324af5b998c3024818eb501a93c3db3e641eab25424a`

Desired content SHA-256: `0731c51b0f65ae63104871a3ed367557518d2cb5bcc23d7952e3bbf125c55c87`

Staged desired file: `C:\Users\KHzz\Documents\Norm\docs\blocked-writes\20260914-214029-93cd63a0-index.html\desired-index.html`

## Intended operation details

```json
{
  "operation": "replace_text",
  "old_text": "                <h3 class=\"offset-2\">Footnotes</h3>\n                <p>Started: 11/23/2022, Updated: 11/30/2022</p>",
  "new_text": "                <h3 class=\"offset-2\">For inquiries</h3>\n                <p><a href=\"mailto:alexanderjames875@gmail.com\">alexanderjames875@gmail.com</a></p>\n                <p>Started: 11/23/2022, Updated: 11/30/2022</p>",
  "expected_occurrences": 1
}
```
