# Norm Future Implementation Notes

This is the active backlog/design notebook for behavior that is not fully implemented. `README.md` describes operation, `CURRENT_STATUS.md` describes current facts, `RELEASE_NOTES.md` records shipped/source-release deltas, and `DEVELOPMENT_NOTES.md` preserves engineering history.

## Current backlog after 0.52.0 source consolidation

1. Run full Windows acceptance on a real installed 0.52.0 build: installer in-place update, venv reuse, executable build, service/API/GUI health, plugin hot-reload, and a real task.
2. Validate the full-backup plugin and installer restore path against a disposable PostgreSQL schema and test installation before relying on it as the sole disaster-recovery mechanism.
3. Rotate any historically exposed PostgreSQL credential and verify every consumer after rotation; old traces are not automatically scrubbed.
4. Extend command execution guardrails so an alternate shell/plugin path cannot bypass a file-tool denial. Prefer reviewed typed operations and least-privilege execution over arbitrary shell text.
5. Add active-call authority cancellation: if required Tailscale/Redis authority disappears during a running subprocess/tool call, terminate it and record an interrupted/uncertain result.
6. Bound startup schema reconciliation with PostgreSQL lock/statement timeouts or a separate migration preflight so stale sessions cannot leave partial startup looking healthy.
7. Continue normal context compaction work: bound ordinary completed-step context and preserve durable pointers rather than copying unlimited prior output.
8. Machine-enforce the generic chart-axis calibration gate rather than relying only on prompt/document discipline.
9. Consider a separate low-privilege capability broker/coordinator for high-impact filesystem/shell/database actions.


## Manual `/condense-memories` operator action

Do not implement this command as a weak alias for background-snapshot rebuilding. The intended action must run non-recursively through the low-level maintenance/model path, traverse the full curated/current memory set, identify true duplicates/contradictions/resolved or superseded state, preserve reusable lessons before deleting redundant rows, never delete merely because a record is old, checkpoint its own continuation state, and rebuild retrieval/background summaries only after the curated-memory result is valid. Add the command only when those semantics can be tested fail-closed.

## Cleanup/workspace follow-up

The current temp policy is deliberately conservative. Future refinement may add explicit temp-artifact registration at tool-write time so every disposable file has task ID, purpose, TTL, and promotion state rather than relying partly on directory convention and age. Never weaken the current rule that active/unverified task recovery material is preserved.

## Backup follow-up

Full backups intentionally contain private keys and secrets when requested. Consider optional encryption (for example an external 7-Zip/AES path) before moving these archives off the trusted machine. Do not silently weaken the backup by excluding secrets/SSH if the user requested a complete restore package.

## Documentation/configuration rule

Maintained document pointers belong in `config\settings.ini`. Historical notes may retain old paths as history, but current-state documents must use the current `core\`, `docs\`, `plugins\`, `.ssh`, external `workspace\`, and external `temp\` layout.
