# r22 — Lean step-boundary context handoff

Baseline: uploaded `Norm-0.55.10r21-Strict-Recovery-No-Celery-Browser-Assets-Portable-Source.zip`.

## Scope

Norm already starts every queued N2 step with fresh Ollama messages, and N1 already independently reviews the step before normal completion. The full task event stream and durable results are not copied into the next step's new message list. This patch **does not** add another model, another summary pass, a new step planner, or another gatekeeping action.

The two remaining sources of unwanted cross-step bulk were:

- **Background rehydration:** every ordinary queued step could receive the global `memory.background_context_chars` allowance (32,000 chars in the r21 default configuration), regardless of the step's index.
- **Self-verification input:** `_generate_with_budget()` always fetched `live.task_evidence(task_id)`, and `_verifier_prompt()` serialized that task-wide evidence, even when the worker was verifying only one intermediate step. Previously completed directory inventories were thus unnecessarily sent back through the verifier.

## Changes

1. For planned `step` / `task_step` jobs with `generated_step_index > 1`, cap generic background-memory injection to `worker.step_boundary_background_chars` (default **8,000 characters**); honor the smaller existing memory budget if configured. The original user prompt, current step, ingrained constraints, on-demand history instructions, Redis compact activity index, and PostgreSQL prior-step index remain unchanged. This is an input budget, not deletion from storage.
2. Add `RedisTaskLog.step_evidence(task_id, step_id)` as a **read-only filtered view** of the existing Redis evidence stream. Ordinary intermediate-step verification reuses evidence for its own step only. Conclusion and final-candidate review continue to receive task-wide evidence, as before. Redis and PostgreSQL records remain untouched.
3. Emit a diagnostic `N2 fresh step context ...` log describing prior-index and background-memory character counts. No new summarization inference call is required.

## Existing boundaries left unchanged

- N1's private step intent, independent acceptance/retry decision, before-tool/after-tool oversight, and final acceptance.
- Task planning and existing Redis dependency parking, suppression, recovery, and checkpoint semantics.
- Original prompt preservation and user-context injections.
- Full Redis evidence, PostgreSQL history/context slices, on-demand `read_task_record`, recovery indexing, and final verification.
- No-Celery source, shared browser, installer, plugins, credentials, and connection settings.

## Regression coverage

`python tools/test_step_boundary_context_r22.py` runs using only the standard library, extracting the changed methods for isolated functional tests. It checks filtered step evidence, unchanged full Redis evidence, first-step/later-step/conclusion budgets, intermediate-step verifier input, and retained whole-task final verification.

The changed source compiles, and release hashes are regenerated. **Not yet live-validated on KHzz** with active Redis, PostgreSQL, Ollama, or installer. A staging install is required before promotion. This release is source-only and intentionally does not modify a running Norm instance.
