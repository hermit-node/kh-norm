# Release

## Norm 0.53.16 / Installer 1.6.7

- N1/N2 tool gating and verified-result reuse remain the execution boundary.
- Canonical internal state_root keeps trusted runtime state separate from model file-tool authority.
- /memory-condense is recent-only; /memory-condense -deep is manual bounded older-history compaction.
- Scheduled maintenance alternates successful regular/full passes.
- Full condensation sweeps the whole date-ordered archive and samples 12 per 200 compact rows by default.
- Replay validation is isolated per sample with up to 4,800 x 4 output tokens.
- Full mode performs one hierarchical merge level over neighboring windows of up to six rows; only validated replacements supersede originals.
- Windows checkpoint replacement is hardened with unique temp files, fsync, retries and durable fallback.
- /stop-all now coordinates exact Norm console hosts with readable 5-second/10-second close windows and Enter/Ctrl+C interruption.
- WeasyPrint 70.0 / Pango 1.58.2 is integrated into the turnkey release; Git source keeps reproducible acquisition metadata instead of vendoring its frozen runtime.
- Installer 1.6.7 validates the release runtime with both info discovery and an actual HTML-to-PDF render.
