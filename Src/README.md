### 0.55.8 review-retry checkpoint-handoff revision (2026-10-09)

- The N1 step-alignment retry now archives original request, primary task, step acceptance criteria, **unchanged N2-verified candidate**, full observed evidence, job metadata/working context, and previously completed-step progress in the existing PostgreSQL `task_context_slices` store.
- The existing `checkpoint_step()` records its archive pointer and first failure before the retry. The review reconstructs its inputs from the saved PostgreSQL slice and receives a compact, deterministic handoff (including progress and archive reference). No additional summarizer-model call or second Python agent is started.
- N1's actual accept/retry decision remains independent; after a second *processing* failure, pass-through retains the `n1_step_review_status=unavailable_after_2_attempts` marker. Failure to persist/read back the checkpoint is a checkpoint failure, **not** grounds for silently reporting N1 approval.
- This is a synchronous fresh N1 model invocation using a persisted/reloaded snapshot, not an independent crash-proof review-only queue consumer. A hard process crash during this retry still relies on the broader existing queue recovery path; that path is not modified here.
- `agents.n1.task_budget_review.num_predict` controls N1's progress-extension review (fallback 900); `agents.n1.step_review.num_predict` controls step alignment (default 6000, capped at 10000); the 900-token command classifier is a distinct hardcoded call. No existing runtime config budgets were modified for this revision.

## Norm 0.55.8 — N1 bounded-review recovery, queue-full visibility, and richer idle detection (2026-10-09)

- Based solely on the user-supplied 0.55.7 CLEAN portable source. The original compiled StegoSplit/Auth/Key binaries, their plugin manifests, and plugin catalog identities remain unchanged.
- N1 step alignment remains `think=true`; default generation is 6,000 tokens with a 10,000-token hard cap and 300-second timeout cap, even if a preserved local configuration requests 86,000 output tokens. These are output/time bounds, NOT a change to the Ollama context window.
- Only AFTER the original N2 structured step verifier accepts a candidate: N1 processing failure checkpoints the exact candidate and evidence in existing PostgreSQL task-step storage, retries N1 once, then passes through the original N2-verified candidate if N1 remains unavailable. This is a synchronous review restart following the durable checkpoint, not a second worker or process. The step verification record indicates N1 was unavailable; a real N1 rejection still returns specific repairs to N2.
- N1 final verification now requeues the SAME candidate for its one existing processing-failure retry instead of asking N2 to rewrite it merely to make the failed review easier. The pre-existing deterministic artifact-safety precondition for review-failure pass-through remains intact.
- `/queue-full` now merges GUI ingress and uncertain deliveries with an explicit worker/Redis + PostgreSQL read-only inventory (live/pending, parked retries, escalations, dead letters, running, failed and suppressed; flags resumability and partial probe failures). New endpoint: `/status/queue-full` on Norm's activity API.
- Idle/resource observation now uses three intervals and measures CPU, GPU compute and GPU memory-controller activity; tracked HTTP/model/worker/queue signals retain priority. Pure external resource activity retains its existing 30-second grace rule and suppression checks.
- Preserved the shared-model N1/N2 setup, first accepted answer, existing recovery framework, voice profile and all compiled plugins. No new Python verifier/agent, no installer changes.
- Tests: focused isolated source tests. NO live Windows/Ollama/PostgreSQL/Redis execution, no rebuilt Norm EXE or Installer EXE; do not describe this as live-validated. A sudden process crash during the synchronous N1 retry is handled by existing queue recovery, not a new N1-only replay-after-process-loss guarantee.

## Norm 0.55.7 — explicit N1/N2 invoke-source provenance (2026-10-08)

- Every typed work-request JSON includes `invoke_source` and `invoke_target`; only genuine operator-ingress prompts are attributed to `user`. Internal generated steps remain `n1`, `n2`, or `system`, regardless of quoted text.
- N2 step-result and final-candidate JSON are tagged `invoke_source=n2`, `invoke_target=n1`; final-candidate normalization retains the tags for durable checkpoint/resume.
- N2 plan self-reviews, plan submissions to N1, N1 review directions, targeted repairs, and N1 loop interventions include a small JSON attribution header. The original operator request remains a separately authoritative field, not overwritten by agent follow-ups.
- This is metadata/routing provenance only: existing N1/N2 review criteria, shared voice profile, context-injection lifecycle, first-accepted-answer preservation, and native Ollama roles remain intact.
- All compiled StegoSplit/Auth/Key plugin binaries remain unchanged, as does installer 1.6.12. Select the adjacent 0.55.7 source; the prebuilt installer EXE embeds an older fallback.

## Norm 0.55.6 — planning self-audit, shared N1/N2 voice and strict step review (2026-10-08)

- N2 reviews its first proposed plan for omitted original requirements, missing prerequisites, sequencing and verification weaknesses, redundancies, inefficient effort, and cases where splitting a step into bounded child-task candidates improves outcome quality.
- The self-review returns a structured `improve` verdict, issue list and a 0–1 confidence assessment. A `no improvement required` verdict is accepted by N2 only at **>= 0.50 confidence**. A lower score causes one additional scrutiny pass. After a second uncertain pass, N2 records the uncertainty for an independent verifier rather than falsely declaring the plan complete.
- Exactly one targeted plan rewrite is allowed if a concrete actionable deficiency is identified. Self-review cannot rewrite an already satisfactory plan for cosmetics. A split becomes explicit planned execution steps, not automatically spawned nested runtime subtasks.
- N1 independently checks every N2 step submitted for acceptance against the original user prompt, primary task request, current internal step, verification requirement and evidence. N1 sends material deficiencies back for targeted correction rather than rewriting satisfactory work.
- N1, the N2 worker, and structured planning calls read the same validated active voice profile as a style prior. The persona cannot override safety, the original task, evidence or strict JSON schemas.
- The prior 0.55.5 fix preserving first satisfactory answers and its structured verifier stay intact. N1 final review remains active.
- All prior CLEAN compiled StegoSplit/Auth/Key modules remain binary-identical. No plugin source or trusted plugin catalog is changed.
- Windows Installer 1.6.12 EXE is unchanged with its older embedded fallback; select the adjacent 0.55.6 portable ZIP, or rebuild from the included builder kit on Windows.

## Norm 0.55.6 — N2 plan self-review without automatic rewrites (2026-10-08)

- Restored one deliberate **N2 planning-stage self-audit** before the independent plan verifier, looking for missing prerequisites, redundant work, improper ordering, unbounded steps, avoidable tool/model costs, and user-constraint or verification gaps.
- Self-audit uses a strict structured yes/no verdict and up to four concrete issues. A clean plan stays unchanged; only a material identified issue permits one targeted refinement pass. Never regenerates a plan simply for style or polish.
- Existing independent plan verification/repair, structured step verification, N1 final approval, and 0.55.5 first-final-answer preservation remain intact. Failed self-audits do not bypass independent verification.
- The verified-plan audit record now includes `plan_self_review.status` (checked_no_change, targeted_revision, proposed_no_change, unavailable) and issues, preserving provenance without turning internal instructions into user messages.
- The CLEAN compiled StegoSplit/Auth/Key plugins and their source identities are unchanged.
- The installer EXE is still the prior prebuilt 1.6.12 executable with older embedded fallback; use the adjacent 0.55.6 source or build the installer kit on Windows.

## Norm 0.55.5 — Preserve first satisfactory N2 answer (2026-10-08)

- Based on the operator-supplied 0.55.3 CLEAN compiled source, incorporating all 0.55.4 voice/provenance fixes.
- Removed the **unconditional, free-form completion-review regeneration** after N2's first answer in both tool and no-tool paths. Previously, a second answer could silently replace the first before any structured verdict.
- Preserved the independent structured step verifier. On acceptance, the original answer is retained **verbatim**; on rejection with specific issues, the existing targeted correction flow remains available.
- Preserved the structured final verifier and N1's final acceptance/revision gate. N1 may still request corrections for identified defects.
- Preserved compiled StegoSplit modules and plugin catalog identities; no plugin source or metadata changed.
- The included Installer 1.6.12 Windows EXE is reused, not recompiled; it must select the adjacent 0.55.5 portable source instead of relying on its older embedded fallback.
- Local Python checks are not a live Windows/Ollama integration test.

## Norm 0.55.4 — provenance/voice fix rebased on CLEAN compiled release (2026-10-08)

- **Base:** operator-supplied Norm 0.55.3 CLEAN compiled portable-source and Installer 1.6.12 kit, **not** the earlier uncompiled StegoSplit source.
- **Reinstated prior 0.55.3 N2 fixes removed from CLEAN:** the active voice profile is a dedicated per-request system instruction (including thinking/review/tool rounds), and internal planning, N1 interventions, retries, and completion reviews are not misidentified as new user-role prompts. The separate original user request retains its user-role provenance.
- **Preserved:** Message 0.4.3, Auth 0.1.0, Key 0.2.0a1 compiled binaries and their exact plugin SHA identities, the help structure, the context-injection lifecycle and all other clean-release code/plugins.
- **Packaging:** release 0.55.4, source ZIP and checksum, Installer 1.6.12 builder kit updated to embed this source. The previously compiled Windows Installer 1.6.12 executable is not rebuilt in this environment; if supplied as a companion, its embedded fallback remains 0.55.3. When using that EXE, the adjacent 0.55.4 portable-source ZIP must be selected/verified.
- **Verification boundary:** source/code regression testing is not an end-to-end Windows/Ollama/Redis/PostgreSQL install. Optional image-based Auth is not an SSH/PAM/TLS authentication integration.

## Norm 0.55.4 clean compiled portable source

Compatible with Norm Installer 1.6.12, Windows Python 3.14 x64. Norm core is portable Python source that the installer compiles into norm.exe. Proprietary StegoSplit internals are precompiled native .pyd modules, accessed through readable plugin src/main.py adapters. StegoSplit Auth reuses Message's native V3 modules instead of duplicating them. Each plugin includes a developer README.md and a user-facing help.txt available with /help stegosplit-message, /help stegosplit-auth or /help stegosplit-key. Master and credential images are NEVER distributed in this package. Filename-derived passwords and hidden PNG file selection are convenience/obscurity only. Native SSH or a network-facing authentication endpoint are NOT provided.

# Norm 0.55.4 — Installer-ready portable source

Norm is a Windows local Ollama/Redis/PostgreSQL agent runtime with persistent task queues, operator controls, history maintenance, and native plugins. The core Norm code remains portable Python source; StegoSplit internals are compiled.

## Install or upgrade

Use the working **Norm Installer 1.6.12** with **Norm-0.55.3-portable-source.zip** alongside its matching `.sha256` file. The installer reads `package-manifest.json` (package schema 1, type `portable-source`), installs/manages the source tree, and builds `core/norm.exe` itself if compilation is selected. This ZIP is not a prebuilt Norm EXE. Package discovery and non-destructive installer validation pass; a complete live installation/upgrade has not been performed.

## Compiled StegoSplit plugins

| Plugin | Version | Native code | Public adapter |
|---|---|---|---|
| StegoSplit Message | 0.4.3 | 12 Windows CPython 3.14 x64 .pyd modules, including V3, paired identifiers, and 256-bit credentials | `plugins/stegosplit_message/src/main.py` |
| StegoSplit Auth | 0.1.0 | 11 Windows CPython 3.14 x64 .pyd modules for fixed-master signed client PNG authentication and local GUI/admin utilities | `plugins/stegosplit_auth/src/main.py` |
| StegoSplit Key | 0.2.0a1 | 3 Windows CPython 3.14 x64 .pyd modules | `plugins/stegosplit_key/src/main.py` |

All proprietary StegoSplit implementation modules are compiled. Their corresponding Python implementations are **not** included. The adapter files remain human-readable so Norm can discover its tool endpoint names, argument types, and return values. Each plugin has a public README and help.txt. The private developer source remains separately available and must not be committed to a public repository.

### How to use

**Message:** Create two lossless PNG shares from a cover image and a password. Both images and the password are required to decode. `create_unlock_pair` writes a randomized 256-bit application credential across the two PNGs; `unlock_image_pair` authenticates/recovers it in one call. `embed_identity_pair` and `unlock_identity_pair` handle paired SSH public-key / SHA-256 / MD5 identifier matching. These are not directly OpenSSH authentication.

**Auth:** A locally trusted administrator generates a private master PNG and issues individually signed client PNG bearer credentials. Present one client PNG to the server-side verification adapter to check it against the master, permissions, expiration, revocation, and optional password. It supports five failed attempts in five minutes followed by administrator unlock. Its TXT-filtered file chooser can accept a manually typed PNG filename. The plugin is disabled by default until locally configured; no network listener, remote upload endpoint, or OpenSSH authentication module is supplied.

**Key:** Existing paired-image key recovery, cover reconstruction, and password rotation remain available through the compiled key engine.

### Security and compatibility

Lossless PNG is required. Filename-derived passwords expose their secret to anyone who obtains the filename; a copy of an unprotected client PNG is a transferable bearer credential. The master issuer seed is stored in protected PNG metadata; its secrecy depends on local filesystem permissions. Never commit actual credential images, authentication SQLite databases, `.env` files, or issuer key material.

The 1.6.12 package validator accepts this ZIP, and the compiled StegoSplit adapter smoke tests and all 18 bundled plugin identity checks passed on KHzz. No full installer-upgrade test or adversarial cryptographic audit has been performed.

See `docs/README.md`, `docs/CURRENT_STATUS.md`, `docs/RELEASE_NOTES.md`, `SOURCE_PACKAGE.md`, and the individual plugin README/help files.
