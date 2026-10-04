# Norm Future Implementation Notes

This is the active backlog/design notebook for behavior that is not fully implemented. `README.md` describes operation, `CURRENT_STATUS.md` describes current facts, `RELEASE_NOTES.md` records shipped/source-release deltas, and `DEVELOPMENT_NOTES.md` preserves engineering history.

## Boundary after the 2026-10-02 maintenance source

Implemented: shared advisory validation counts/generations, Redis live versus PostgreSQL snapshot wording, budgeted context, no-op recovery completion, deterministic instrumented-write verification, explicit first-party plugin identity/checksums and docs consolidation. These are no longer design-only items.

Deferred: refresh and validate the Installer 1.6.6 embedded payload, sanitize/synchronize the public source tree and publish only when authorized, rebuild/test the frozen executable, and promote live after acceptance. No live promotion is claimed by this source checkpoint. HTTP prompt-id idempotency and recovery-note relocation/self-healing are not implemented here.

Remaining persistence limits: snapshots are observation-triggered, not a background timer; loss of Redis can lose confirmations since the last PostgreSQL snapshot or pending history. A stronger independently durable outbox/background flusher is future work. Plugin checksums are not signatures, and last-known-good hydration is in-process rather than a persisted cold-start rollback copy. Arbitrary shell writes/uninstrumented plugins still need explicit artifact evidence; this is not a general filesystem mutation monitor.

## Continuing backlog (historical items retained)

- Make installer source sync transactional: stage/validate dependencies and candidate executable before committing a new managed source tree, with a defined rollback path if post-sync dependency/build validation fails.
- Add explicit plugin export declarations so compatibility `run` wrappers and helper functions do not automatically become model-visible native tools.
- Route the `verbatim_lines` plugin through Norm's canonical allowed-root policy; it is currently an intentional exact writer but should not be a second unrestricted filesystem API.
1. Run full Windows acceptance on a real installed 0.52.5 build: installer in-place update, venv reuse, executable build, service/API/GUI health, plugin hot-reload, and a real task.
2. Validate the full-backup plugin and installer restore path against a disposable PostgreSQL schema and test installation before relying on it as the sole disaster-recovery mechanism.
3. Rotate any historically exposed PostgreSQL credential and verify every consumer after rotation; old traces are not automatically scrubbed.
4. Extend command execution guardrails so an alternate shell/plugin path cannot bypass a file-tool denial. Prefer reviewed typed operations and least-privilege execution over arbitrary shell text.
5. Add active-call authority cancellation: if required Tailscale/Redis authority disappears during a running subprocess/tool call, terminate it and record an interrupted/uncertain result.
6. Continue normal context compaction work: bound ordinary completed-step context and preserve durable pointers rather than copying unlimited prior output.
7. Machine-enforce the generic chart-axis calibration gate rather than relying only on prompt/document discipline.
8. Consider a separate low-privilege capability broker/coordinator for high-impact filesystem/shell/database actions.


## Destructive curated-memory `/condense-memories` operator action

`/memory-condense` and `/memory-condense -full` now invoke replay-validated deep-history consolidation (bounded manual pass versus all-terminal full pass). Do not implement `/condense-memories` as an alias for either of them; curated-memory deduplication/contradiction cleanup remains a separate future action. The intended action must run non-recursively through the low-level maintenance/model path, traverse the full curated/current memory set, identify true duplicates/contradictions/resolved or superseded state, preserve reusable lessons before deleting redundant rows, never delete merely because a record is old, checkpoint its own continuation state, and rebuild retrieval/background summaries only after the curated-memory result is valid. Add the command only when those semantics can be tested fail-closed.

## Cleanup/workspace follow-up

The current temp policy is deliberately conservative. Future refinement may add explicit temp-artifact registration at tool-write time so every disposable file has task ID, purpose, TTL, and promotion state rather than relying partly on directory convention and age. Never weaken the current rule that active/unverified task recovery material is preserved.

## Backup follow-up

Full backups intentionally contain private keys and secrets when requested. Consider optional encryption (for example an external 7-Zip/AES path) before moving these archives off the trusted machine. Do not silently weaken the backup by excluding secrets/SSH if the user requested a complete restore package.

## Documentation/configuration rule

Maintained document pointers belong in `config\settings.ini`. Historical notes may retain old paths as history, but current-state documents must use the current `core\`, `docs\`, `plugins\`, `.ssh`, external `workspace\`, and external `temp\` layout.
