# Norm 0.55.8 continuity r7 - terminal conclusion

Derived from the installed r6 source tree, staged separately from the running installation. Source-only release; does not rebuild core/norm.exe.

## Terminal scope
Only a multi-step last generated conclusion with is_final_candidate and generated_step_count greater than 1 is intercepted. Earlier planning and implementation are untouched. Disable with config/runtime.json terminal_conclusion.enabled=false.

N1 decomposes each completed execution step into 1-3 evidence-based checks. Redis stores a report card with PENDING/TRUE/FALSE/UNKNOWN. Each small N2 check sees only the relevant checkpoint and bounded evidence; no mutation tool execution is performed. Calls are scheduled through a bounded local executor, with inference serialized for the shared model. This is not a separate Redis Streams consumer cluster.

When all assertions are TRUE, N1 outlines independent sections and small N2 calls draft and check one paragraph each. Accepted paragraphs persist in Redis and Python assembles the final user reply. The original task ID remains unchanged.

When at least one assertion is FALSE, N1 produces bounded corrective work as a successor task referencing original completed steps and the Redis report card. Original task status is passed_to, and new execution only covers remaining work. UNKNOWN-only results do not create corrective work or count as success. An explicit retry_terminal_verification flag can reopen UNKNOWN checks on the same task.

Redis namespaces: norm:terminal:v1:<task_id>:checks, :meta, :paragraphs. Retention 30 days. Max check count default 24, capped 32; verification deadline one hour, executor max 2 workers, shared inference serialized.

## Integrity and limitations
- The original planning and ordinary N2 execution stages are not redesigned.
- The final paragraph must satisfy existing deterministic artifact checks.
- The old terminal clean-up is bypassed in this branch to retain handoff evidence and avoid unsafe soft deletion and extra large model calls.
- Code was syntax checked and covered by isolated mock tests. NOT end-to-end validated with live Redis, PostgreSQL, or Ollama. Race, crash-interleaving and database integration testing are necessary before live deployment.
- No installed secrets, state, logs, SSH keys, Python environment, or compiled Norm.exe in this source package. Compiled StegoSplit modules retained unchanged.

Optional runtime.json entry: terminal_conclusion: enabled true, verify_workers 2, max_checks 24, deadline_seconds 3600, report_ttl_seconds 2592000. Defaults operate without a config edit.

Run Windows regression tests with PYTHONUTF8=1 to avoid the original CP1252 decoding error in Unicode source files.
