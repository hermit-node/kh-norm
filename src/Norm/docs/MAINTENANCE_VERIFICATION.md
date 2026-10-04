## 2026-10-04 N1/N2 tool-gate checkpoint 1

- PASS: N1 pass-through identity hooks return user ingress and N2 user-facing text unchanged at the agent boundary.
- PASS: three consecutive materially identical N2 reasoning/tool turns trigger the N1 loop-judgment path in the regression harness; a positive judgment yields a reset instruction and runtime wiring blocks that repeated turn before tool execution.
- PASS: known file mutations invalidate both the mutated target and its parent-directory validation target so cached directory listings cannot survive a file create/replace.

- Full source syntax compilation passes after adding `core/norm_runtime/n1_gatekeeper.py`, wiring it into the worker/direct-tool paths, and adding per-role runtime configuration.
- `python tools/test_n1_gatekeeper.py` passes schema instrumentation, Redis answer reuse, unmodified fresh-result forwarding, identical-request raw reuse, loop-reset emission, mutation invalidation, and third-repeat blocking for non-cacheable tool requests.
- `python tools/test_validation_round_refresh.py` now exercises the checkpoint-1 ownership model directly: N1 sees the live Redis `num_checks` before deciding and returns an existing verified value without invoking N2's proposed tool.
- Static executor-path review confirms model-requested tool execution in `PromptWorker` and degraded direct chat routes through N1 when configured. The remaining direct `read_file` in `_verify_pending()` is the deterministic post-write safety read-back and is intentionally not a model-requested tool.
- The packaging container does not have the managed runtime `redis`/`psycopg` dependencies installed, so service-backed legacy tests were not falsely reported as rerun here.

## 2026-10-03 suppress/flush race regression

Added regression coverage for the operator sequence `suppress active generation -> flush suppressed -> suppress again while cancellation unwinds`. Expected behavior: the active transition marker remains until worker acknowledgement, durable flush is allowed, the duplicate suppress is idempotent, and no stale transition marker remains after `_handle_suppressed()`.

# Maintenance source verification — 2026-10-02

## 0.53.13 runtime-summary / prompt-fidelity / vision checks

- End-of-task durable summaries remain enabled and publish once after silent structured generation.
- Runtime-summary regression verifies coverage-cursor advancement, stale-version pruning, rejection of `Superseded information`/`Recent messages` ledger shapes, deterministic fallback after repeated invalid/model-failure results, and independence from `memory.consolidation_batch_chars=14000`.
- Prompt-fidelity regression verifies that an ordinary task sentence cannot be removed merely because the classifier marks it memory-worthy, while an exact `By the way, ...` aside may still be extracted.
- Runtime stream regression verifies immediate Ollama fragment publication plus one complete `durable_summary` event.
- `vision_parse` 0.2.1 keeps the ten-page cap and uses 1.5x/2.2x/2.9x adaptive render tiers; prior truncation/retry/layout/cache behavior is retained.


Historical baseline: runtime `9b6bb13`, plus the preserved optional-entrypoint change. Current packaged Norm is 0.53.14; Installer is 1.6.6-unified.

## Automated evidence

- `python tools/test_maintenance.py`: 17 regression tests covering recovery no-op/root-artifact contracts, read-after-last-write ordering, missing/mismatched hashes, plugin mutation evidence, context budgeting, unknown durable recent counts, all ten identities, corrupt/import-failing candidate retention, identity removal, cold invalid plugins, unsafe checksum paths, missing coverage, and legacy plugin support.
- `python tools/test_validation_services.py --services-root C:/Norm`: actual Redis/PostgreSQL tests in unique test-only namespaces. Covers rolling-window expiry, unchanged/changed generations, unrelated-task sharing, 40 concurrent increments, history, stale-snapshot rejection, expected snapshot lag and due checkpoints. Also checks completed-generation retention through a PostgreSQL history outage and successful retry. Verifies Redis and PostgreSQL cleanup. The PostgreSQL pool gate also proves an eight-connection cap: with all eight checked out, the ninth checkout times out instead of opening another connection.
- Source tests do not certify frozen executable behavior, GUI/installer interaction, live deployment or remote publication.

## Preservation and remaining work

Compact recovery handoff implementation is unchanged from the baseline. Original installer/public Git state is preserved; installer wrapper is 1.6.6-unified. Current source status and operator semantics are in [CURRENT_STATUS.md](CURRENT_STATUS.md) and [README.md](README.md). Deferred items are in [FUTURE_IMPLEMENTATION_NOTES.md](FUTURE_IMPLEMENTATION_NOTES.md).

## Additional checks and limits

All ten first-party plugins declare 59 public entrypoint functions in the current source (the pre-voice nine-plugin baseline hydrated as 42 native tools) with the installed runtime dependencies, and the diagnostic scan verifies all identities. All 91 Python files in this packaged tree compile syntactically in the packaging validation. AST comparison confirms the three handoff note/persistence/locator methods are unchanged. Git whitespace checking passes.

The optional Akinator strict documentation checker reports three path findings: generated runtime paths (such as the built executable and SOS note) and historical paths in DEVELOPMENT_NOTES are not files in this source-only checkout. Those references were retained as operational/history documentation; this checker is not reported as passing. A first hydration attempt with system Python lacked Pillow; the installed runtime interpreter provides it and all nine hydrate there. Initial temporary-directory permission and snapshot-timezone failures were corrected before the passing test run.
## 0.53.12 startup/package compatibility checks

- Python syntax compilation covers the modified shared GUI helper, operator-console launcher, Runtime/Replies helpers, settings path resolver, and verbatim compatibility shim.
- Targeted source checks verify that Prompt and Replies can import the restored lazy `load_runtime_config` API.
- Verbatim path resolution is tested against both schema-2 `plugins/verbatim_lines/src/_cli.py` and legacy `plugins/verbatim_lines/_cli.py` layouts; the package carries the legacy shim without changing the schema-2 `src/` identity hash.
- `Run-Norm.bat` now delegates operator-window creation to `start_operator_consoles.py`; all three surfaces use `operator_console_host.py`. Runtime/Replies no longer use `norm.exe` process disappearance as an automatic exit condition.
- `core/requirements.txt` and `tools/requirements-lock.txt` are checked for the direct dependency set used by current runtime/tools/plugins. PyTorch is intentionally validated as installer-managed rather than ordinary requirements-managed.
- These source/package checks do not claim live service health on another machine; Ollama/Redis/PostgreSQL/Tailscale/CUDA still require target-machine validation after installation.

Packaging-container note: the full historical `tools/test_maintenance.py` suite requires the managed runtime dependencies (including psycopg). The packaging container used for this ZIP did not have psycopg installed, so the prior recorded 17-test result is retained as historical evidence rather than falsely reported as rerun. The current refresh was revalidated with full-source syntax compilation, dual-path verbatim resolution tests, schema-2 identity verification for the unchanged verbatim `src/`, startup/console static invariants, and the dependency-manifest regression test.

## 2026-10-03 verification/archive maintenance checks

- 22 maintenance tests pass under dependency stubs in the packaging environment.
- Mandatory Redis preflight -> evidence -> Redis check-in regression passes with live pool refresh across decision rounds.
- Archive adapter regression passes for manifest, bounded member read, SHA-256, exact directory match, and same-size SHA mismatch detection.
- A Windows host smoke-tested installed 7-Zip 24.06 with the exact list/stream switches used by the intermediary.
- All first-party plugin identities verify after resealing `file_read`.
- Python source compiles cleanly.
