# Norm 0.53.14 portable source package

This ZIP is the exact **Norm 0.53.14** source payload consumed by Installer **1.6.6-unified**. It is source-only: `.venv` and compiled `core\norm.exe` are generated/reused during installation and are not shipped inside the portable source archive.

For current runtime behavior see:

- `docs/README.md` — current operator/runtime overview
- `docs/CURRENT_STATUS.md` — current 0.53.14 state only
- `docs/RELEASE_NOTES.md` — version/change history
- `docs/MAINTENANCE_VERIFICATION.md` — verification evidence
- `docs/FUTURE_IMPLEMENTATION_NOTES.md` — active backlog/design notes

## Current 0.53.14 behavior

- End-of-task durable summaries remain enabled. Structured generation is silent; the complete saved summary is emitted once as an `End-of-task durable summary` runtime block.
- Durable thread/runtime summaries are current-state projections and may shrink. Superseded-state ledgers, embedded recent-message transcripts, and conversation-log sections are rejected/rebuilt.
- Normal Ollama answer/thinking fragments publish as they arrive rather than waiting for newline/1 KiB display buffers.
- Prompt interpretation remains enabled for intent and durable side information, but executable wording only removes exact clearly separable sidecar spans. Embedded task qualifiers remain intact.
- `memory.consolidation_batch_chars=14000` remains a separate maintenance batching limit.
- `vision_parse` 0.2.1 handles up to **10 PDF pages per call** with adaptive **1.5x / 2.2x / 2.9x** rendering, dense-layout splitting, completion-length fallback, repeat-loop retry, and bounded in-process page reuse.
- `write_file` / `replace_text` retain guarded core mutation policy while delegating exact UTF-8 temporary-file writing to the private hash-verified `verbatim_lines` primitive.
- Package-local 7-Zip remains the preferred archive backend.
- N1/N2 checkpoint 1 is active: N2 remains the reasoning/worker path; user text and N2 user-facing output pass through N1 unchanged; N1 gates model-requested tools, reuses live verified answers when appropriate, records fresh observations, and can halt a judged repeated reasoning/tool turn before its tools execute. Fresh executor results reach N2 unchanged.
- DB3 prompt-ID idempotency, the canonical shared PostgreSQL pool adapter, replay-validated deep-history maintenance, and the compact Redis validation pool remain part of the package.

## Current layout

- Runtime root is relocatable; installed path is written to `config\settings.ini`.
- Runtime source/executable directory: `core\`.
- Maintained docs: `docs\`.
- Dynamic plugins: `plugins\`.
- External durable workspace: `%USERPROFILE%\Documents\Norm\workspace`.
- Disposable/recovery area: `%USERPROFILE%\Documents\Norm\temp`.
- Persistent machine-specific state/secrets are external to the portable source package and are preserved/migrated by the installer according to its update rules.

## Package contract

`package-manifest.json` is the installer-facing package contract. Plugin `plugin.json` files and source-tree SHA-256 identities are authoritative for package-managed plugin code. The portable package contains no user secrets.
