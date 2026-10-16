# Norm future implementation notes

This file contains only active, unimplemented work for the current Norm design.

## Celery post-r2 follow-ups

Live-test asynchronous N1-approved plugin calls, out-of-order completions,
same-task resumption and final-conclusion continuity. Verify that Redis DB is
operator-selectable via config/settings.ini and that workers do not share a
process-local connection pool. Run recovery/invalidation checks before enabling
write-capable plugins in production. Add worker cancellation/stop-all policy.

## Installer transactional promotion

Make managed source updates transactional across source sync, dependency validation, candidate executable build, and final promotion. A failure after staging should have a defined rollback to the previously verified runtime tree.

## Persisted last-known-good plugin rollback

Add an optional persisted verified plugin snapshot so cold start can recover from a newly broken plugin source tree without manual restoration.

## Stronger durable validation outbox

Add an independently durable small outbox/background flusher so Redis loss cannot discard the most recent unmigrated evidence/change observations.

## Shell/capability hardening

- Prefer typed operations for filesystem/database/network actions.
- Ensure alternate shell/plugin paths cannot bypass canonical file authorization.
- Add active-call cancellation when required authority disappears during a running subprocess/tool operation.

## General filesystem mutation evidence

Add a low-overhead mutation-evidence mechanism for arbitrary shell/plugin writes without expensive whole-tree scanning.

## Curated-memory deduplication

A future curated-memory cleanup action may deduplicate or resolve contradictions in the active memory store itself. This is distinct from task-history /memory-condense and /memory-condense -full.

Requirements: no deletion because of age alone; preserve reusable lessons; use contradiction/supersession confidence gates; checkpoint independently; fail closed; rebuild retrieval/background summaries only after a valid result.

## Temp artifact registry

Add explicit task-time registration for disposable artifacts with task ID, purpose, TTL, reproducibility, and promotion state. Active/unverified recovery material remains protected.

## Full-backup encryption option

Add an optional encrypted export path for full backups moved outside the trusted machine without silently excluding secrets, SSH, or PostgreSQL recovery material.

## Stronger plugin export declarations

Add explicit tool-export declarations so compatibility wrappers/helpers cannot become model-visible merely because they are public Python functions.

## Chart-axis calibration enforcement

Move the chart-axis calibration contract into deterministic machine checks where practical.

## Lower-privilege high-impact capability broker

Evaluate a separate low-privilege broker for high-impact filesystem, shell, and database actions while preserving the current N1/N2 boundary.


## 0.55.1 follow-up verification (2026-10-08)

- Context injection lifecycle is implemented in source, but run a live PostgreSQL migration and a queued task with an injection that is verifiably completed mid-task. Confirm only the flagged injection retires and later steps do not repeat its work; preserve older persistent injections.
- Voice N2 conditioning was wired in source; compare one fresh queued worker answer before/after `/voice-tune Cockney --preset cockney` and `/voice-activate Cockney`. `/voice-test` measures local model response variation but not actual queued-path delivery.
- A deterministic, general-purpose operation-idempotence guard at the action boundary (beyond injection retirement) remains open. N1 reuse/verification and prompt admonitions do not fully guarantee duplicate mutations are blocked.
- Lesson-model plugin still does not train/promote model weights; installer Windows end-to-end, OCR Tesseract binary management and remote-app authentication remain separate verification or security tasks.

- Audit installer 2.0.0 against this actual source manifest. It intentionally does not contain the former `build_script` field or `tools/build_norm.py`; legacy 1.6.x installers requiring them are not known-compatible.
