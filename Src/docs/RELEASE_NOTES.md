## Norm 0.55.8r10c - explicit TaskPlan user origin (2026-10-10)

- Finalizes active-step-first JSON and chat handoffs from r10b.
- Fixes child TaskPlan serialization: original_request can contain the scoped
  child objective, while original_user_prompt must contain the verbatim root
  user-authored prompt. Never falsely label the child's instruction as the user
  origin, and never infer missing original intent from a generated instruction.
- TaskPlan remains an existing frozen dataclass. Root, recovery and child
  constructors set the separate original_user_prompt attribute; old dataclass
  constructors remain compatible but missing originals require durable recovery
  before execution.
- N1 checks current assigned work, its supporting evidence, and constraints in
  the originating user request. Original constraints prevail over contradictory
  generated step wording; the original is not a fresh task at step 7/7.
- The live C:\Norm installation is untouched. Python regression tests pass,
  but actual model/tool execution and installer deployment are not smoke-tested.

## Norm 0.55.8r10b - active-step-first handoffs (2026-10-10)

- Fixes N2 executing the original user's root prompt again during later steps
  (e.g. step 7/7), overwriting or repeating verified earlier work.
- All N2 JSON handoffs carry original_user_prompt for authentic user intent,
  task_user_prompt for task scope, and the CURRENT active assignment separately.
- N2's system + user-role chat messages place CURRENT step execution first and
  preserve the complete original user prompt as a clearly bracketed reference.
  The original is never sent alone as the user turn during an internal step.
- N1 reviews actual current-step results against observed evidence and the
  original user's constraints. The original may override conflicting generated
  step constraints but does not supersede the active work assignment or force a
  root-task restart.
- TaskPlan and recovery handoff JSON retain original_user_prompt; final candidate
  normalization no longer discards it. Legacy queued JSON is repaired using
  authentic PostgreSQL root-task state when possible.
- r10a PromptJob data-class field and Redis serialization remains compatible.
- Regression suites now assert active step-first execution rather than legacy
  bare-root-prompt user-role messages.
- No live files were overwritten. Full live model / tool orchestration testing
  and EXE recompilation have not been performed.

## Norm 0.55.8r10a - original prompt handoff preservation (2026-10-10)

- Source-only hotfix after r10. Original r10 archive remains untouched.
- TaskPlan, TaskStep, StepResult and PromptJob already are dataclasses.
- Adds a dedicated PromptJob.original_user_prompt field. Existing metadata and
  legacy Redis records remain compatible. Serialization retains both views and
  rejects conflicting prompt values before enqueuing.
- Resolves the immutable root-task original_request from PostgreSQL when a child
  inherits missing or misleading original prompt metadata. Preserves the current
  scoped task assignment separately.
- Applies restoration before tool-model slices, child creation, and recovery
  handoff construction. Never uses a generated internal instruction as a user
  message when the original root prompt is unavailable.
- If neither existing nor durable root context contains an authentic prompt,
  fails explicitly with a recoverable missing-context error; cannot fabricate
  a lost user message.
- No new task controller, queue, database, or architectural rewrite.
- This hotfix is regression-tested with mocks; full live N1/N2/Redis/Postgres
  crash/restart and installer integration still require deployment testing.

## Norm 0.55.8r10 - portable-source recovery and live plugin preservation (2026-10-10)

- Based on intact r9 N2-restored portable ZIP. No live C:\Norm code was overwritten.
- Includes the eight existing C:\Norm\objects contract files/docs/tests; narrowly wires typed
  recovery-analysis validation and PASSED_TO handoff preconditions without changing task IDs,
  PostgreSQL/Redis ownership, the existing recovery coordinator or model selection.
- Recovery-analysis child final output stays scoped JSON (no generic report wrapping).
  N1 reviews the child assignment/result and evidence, not the unfinished root deliverable.
- Carries forward only two changed live web-browser Python files:
  plugins/web_browser/src/_bridge.py and src/web_runtime/browser.py. Both repair module
  search paths so subprocess and in-process browser calls can import norm_runtime.settings.
- Existing live vision_parse/file_read PDF plugin source files exactly match r9; the
  existing PDF raster/text coverage code is retained, not replaced with unrelated live state.
- Context injections retire on verifier-confirmed satisfaction by default with no popup;
  opt out using /inject-context -persist, -forever, or -no_expire (also -- variants).
  Legacy persisted records are not silently changed by the migration.
- Restores console /failed [limit], /resume-failed <selector>, /flush-failed;
  all use existing activity API/durable-worker controls, no new task system.
- Staged-source unit tests only. No actual Windows EXE rebuild or live model, Redis,
  PostgreSQL, GUI, installer or web navigation smoke test was performed.

# Norm 0.55.8r9 — restore native N2 conclusion and N1 final acceptance

**Source release (not a precompiled EXE), based on the user-supplied 0.55.8r8 ZIP.**
Celery workers and the DB5 terminal pipeline have been removed. All AI-generated
user-facing output uses the normal N2 worker with its active voice, native tools,
Nabler, context and N1 tool gate. N2 self-checks, submits the final candidate,
and N1 must explicitly accept; unavailable or rejected N1 review cannot be
silently substituted with an unrestricted model call or an automatic acceptance.

N2 execution keeps the unchanged original request in the Ollama `user` role as
*intent/constraints reference*, never as a second instruction to restart the
original task. Only the currently approved N2 step is executable.

A genuinely terminal failure retains SOS, Redis/PG checkpoints and the prior
history, then attempts a **`task_kind=recovery`** successor with a stable new task
ID and parent pointer. Recovery proceeds through N2's check planning, N1
step review, N2 evidence verification, N1 review, N2 remaining-work planning,
execution, and a fresh N2→N1 final. Compact task metadata is carried forward;
full archived evidence is fetched selectively. A recoverable normal round yield
continues on the existing ID, without manufacturing a recovery task.

Celery for approved CPU-side disk/DB tools is deferred in
`docs/FUTURE_IMPLEMENTATION_NOTES.md`. The original compiled StegoSplit
modules are unchanged. Tests are offline and cannot prove Windows/Redis/Ollama
integration until installed and smoke-tested. No live `C:\Norm` was modified.

---

## Norm 0.55.8r8: DB5 Celery terminal conclusion

Five independent Windows Celery workers handle atomic read-only file checks and short paragraph jobs. Redis DB5 holds broker, result and report card state. Installer-owned norm.exe build, source zip only. See TERMINAL_CONCLUSION_R8.md.

## Norm 0.55.8 continuity r7 - terminal only

Redis checkpointed final-step verification; N1 repair handoff; short independently checked report paragraphs. No changes to earlier planned execution steps. Source-only, mock-tested. See TERMINAL_CONCLUSION_R7.md.

## Norm 0.55.8 continuity r6 — preserve original user role and execute the active step (2026-10-09)

- **Cumulative source-only update built directly from r5.** Corrected a r4/r5 regression in which generated N2 step/child chat calls contained only `system` messages. Ollama's `norm` chat template rejected them with HTTP 500 `no user query found in messages`; automatic retries repeated the invalid envelope.
- Every normal tool-enabled N2 execution slice now retains the **unchanged original operator request** as the real Ollama `user` message. This is the original task's continuing purpose, constraints, and acceptance criteria. It is **not** a new request to plan again. Historical queued steps with missing separate prompt metadata can recover the original from the existing `AUTHORITATIVE PRIMARY TASK REQUEST` field in the stored step text; if the original cannot be located, fail explicitly rather than misattributing a synthetic runtime command to the user.
- **Distinct phase contracts:** planning in `conversation_service` generates and N1-verifies bounded actionable steps but does not perform them; execution in `prompt_worker` must actively **prosecute the current assigned step** using its verification requirement, continuation state, task working memory, and completed PostgreSQL prior steps. Worker execution must not restart the original assignment, recreate the plan, repeat completed work without new invalidating evidence, or silently execute later steps. Final/conclusion steps synthesize established work, not start over. The internal step/correction remains identified as **Norm-generated**, never an operator-authored command.
- Restored the original `user` message **without undoing the r4 PostgreSQL completed-step memory and compact-resume corrections**. N1 tool gating, plan verification, step reviews, final reviews, 38,000/74,000 enabled-tool budgets, 16,000 no-tools budget, 87,400 final repair, and the r5 Redis/Markdown truncation fallback remain unchanged.
- Regression tests exercise original-user-role preservation on planned and direct turns, active-step boundaries, conclusion synthesis, legacy queued metadata, failure on truly missing original prompt, shared voice, and continuity/overflow behavior. **Offline only**; actual Windows/Ollama/Redis/PostgreSQL execution is not verified by the tests.

## Norm 0.55.8 continuity r5 — output budgets and lossless final-report overflow (2026-10-09)

- Cumulative source-only update based on **0.55.8 continuity r4**. The main **tool-enabled N2** default output budgets are now **38,000 tokens per ordinary model turn** and **74,000 for long-form/conclusion turns**; existing explicit per-job `num_predict` overrides still win. N2 **without tools remains at 16,000**. Final-answer correction and N1-reviewability repair now request **87,400 tokens**. The independently configured N1 final-verdict token budget and general Ollama helper defaults are unchanged.
- An Ollama output-length stop during tool-enabled N2 work retains the existing lossless PostgreSQL segment/thinking checkpoint and additionally writes the actual generated portion to a **24-hour Redis working key**, with a bounded beginning-of-output preview and Redis locator in the continuation checkpoint. N2 without tools also preserves truncated output in Redis and yields rather than treating a partial generation as a completed answer. This does **not** manufacture any text the model never generated.
- A final/conclusion report longer than **16,000 characters** (configurable at `output_overflow.final_reply_max_chars`) is first preserved unchanged as a new Markdown file under the runtime's configured **state_root/reports/final-overflow**. The full content is independently kept in a 24-hour Redis working copy. N2 makes **up to three** bounded attempts to condense that copy; if none fits the configured target, it submits the shortest *complete* candidate plus a filesystem link to the original. If all attempts fail to produce a complete answer, it supplies a short pointer to the untouched original. Ordinary N2/N1 verification still applies to the submitted reply.
- When final-candidate repair or N1-reviewability repair hits the requested generation limit even after continuation attempts, preserve the original candidate as a Markdown report and use the same Redis/condensation fallback instead of converting the formatting failure directly into task failure. Truncated N1 final-verdict JSON is stored in Redis for diagnosis but remains a **processing failure, never an invented acceptance**.
- Redis output keys are short-lived; original user-facing Markdown files persist in state storage. The `file://` link refers to the original file **on the Norm host**, not to a publicly hosted URL. Installations must have write permission to the configured state_root. These changes do not add automatic child-task generation on normal working-turn output limits; that remains deferred.
- No changes to Ollama context length, context chunking, step/round budgets, N1/N2 tool-call policy, normal task verification rules, compiled/private plugins, installer, or original 0.55.8 r4 continuity fixes. **Source-only; 21 focused offline tests passed. Windows/Ollama/Redis/PostgreSQL integration still needs a live test.**

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

### N1 alignment and shared voice refinement (0.55.6)

- N1 now independently reviews N2 outputs at every step-acceptance boundary against the authoritative user request, scoped task, internal step requirements and recorded evidence; material defects return an actionable correction to N2. No automatic style-based rewriting.
- The N1 final gate explicitly checks original task fidelity as well as completed-step requirements. Model failures do not count as successful per-step review.
- Both agents use the exact same validated active voice-profile source; N1 receives it in system context on model-review calls. This changes linguistic style, never verification criteria or JSON contracts.
- N2 planning self-audit remains independent of N1 verification, with bounded issue-based tightening only.

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

## 0.55.3 clean compiled release - October 8, 2026

- StegoSplit Message 0.4.3, Auth 0.1.0 and Key 0.2.0a1 algorithms compiled as CPython 3.14 Windows x64 native .pyd modules; entrypoint adapters remain readable.
- Shared V3 backend stored only in stegosplit_message; stegosplit_auth explicitly imports that plugin. No duplicated V3 engine.
- Recomputed exact plugin content SHA-256 values; restored installer 1.6.12-compatible manifest/build script and updated per-plugin Norm README and user-facing /help text.
- No private implementation Python, build object files, generated code, runtime secrets, master/issued credential PNGs, database, logs or state in distribution.
- Custom image authorization is NOT an OpenSSH integration; no network upload handler is bundled.

## 0.55.3 compiled portable-source packaging — 2026-10-08

- Installer 1.6.12-compatible portable-source manifest and build script retained.
- StegoSplit Message 0.4.3, Auth 0.1.0, and Key 0.2.0a1 carry CPython 3.14 Windows x64 compiled native engines and readable main.py adapters.
- Plugin SHA-256 identity values recalculated from exact compiled src trees; generated registry excluded.
- Implementation sources excluded from compiled distribution. Proprietary rebuild sources remain separate.
- Image authentication is a bearer-credential verifier, not a native OpenSSH module; TXT-dialog path UI is optional.

## 0.55.3 development-stage source archive (historical; superseded by compiled package)

- Packaging correction: restored required `build_script: tools/build_norm.py`, verified against the exact supplied 1.6.12 installer, and removed stale generated plugin registry. Existing installer unchanged.
- Adds `plugins/stegosplit_auth` v0.1.0 as a separate signed, revocable fixed-master/many-client PNG credential subsystem. Existing `stegosplit_message` v0.4.3 V3 two-share messaging, identity, and 256-bit paired-credential functions remain unchanged.
- Authentication uses Ed25519 issuer signing, a private server master PNG, individually signed differential client PNGs, durable SQLite revocation/lockout, optional filename-based passwords, and a TXT-filtered PySide6 selector allowing typed PNG paths.
- Compiled Windows CPython 3.14 `.pyd` for authentication was built and smoke-tested on KHzz as a separate compiled artifact; this GitHub SOURCE zip includes the matching private Python source and repeatable Windows build scripts, not that new native binary. Original V3 `.pyd` modules remain bundled.
- Not an OpenSSH authentication plugin, server-side HTTP upload API, or TLS gateway. Client PNGs are copyable bearer credentials. Master PNG embeds the Ed25519 issuer seed in unencrypted metadata; protect it with ACLs and never commit generated credentials or SQLite state.
- Corrected 0.55.1 stale headings, source manifest plugin version, plugin index, and package version; omitted generated plugin registry and historical backup snapshot from repo source archive.

## StegoSplit plugin 0.4.3 two-PNG credential (Norm 0.55.2 source patch) — 2026-10-08

- `create_unlock_pair` generates a random 256-bit application credential and carries it in two StegoSplit V3 PNG shares; no SSH, hash, .pub, or .txt input required.
- `unlock_image_pair` performs paired V3 extraction, authenticated decoding and optional trusted SHA-256 enrollment comparison in one call. Raw key is opt-in (`reveal_key=True`) to avoid accidental secret disclosure through Norm logs. Matching a particular enrolled pair still requires a trusted fingerprint in the consuming application.
- Preserved 0.4.2 paired identifier tools, 0.4.1 legacy single-image read-only compatibility, compiled 0.4.0 engine and all placement/decoy algorithms without change.

## StegoSplit plugin 0.4.2 paired identifier patch for Norm 0.55.2

- Replaced new single-image SSH/SHA-256 identity writes with a V3-encrypted **two-share** identifier envelope supporting SSH public keys, SHA-256 and MD5. All share matching, decrypting, decoding and optional independent expected-value comparison are exposed through one `unlock_identity_pair()` operation.
- Kept historical 0.4.1 single-PNG reads as an explicitly read-only legacy API; stopped exposing single-image creation actions. Unchanged V3 compiled 0.4.0 engine; no change to scatter/decoys/pixel format.
- Added Python integration and wrong-key/wrong-value/legacy compatibility regression tests. Windows compiled .pyd test deferred to an actual Windows CPython 3.14 runtime.

## 0.55.2 — 2026-10-08 — StegoSplit V3 packaged integration and public identifier shortcut

- Replaced bundled StegoSplit V2 `stegosplit_message` v0.3.0 source with the operator-provided native StegoSplit V3 0.4.0 distribution, retaining its Windows CPython 3.14 x64 `.pyd` binaries **exactly** and wrapping it as plugin 0.4.1.
- V3 encodes each paired-carrier 4-bit nibble using 16 color-zone vectors among 26 nonzero RGB -1/0/+1 combinations; optional zlib level-9 payload compression is independent.
- Added single-image **public** SSH key / SHA256 digest embed/read/verify native plugin tools. Default is a lossless RGB-LSB PNG carrier at 3 bits/pixel; optional overt PNG tEXt metadata. Neither mode constitutes network authentication on its own.
- Updated plugin identity hash, help.txt, source manifest, and added regression tests. Retains all 0.55.1 injection/voice/lesson/vision features. Windows-native paired-carrier runtime tests still required.

## 0.55.1 — 2026-10-08 — Verified context-injection retirement and stronger voice controls

- Added PostgreSQL migration for `task_context_injections` statuses, operator retirement flag, and audit metadata. Existing rows retain legacy behavior; new `/inject-context` entries expose a checked default checkbox or explicit `--once`/`--keep` flags. `/injections`, `/injection-retire` and `/injection-restore` manage current-task lifecycle directly.
- A completed step can auto-retire a specified injection only when the independent structured verifier accepts it, explicitly reports that injection ID fulfilled, and successful tool evidence exists. Worker prompts consult live active injection state at each call and stop replaying retired text, including during resumed jobs. The PostgreSQL audit record is preserved.
- Loaded the hash-validated active voice prompt into queued N2 worker generation. This is separate from direct conversation conditioning; N1 tool gatekeeping and verification do not become style-conditioned.
- Upgraded voice_profile to 0.4.2: distinctive dialect/sociolect synthesis instructions, a separate `style_override.txt` preserved through corpus rebuilds, `/voice-tune NAME --preset cockney`, and `/voice-test NAME "QUESTION"` for neutral versus conditioned local Ollama comparisons. No model weights change.
- Preserved cumulative 0.55.0 lesson-model/code-memory, OCR/vision, installer catalog, help/slash command and runway changes. No installer redesign or code-memory weight training was introduced.
- Pending: Windows/PostgreSQL/Ollama live integration, general duplicate-mutation guard not limited to context injections, lesson-model weight promotion, and standalone Tesseract executable installation.

## 0.55.0 — 2026-10-08 — Cumulative source release with lesson model, voice and OCR/vision tools

- Promoted the combined 0.54.3 source-patch line to package version 0.55.0; preserved historical 0.54.3 release notes below. Installer baseline remains 2.0.0 and must be built/run separately.
- Includes built-in `lesson_model` v0.3.0a1 for operator-reviewed reusable lessons and source-ZIP code memory, PostgreSQL project summaries, README exports, and standalone interactive topology maps; actual model-weight training and automatic promotion remain disabled.
- Includes direct voice profile management, activation refresh, plugin-owned help manuals, compact `/help`, `/help plugins` **and `/help plug-ins`**, `/help voice-profiler`, and `/help vision-parse`.
- Includes central OCR-first `vision_parse` for PDFs/images, grayscale ink coverage checks, selective vision crops and suspicious-excerpt rechecks, with direct `/vision-*` console commands. Tesseract's host executable remains a separate optional dependency.
- Retains resource-only runway yielding and suppression protections carried from 0.54.3.
- Not yet included: automatic retirement of fulfilled task-context injections; verified propagation of active voice-profile instructions into every queued N2 worker. Those are separate follow-up issues.

## 0.54.3 — 2026-10-08 — Lesson/code-memory plugin integration candidate

- Added built-in schema-2 `lesson_model` v0.3.0a1 with explicit operator approval for reusable lessons; candidate creation alone never approves a lesson. Training and automatic `norm:latest` promotion remain disabled.
- Added wholesale SHA-verified source-ZIP retention, bounded runtime-priority code walking, PostgreSQL raw project/version memory, separate human README generation, and canonical CodeGraph + standalone interactive topology output. Original input ZIPs remain untouched.
- Added `/help lesson-model`, plugin settings, package-manifest registration, and runtime guidance for lesson/code-memory use.

## 0.54.3 — 2026-10-08 — Direct PDF/image reader slash controls (source patch)

- Added direct Norm Prompt `/vision-parse`, `/vision-read`, `/vision-text`, `/vision-force`, `/vision-recheck`, and `/vision-status` operator commands; PDF/image reads bypass the queued N2 tasks and use existing verified `vision_parse` plugin code.
- Added the plugin-owned `plugins/vision_parse/help.txt` command reference, `/help vision-parse` and aliases; kept plain `/help` compact.
- Explicit native/OCR/vision mode, page/window selection, model focus, suspicious-text recheck and bounded results; authorized file roots and single active read per console are enforced.

## 0.54.3 OCR-first PDF/image reader overlay (2026-10-08, source patch)

- Extended shared `vision_parse` to PNG, JPEG, WebP, BMP, TIFF and scanned-PDF OCR-first input. High-confidence OCR word rectangles are masked with transparent alpha before computing uncovered ink and targeting bounded vision crops; model inputs white-composite masked areas for predictable cross-backend behavior.
- Added explicit `suspect_excerpt` OCR recheck, line geometry, OCR word/coverage/region diagnostics and forced text/vision overrides. Tesseract binary remains an optional, separately installed local dependency; pinned `pytesseract` in Python requirements.
- Added raster/OCR integration tests and retained page-level grayscale coverage validation. Does not modify the generic image-analysis tool or the voice-profile implementation.

## 0.54.3 — 2026-10-08 — Shared PDF grayscale coverage gate (source patch)

- `vision_parse` hybrid mode now measures grayscale raster ink against native character footprints, including image-area detection, and calls Ollama vision only on insufficient or suspect coverage.
- Per-page metrics and reasons are emitted; `text`/`vision` overrides and process-local caching remain supported.
- Voice-profile `auto` extraction delegates to the shared PDF reader instead of owning a separate text-length heuristic.
- New targeted PDF coverage regression tests.

0.54.3 — 2026-10-07 — Nabler structured conclusion and verification reporting

- Prompt ingress dispatch now waits a maximum of 30 seconds when the sole busy signal is host CPU/GPU `resource_activity`; measured Norm requests, active generations, queued work, and failed status probes remain hard admission gates. The grace period is configurable through `console_queue.resource_activity_grace_seconds`.
- Pre-task suppression now stays effective through the resource-idle runway, thread setup, and the final pre-HTTP admission check. `/flush-suppressed` removes operator-visible delivery records without clearing cancellation tombstones that a dispatcher may still hold in memory. Added an isolated regression for resource timeout, tracked work, cancellation, and suppression flush.
- Added built-in schema-2 `plugins/nabler` v0.2.0 with verified source SHA-256 and native data-only `render_report` tool for single-call tables/checklists. The native tool never executes arbitrary source supplied in its arguments.
- Merged the two experimental Python authoring paths into one plugin: deferred lambda/callable checks and trusted local multiline `Table.add_row(script=..., context=...)` blocks. Inline script code executes once per row on rendering, requires assigning `result`, and is not a sandbox. The duplicate `nabler_inline` plugin is not packaged.
- Added `tabulate==0.9.0` to direct and locked dependencies, plugin auto-discovery/manifest registration, default N2 conclusion preference, and source-level conclusion directive.
- Included Nabler regression tests and standalone-script `.pth` registration helper. Existing user installations require preservation/migration of their config; this source release does not rewrite an already-running installation.
- Left the pinned 0.54.2g managed optional-plugin recovery package unchanged. SSE client source and unrelated Norm 0.54.2i behavior remain unchanged.

0.54.2i — 2026-10-07 — Progress-leased task budgets, resumable failures, and real compact headroom resume

- Kept existing per-turn tool behavior and task-round accounting. N1 now reviews durable forward progress at round 96 and may grant 36 additional rounds per review, repeatedly, up to an absolute 384-round cap. Dense useful tool-call turns are allowed; churn does not earn another lease.
- Task-round safety stops now preserve a durable queue/checkpoint snapshot. Added `/failed`, `/resume-failed N|TASK-ID|PREFIX`, and `/flush-failed`. Manual resume resets the active round/budget window while retaining lifetime failure/resume telemetry.
- `/flush-failed` asks Norm for one generalized failure-prevention/recovery lesson before deleting failed task execution/history rows, live Redis/task state, and stale background-memory snapshots. The compact lesson survives as a runtime maintenance note.
- Fixed context-headroom continuation so a requeued slice does not rebuild the same exhausted prompt from full working memory, completed-step history, and background memory. The fresh slice uses N1's compact continuation plus normal task identity/instruction fields; PostgreSQL remains lossless. Legacy 0.54.2h headroom checkpoints are recognized from their saved yield/headroom markers, so already-parked h tasks compact correctly on first resume after upgrade.
- N1 continuation distillation now treats an assistant tool-call proposal with no following tool result as unexecuted, preventing interrupted proposals from being recorded as completed side effects.
- Retains 0.54.2h's planner-only one-primary-output-file guidance and the 0.54.2g managed-plugin/SSE fixes.

0.54.2h — 2026-10-07 — Single-primary-file planner discipline

- Planner guidance now tells Norm that each executable file-creation/file-editing step should mutate one primary output file, while allowing unlimited read-only source/context inspection.
- Semantically related target files should be planned as separate executable steps; a complete primary file may still be emitted in one guarded verbatim write.
- No runtime mutation guard, plan-verifier rule, or plugin behavior was changed in this patch.
- Retains the 0.54.2g managed-plugin payload repair and SSE/runtime fixes unchanged; managed-plugin fallback remains pinned to the known-good 0.54.2g package because plugin payloads are unchanged.

0.54.2g — 2026-10-07 — Restore managed plugin payloads

- Restored `plugins/vision_parse`, `plugins/web_browser`, `plugins/jan`, `plugins/vane`, and `plugins/opencode`, which were present in the known-installing 0.54.2c package but accidentally omitted from both 0.54.2 and 0.54.2f.
- Verified every restored Schema-2 `src/` tree against the exact SHA-256 already declared in 0.54.2f's `package-manifest.json`; no plugin source was changed during the repair.
- Repointed the managed-plugin public package identity to 0.54.2g so future repair/fresh-install fallback no longer targets the incomplete 0.54.2 archive.
- Retained all 0.54.2f runtime fixes: multi-viewer Replies/Runtime fan-out, direct byte-cursor streamed-evidence marking, and source-package exclusion of `core/norm.exe`.
- Retained the final 0.54.2 N1 action-boundary protected-literal/Bird behavior and oversize-recovery corrections unchanged.

0.54.2f — 2026-10-07 — Viewer fan-out and byte-cursor verifier fix

- Kept Norm Prompt as the only singleton operator console; removed named-mutex singleton enforcement from read-only Norm Replies and Norm Runtime viewers so invisible orphan viewer processes cannot block replacement windows.
- Confirmed Replies uses independent Redis XREAD cursors and Runtime uses independent ActivityHub/SSE subscriber queues, so multiple viewers can safely coexist without consuming each other's events.
- Marked direct text read_file calls with an explicit start_byte cursor as streamed evidence. N1 now uses deterministic path/source-fingerprint/cursor metadata for those reads instead of invoking verification_value on the raw chunk, preventing Laguna output-limit failures on byte-chunk reads.
- Fixed portable-source packaging to omit core/norm.exe, matching the package manifest and source-package documentation.
- Reconstructed this patch from the known-good 0.54.2 action-boundary/Bird baseline after testing exposed that an older 0.54.2b source tree had been installed.

0.54.2 — 2026-10-07 — Recovery scope and action-boundary protected-literal guard

- Removed the oversize-recovery fallback that converted every unnumbered task into synthetic `scope_items=[1]`. Ordinary unscoped work now stays unscoped, while real single-item scopes retain the existing maximum of four semantic subchecks.
- Recovery-analysis child instructions now show the actual parent scope and state the four-subcheck limit only when the parent is genuinely scoped to one numbered item.
- The existing upstream protected-literal transformation is unchanged. N1 no longer uses `observe_n2_turn()` for this policy; that observer is reserved for genuine reasoning/tool loops.
- Protected-literal checks now occur only at the concrete tool-action boundary. The first denied action returns only `The Bird says No. The Worm won't Go.`; a later retry may receive a short goal-preserving alternative. Direct PostgreSQL pool/query/execute tools remain allowed.
- The guard uses a cheap local prefilter before its semantic N1 judgment, and its counters/history are process-local RAM only; they are never restored from Redis or PostgreSQL after restart.

0.54.2b — 2026-10-06 — Redis verification-pool evidence replay

- Made the existing Redis live verification pool the sole hot reusable-evidence cache; removed the redundant process-local raw-result cache.
- N1 now judges evidence sufficiency rather than using age/duplicate-call rules: source volatility, requested freshness, known mutations, scope, contradictions, and completeness determine whether prior observations are sufficient.
- A reuse decision now returns the actual selected Redis replay payload to N2. N2 is no longer told to "use prior evidence" that may have been evicted from its active context.
- Information-only repeats bypass the generic loop-reset blocker. Repeated non-information/reasoning paths retain the existing loop protections.
- Added private, bounded replay payloads in a companion Redis hash plus scoped observation IDs in the verification metadata. N1 sees only bounded metadata/canonical values during its decision; payloads are fetched afterward.
- Preserved the one-large-chunk-per-N2-turn rule. Chunk reads may replay exactly one previously observed raw chunk when N1 judges it sufficient; otherwise the source is read fresh.
- Kept PostgreSQL as durable validation/history/checkpoint storage; Redis replay payloads remain hot evidence and are not copied into long-term validation history.
- Added regression coverage for Redis replay semantics, same-value refresh/change invalidation, multi-observation sufficiency, mutation invalidation, and chunk replay after a same-step requeue.

0.54.2a — 2026-10-06 — Adaptive single-chunk evidence context

- Added runtime-owned context budgeting for large default text `read_file` results. Before the read executes, Norm estimates current N2 system/message/tool-schema usage, reserves the current N2 output allowance plus a safety margin, and targets 20% of the remaining usable input budget for one raw evidence chunk.
- Enforced one raw evidence chunk per N2 turn. Large reads return a source-fingerprint-locked stream with numbered pieces and seam markers (`{0:1}`, `{n:n+1}`, `{N:0}`); N2 may request any one piece by `stream_id` + `piece`, including non-sequential jumps.
- Added `retain` to streamed reads. Once N2 consumes a chunk and emits its next tool call, useful compact facts are retained with source/piece provenance while the prior raw chunk is evicted from active model history. Full raw tool evidence remains durable outside the model context.
- Bounded retained per-piece facts against the configured compact-state partition so accumulated summaries cannot silently consume the raw-evidence slot.
- Stream reconstruction is byte-addressed and source-fingerprint checked. If the file changes mid-stream, the stream is invalidated instead of stitching incompatible pieces together.
- Chunked `read_file` calls still check in with N1/Redis, but Redis canonical values are never substituted for requested raw bytes. Streamed-read check-in is deterministic source-fingerprint metadata, avoiding the former N1 `verification_value` model pass over huge file contents.
- Added `config/runtime.json:context_budget` for the adaptive fraction, safety reserve, compact-state fraction, token/byte estimation, and retention caps. Existing live runtime values continue to migrate through Installer 1.6.12's snapshot-and-merge JSON path.

- Added a 5% N2 context-headroom checkpoint threshold driven by Ollama's returned `prompt_eval_count` plus only the newly visible assistant payload; hidden thinking tokens are not treated as persistent next-turn context, and Norm does not re-tokenize/rescan the whole prompt every turn.
- At the headroom threshold, the recent execution slice is archived losslessly to PostgreSQL `norm_runtime.task_context_slices`. N1 then reads the durable copy and distills only task-applicable continuation state (completed work, verified facts, settled decisions, do-not-repeat items, open work, next action, and evidence cursor).
- The existing checkpoint/yield/requeue lifecycle remains authoritative. The distilled checkpoint is placed in `job.context` for the same task/step; it is explicitly an internal continuation and never becomes a synthetic user prompt.
- Active chunk-stream cursor/state is persisted in existing requeue metadata, so a checkpoint can resume the same source-fingerprint-locked piece map without replaying evicted raw chunks.

0.54.1a — 2026-10-06 — Runtime profiles and read-only Codex reasoning

- Added `config/profiles.json` with distinct user, N1, and N2 persona/voice slots. Direct persona is operator-authored; corpus-derived persona/voice profiles are supplemental and remain runtime-owned across model switches.
- Extended `voice_profile` so one PDF/text corpus build produces both `voice_profile.json`/`voice_prompt.txt` and `persona_profile.json`/`persona_prompt.txt`, with source material treated as untrusted data.
- Added `voice_profile.build_from_chat_history()` for the user voice slot. It learns only from durable user-role chat messages, excludes assistant/system text, refreshes rather than endlessly appending prior chat-derived chunks, and never infers or overwrites the operator-authored user persona.
- Added first-party `codex_reasoner` integration. Codex CLI runs ephemeral/read-only in an empty temporary directory, receives only evidence Norm explicitly supplies, and returns structured reasoning; Norm remains the sole execution agent.
- `profiles.json` is schema-managed installer config: the existing installer snapshots the complete config directory before synchronization and merges matching live JSON values into the new schema, so operator profile values survive upgrades with rollback copies.

0.54.0e — 2026-10-06 — Runtime-owned model policy, overflow recovery, and backup dedupe
- Moved model context authority into `config/runtime.json`. `config/settings.ini`, `config/norm.json`, and the Ollama `Modelfile` no longer own context size; ordinary models default to 65,536 tokens while the `norm` model family retains the existing 180,000-token override.
- Moved the former baked Norm `SYSTEM` role text out of the Modelfile and into `agents.n2.persistent_instructions`. Added a separate `agents.n1.persistent_instructions` set. Each agent's runtime-owned instruction set and context policy stay attached across `/switch-model`, model-switch probes, context-summary calls, maintenance fallback clients, and ordinary generate/chat calls.
- Exposed every N1 generation budget as a verbose configurable runtime profile: ingress, routing, final review, maintenance review, tool-loop reset, reasoning-loop judgment, Redis reuse decision, and verification-value extraction. Defaults reproduce the live pre-0.54.0e values exactly, including thinking flags, temperatures, and timeouts.
- Ollama `exceed_context_size_error` is now parsed as a structured context-overflow condition. Interactive work yields into Norm's existing bounded oversize-recovery decomposition instead of repeating the same oversized request through the generic retry loop.
- Memory-condensation continuation count is configurable as `memory.consolidation_max_output_segments` and defaults to 4 instead of the previous hard-coded 2, reducing false maintenance parks on valid long final merges.
- `plugins.ini` backups now live under `backups/plugins-ini` with the other runtime backups. Existing `config/backups/plugins-ini` files are migrated automatically. Exact duplicate backup contents are collapsed by SHA-256 and do not consume one of the 12 retention slots; unchanged plugin settings still create no backup at all.

0.54.0d — 2026-10-06 — Preserve Ollama HTTP error diagnostics
- Ollama HTTP 4xx/5xx failures now read and retain the response body instead of losing Ollama's diagnostic JSON when urllib raises HTTPError.
- Persistent runtime logging records endpoint, model, HTTP status/reason, content type, bounded/redacted response body, and structural request metadata (request bytes, message roles/count, tool names/count, thinking/stream flags, and generation options) without logging prompt or tool-result contents.
- The raised runtime error also carries the bounded/redacted Ollama response body so Norm recovery/troubleshooting can reason from the authoritative server error instead of guessing from `HTTP Error 400: Bad Request`.
- Response-body logging is capped at 16 KiB and marked when truncated. No tool-routing, retry-policy, model-selection, or filesystem-policy behavior changed in this diagnostic patch.

0.54.0c ? 2026-10-05 ? Single-model maintenance writer/verifier
- Maintenance condensation and maintenance verification now use the same active maintenance model client, matching the normal N1/N2 same-model pattern.
- The configured maintenance chain stays synchronized by role: Granite/Granite, then north-mini-code/north-mini-code on failure, then norm/norm only as the final fallback.
- Normal interactive N1/N2 model selection is unchanged.

0.54.0 Beta 2 ? 2026-10-05 ? Installer staging + control-order correction
- Moved N1 ingress review after the existing deterministic Python/control special-command path; handled slash/control work does not enter N1 ingress.
- Normal conversational work still receives N1 ingress review immediately before N2 classification/execution.
- Package version normalized to 0.54.0-beta2.

# Norm release notes

## 0.54.0 Beta — 2026-10-05 — Friendly maintenance-model resolution

- Promoted the 0.54 line to Beta and retained the full N1 ingress/tool/final-answer control loop plus subject-bound routing and N1-gated 7-day/21-day maintenance.
- `memory.condensation_model` and `memory.condensation_backup_model` may now use convenient names such as `granite` or `north-mini-code`; Norm resolves them against Ollama's installed-model inventory at the start of each maintenance pass.
- Resolution is deterministic: exact full tag → exact base name (preferring a unique `:latest`) → normalized starts-with → normalized substring → bounded fuzzy similarity.
- Ambiguous matches are never silently selected. An ambiguous/unmatched primary falls through to the one configured backup, then `norm`; blank fields still resolve to `norm`.
- If Ollama's model inventory cannot be queried, Norm preserves the configured names and lets the existing generation failover path decide availability instead of disabling maintenance.

## 0.54.1 — 2026-10-05 — Configurable maintenance condensation model

- Added runtime-selectable memory-condensation model settings: `memory.condensation_model` and one `memory.condensation_backup_model`.
- Blank primary or backup values resolve to `norm`; the effective chain is primary → configured backup → `norm`, with duplicates removed.
- Maintenance model settings are re-read at the start of every manual or scheduled condensation pass, so model changes do not require a rebuild.
- Model failure falls through to the next configured maintenance model for the remainder of that pass; explicit model-generation cancellation still stops immediately.
- The selector applies to weekly reductive, 21-day destructive, and manual memory-condensation jobs; N1 remains the independent review/gatekeeper model.

## 0.54.0 — 2026-10-05 — N1 control loop and subject-bound conversation routing

- Promoted N1 from mostly-transparent ingress/egress hooks to the full conversation boundary: N1 reviews the exact user turn before N2, continues to gate/reuse tool evidence, and now accepts/retries N2's final user-facing answer without rewriting it itself.
- Added configurable ingress/final N1 review budgets and 12-minute default timeouts alongside the existing configurable maintenance review budget. N1 processing failures are distinct from semantic RETRY decisions.
- Added N2 subject decomposition for mixed turns. N2 ranks/splits materially distinct subjects and proposes existing/new thread placement; N1 reviews that proposal before persistence.
- Raised the default existing-thread routing threshold from 0.58 to **0.68**. Sub-threshold matches cannot silently contaminate an existing thread; they create a better-named subject thread or require clarification.
- Added `message_threads.projected_content`: the authoritative user message remains intact in `messages.content` after existing ingress whitespace normalization, while each subject thread sees only the accepted relevant projection.
- Multi-subject assistant replies are projected back using only verbatim excerpts of the approved final answer; failed projection falls back to the primary thread instead of copying unrelated reply text across subjects.
- Durable memory extraction and summary refresh are performed per subject thread after mixed turns.
- Retains the 0.53.22b N1-gated 7-day reductive / 21-day destructive maintenance behavior and configurable maintenance review allowance/timeout.

## 0.53.22b — 2026-10-05 — Configurable N1 maintenance review budget

- N1 maintenance review output allowance is now runtime-configurable with `agents.n1.maintenance_num_predict`; default is 5,000 tokens instead of the prior hardcoded 500-token limit that frequently caused `ModelOutputTruncated` during maintenance judgments.
- N1 maintenance review wall-clock timeout is independently runtime-configurable with `agents.n1.maintenance_review_timeout_seconds`; default is 720 seconds (12 minutes). The override applies only to maintenance judgment calls and does not change N1's normal tool-gate timeout behavior.
- Effective N1 maintenance review allowance and timeout are logged at startup so the active values are observable. Changing either setting requires only editing `config/runtime.json` and restarting Norm; no rebuild is required.

## 0.53.22 — 2026-10-05 — N1-gated reductive/destructive memory maintenance

- Scheduled memory maintenance now uses independent clocks: a **7-day reductive pass** and a **21-day destructive pass**. When both are due, the destructive pass runs and also satisfies that week's reductive maintenance; the old alternating regular/full every seven days behavior is removed.
- Weekly/background condensation is explicitly reductive. N2 ranks information by future usefulness, preserves important/current state with more detail, and aggressively minimizes lower-value context instead of treating low importance by itself as permission to erase a distinct subject.
- N1 now has an explicit maintenance job: after every N2 condensation candidate, N1 judges whether to accept it or send N2 back for another rewrite. N2 remains the semantic editor/ranker; N1 is the critic/gatekeeper and does not perform the rewrite itself.
- Batch condensation uses up to four N2 attempts. A good N1-approved result at or below 1,800 characters stops early; otherwise N1 may continue retries toward the 1,800 ideal while 3,400 characters is the practical batch ceiling. Final bounded attempts keep the best semantically valid result instead of aborting maintenance solely for length.
- The long-cycle pass is destructive rather than merely 'deeper compression'. N2 may propose `destroy` for obsolete TODOs, superseded state, abandoned intermediate plans, stale failures with no reusable lesson, redundant chronology, and facts already implicit in the stable baseline. N1 must approve the destruction before the compact record is omitted.
- Destructive raw-task pruning uses a separate N1-approved path instead of weakening normal archive safety. Ordinary pruning still requires validated compact-history coverage; destructive deletion additionally requires the exact maintenance approval token and terminal task status.
- N1 maintenance judgments are logged and counted (`total`, `accept`, `retry`, `destructive_accept`) so a quiet gatekeeper is observable even when it approves work.
- Added focused N1/maintenance regressions for retry/accept behavior and N1-approved destructive discard; updated maintenance-control contract tests for the 1,800 ideal / 3,400 ceiling / 21-day destructive schedule.

## 0.53.21 — 2026-10-05 — manifest-driven plugin settings and bounded backups

- Added manifest-driven `config/plugins.ini`: every Schema-2 plugin carries a `settings` declaration in `plugin.json`; `PluginManager.refresh()` reconciles settings on startup and every hot-rescan. Existing declared values survive, missing/invalid values fall back to plugin defaults, and dangling keys/sections are pruned only after a backup is written.
- Added a standard per-plugin `enabled` setting. Jan/Vane/OpenCode default disabled; existing built-ins default enabled. Toggling a plugin setting takes effect on the next normal plugin refresh without rebuilding Norm.
- Shared model-provider defaults are declared identically by Jan/Vane/OpenCode and merged safely; conflicting defaults from two plugins are rejected instead of silently using last-writer-wins behavior. Remote/model endpoints default blank and disabled, so an install with no second PC configured behaves exactly like pre-remote-agent Norm.
- Installer 1.6.10 edits the same plugin settings sections (`model_provider`, `jan`, `vane`, `opencode`) instead of writing remote-agent fields into core `settings.ini`; it can read/migrate the short-lived 1.6.9 experimental `[model_provider]` / `[remote_agents]` core sections.
- Secrets remain exclusively in Norm's configured external `.env`; `plugins.ini` is non-secret derived/operator configuration.
- Backup retention is hard-capped at the newest 12 sets per backup class/location: plugin-settings snapshots, file-tool write backups, maintenance SQL safety dumps, source/full backup archives, and installer config snapshots. A `.zip` plus its `.sha256` companion counts as one backup set.
- Jan/Vane/OpenCode adapters advance to v1.0.2 for the per-plugin settings layout; source-tree identities are rehashed and verified.

## 0.53.20 — 2026-10-05 — remote agents, shared models, and reproducible optional plugins

- Added Schema-2 `jan`, `vane`, and `opencode` adapters using explicit operator-configured Tailscale endpoints; no peer discovery or endpoint sweeping is performed.
- Added shared OpenAI-compatible chat/embedding endpoint configuration so Jan and OpenCode can share one primary/optional secondary chat model while Vane uses a small embedding model.
- Installer 1.6.9 adds a **Remote agents** tab to the Environment page with migrated non-secret endpoint/model settings, masked service/API credentials, and connection probes.
- Optional/public plugins now use checksum-first reconciliation: `vision_parse`, `web_browser`, `jan`, `vane`, and `opencode` have their installed Schema-2 `src/` hash checked before any write. Matching adapters are untouched; mismatched Norm-owned `src/` is repaired from the packaged release and re-verified, with the pinned public 0.53.20 source used only as a second repair path. Missing plugins prefer the exact public source with a verified packaged fallback. Non-src plugin state/cache is preserved, and reproducible third-party runtimes/dependencies are not vendored into portable/source backups.
- Kept the 0.53.19 weekly-condensation soft-target correction and optional web-browser plugin.
- Restored the built-in `backup` plugin that was advertised by the manifest/docs but accidentally absent from the 0.53.18/0.53.19 public payloads.
- Removed the inherited `history_maintenance.py.normpatch` development artifact, generated Python bytecode/cache directories, generated `plugins/.registry.json`, and the unreferenced legacy prompt-worker module from the portable source. Runtime-generated state and documented compatibility shims remain intact.

## 0.53.19 — 2026-10-05 — Resilient weekly condensation and optional real-browser web plugin

- Weekly/background memory condensation no longer treats the configured 1,800-character batch target or 6,000-character snapshot target as fatal hard limits. Norm still performs a tightening pass and keeps the smaller valid result, but an unusually information-dense batch is checkpointed and carried forward with a warning instead of aborting the entire maintenance run with `refused compression target`.
- Added optional schema-2 `plugins/web_browser` v1.0.0 with Playwright browser navigation, Beautiful Soup 4 + Soup Sieve link/DOM parsing, Trafilatura main-text extraction, bounded same-domain crawling, browser-backed Bing/Bing News search, persistent browser sessions, and workspace-confined linked-file downloads.
- Web browsing prefers installed Microsoft Edge, then Chrome, then an already-installed Playwright Chromium. It does not bypass CAPTCHAs/anti-bot challenges and rejects localhost/private/link-local destinations to avoid turning untrusted pages into an internal-network probe.
- The web plugin executes its third-party runtime in Norm's `.venv` worker rather than importing Playwright into the frozen `norm.exe`, keeping the plugin independently removable and avoiding a large PyInstaller browser/runtime payload.
- Added exact direct dependency pins for Beautiful Soup 4.15.0, Soup Sieve 2.10, Playwright 1.63.0, and Trafilatura 2.3.0. Playwright 1.63.0 supports Python 3.14 and can drive installed Edge/Chrome without downloading a separate browser binary.
- Corrected `[project].version` in `config/settings.ini` to 0.53.19 so installer-built `norm.exe` metadata matches the package manifest; the 0.53.18 public tree still carried the older 0.53.16 project value.

## 0.53.18 — 2026-10-05 — Tight weekly working memory, controllable maintenance, and public runtime bootstrap

- Regular scheduled background condensation is now explicitly a tight working-memory synthesis layer rather than a rewritten history. The default 14,000-character input slice targets about 1,800 characters per distilled slice and about 6,000 characters for the final snapshot; repeated project updates collapse into current state plus durable decisions, unresolved obligations, and reusable lessons while transient examples/levels/shorthand, routine successful checks, smoke narration, superseded state, and source-by-source restatement are discarded.
- Regular snapshot generation no longer gets large blind continuation budgets. Slice generation uses 900 x 2 and final snapshot generation 1,800 x 2; over-target output is re-condensed as a candidate rather than continued. Compact-record reconstruction remains a separate 4,800 x 4 validation path.
- Scheduled maintenance is now first-class operator-visible work. /status/busy reports maintenance mode/phase/run state; /queue and /queue-full show active or parked maintenance even when the prompt queue is empty.
- /suppress-task falls through to active scheduled maintenance when there is no active/queued user task ahead of it. Suppression writes durable PostgreSQL weekly_maintenance_parked state, marks the Redis maintenance run suppressed with auto_resume=false, cancels the active model call, and preserves its checkpoint.
- Added /resume-task maintenance to explicitly resume a suppressed or requires_attention scheduled maintenance run when the worker is idle.
- ModelOutputTruncated during scheduled maintenance is now a deterministic attention condition: Norm parks the run as requires_attention with automatic retry disabled instead of hot-looping the same failed checkpoint. Other transient failures can still retain same-mode resume evidence.
- Scheduled maintenance now clears the worker idle flag for its lifetime, so busy/status controls match actual model/GPU work.
- Added tools/test_maintenance_control.py; six focused maintenance-control regressions pass alongside existing maintenance, model-switch/help, and suppression/resume tests.
- Public WeasyPrint/Pango delivery changed from vendored native binaries to verified download-on-install/lazy repair. package-manifest.json pins the official WeasyPrint 70.0 Windows onedir URL and SHA-256; tools/fetch_weasyprint_runtime.py verifies, safely extracts, validates Pango, and renders a real PDF.
- Installer 1.6.8 preserves a valid existing WeasyPrint runtime, invokes the canonical source helper when it is missing/stale, and then repeats --info plus real PDF smoke validation. tools/weasyprint.cmd uses the same helper for runtime repair.
- The canonical public release layout is installer + installer source + portable-source ZIP + SHA + matching exploded src tree. Private machine state/configuration remains local rather than defining a separate private code edition.

## 0.53.17 — 2026-10-05 — Session model switching and single-source operator help

- Added /switch-model as a Norm runtime control backed by the live Ollama API instead of model-store directory scanning. With no selector it lists installed models as deterministic one-based choices (norm / norm:latest first, then alphabetical); a number, exact tag, or uniquely resolvable base name can be selected.
- Norm now always boots all model roles on canonical norm. Interactive switches are session-only and never rewrite runtime configuration or durable boot state; restarting Norm returns to norm.
- Model switching is idle-only and fail-closed. Norm silently probes the candidate on every distinct live Ollama endpoint used by N1, N2/chat/worker, context, and vision, re-checks tracked work after probing, then changes all live client model pointers together. Missing models, probe/load failures, endpoint disagreement, new work, or commit failure leave or restore the prior model.
- Ollama shutdown now targets the active session model rather than assuming the boot model.
- Added docs\help_menu.txt as the single operator-help authority. Both Prompt frontends read it at help / /help time; the duplicated hardcoded Python command menus were removed.
- Fixed the activity-control callback signature for /memory-condense -deep so the existing full/deep API payload reaches PromptWorker.request_memory_condense without a one-argument callback mismatch.
- Added tools\test_model_switch.py; seven model/help regressions pass alongside the existing five maintenance tests, N1 gate tests, and requirements-manifest checks.

## 0.53.16 — 2026-10-04 — Maintenance reliability, hierarchical memory condensation, complete console teardown, and bundled WeasyPrint

- Deep-history reconstruction is isolated per sampled compact memory. Each replay gets up to **4,800 output tokens per continuation segment × 4 segments**; no neighboring compact memory, previous replay, or tool call can help the sample pass. Replay text is disposable and is not persisted as memory.
- Failed compact-memory validation may repair only that record from its own authoritative task source, then re-run the isolated reconstruction. The repaired record is the one persisted; a failed retry preserves trusted/raw state.
- /memory-condense is explicitly recent-only, using live-configurable memory.regular_memory_window_days (default **14 days**). Manual force means “run now,” not “scan all history,” and recent mode does not globally prune old conversation history.
- Scheduled memory maintenance now alternates successful **regular -> full -> regular -> full** passes on memory.scheduled_memory_interval_days (default **7 days**). Failed/interrupted scheduled work resumes the same recorded mode; alternation advances only after success. Automatic deep scheduling was removed.
- Added manual /memory-condense -deep for bounded older raw-history compaction/validation without hierarchical merging.
- Scheduled and manual /memory-condense -full sweep the complete date-ordered historical archive. Validation is partitioned into compact-row batches via deep_history_full_batch_rows (default **200**) and samples deep_history_full_samples_per_batch (default **12**) isolated rows from each batch. After QA passes, one hierarchical merge level considers chronologically neighboring windows of up to deep_history_full_merge_max_records (default **6**). A window may remain unchanged or reduce to 1..N validated replacement rows; repeated full passes can therefore progressively condense prior merged outputs.
- Within each neighboring merge window, full mode may leave unrelated rows separate or consolidate genuinely related/redundant subsets into **1..N** surviving records instead of forcing N-to-1. Every source memory must appear exactly once in the proposed partition; singletons stay unchanged; actual merged groups retain full provenance.
- Merged records must preserve reconstructable task/request, meaningful steps or itinerary, tools used, results/current state, artifact pointers, efficiency/failure lessons, reusable constraints/fixes, and concise improvement notes. Each constituent is replay-tested from the merged record alone. A failed merge falls back to its original constituent compact memories.
- Background-condensation checkpoints use unique PID/UUID temp files, flush + fsync, six bounded Windows atomic-replace retries, and a final durable in-place rewrite fallback for persistent destination-sharing denial.
- /stop-all now signals exact detached Norm operator-console hosts after the SOS snapshot is verified. Non-prompt operator windows auto-close after about **5 seconds**; Norm Prompt remains about **10 seconds**; Enter or Ctrl+C closes a countdown immediately. Generic python.exe processes are never mass-killed.
- Bundled the official **WeasyPrint 70.0** Windows onedir runtime under tools\weasyprint, including the UCRT64 native stack with **Pango 1.58.2**. No target-machine MSYS2/Pango compilation is required.
- Installer **1.6.7-unified** validates the bundled WeasyPrint runtime with --info and a real HTML-to-PDF render before installation reports success.
- Current-state documentation was reconciled against 0.53.16 source/config. README.md, CURRENT_STATUS.md, DEVELOPMENT_NOTES.md, MAINTENANCE_VERIFICATION.md, FUTURE_IMPLEMENTATION_NOTES.md, and SOURCE_PACKAGE.md contain current state/current contracts/current evidence/active backlog only; RELEASE_NOTES.md remains the sole maintained historical ledger.
## 0.53.15 — 2026-10-04 — Canonical path-policy and internal state root
- Added one canonical `state_root` setting under `[paths]`; weekly condensation checkpoints, deletion trash/file backups, voice-profile state, GUI fallback state, emergency snapshots, and full-backup state now resolve through it instead of separately spelling `runtime_root/state`.
- Added `@state` plus `internal_directories` to the central file-access policy. `@state` is internal-only by default and is not exposed to N2/native/plugin file tools.
- Core file tools and built-in file plugins now share the same `authorize_path()` boundary. Outside-root access is always rejected; the enforcement flags now only select normal-vs-HARDLOCK messaging.
- Added `[file_access_overrides]` additive per-capability roots such as `file_read.read_add = @state` without widening every tool.

## 0.53.14 — 2026-10-04 — N1/N2 tool-gate checkpoint 1

- Added a two-role runtime boundary without changing user-facing routing yet: N1 is a gatekeeper/supervisor and N2 remains the existing reasoning/worker path. User input and N2 answers continue through the current conversation flow without N1 rewriting them.
- `run norm` now initializes both logical agent clients. Both default to the existing `norm` Ollama model/endpoint for checkpoint 1, while `runtime.json:agents.n1/n2` provides separate role configuration for later model/host separation.
- Removed N2 ownership of `verification_preflight`, `verification_history`, and `verification_checkin` from the native tool loop. N2 now proposes the actual information tool with `n1_need` and `n1_target`; N1 alone checks the live Redis verification pool, decides reuse versus fresh execution, and records fresh evidence.
- Fresh tool execution results are passed to N2 unchanged. N1 verification/check-in metadata is recorded out-of-band; Redis reuse returns the already-verified fact instead of invoking the tool. Exact repeated requests within the same task/step reuse the prior raw result.
- Added a first loop/rabbit-hole guard: exact repeated N2 tool requests are suppressed/reused, while consecutive similar N2 reasoning/tool turns are passively observed and escalated to N1 for a loop judgment. A judged rabbit hole is stopped before that turn's proposed tools execute, then N2 receives a reset instruction to continue from established evidence using a materially different next action.
- Deterministic runtime read-back verification after writes remains a core safety invariant and is not routed through model approval because it is not an N2-requested information call.

## 0.53.13 — 2026-10-03 — runtime summary, prompt fidelity, and compact validation pool

- Retained the end-of-task durable summary instead of suppressing/removing it: structured generation stays silent, then one complete reconciled summary block is published after persistence.
- Replaced newline/1024-character runtime display buffering with immediate publication of upstream Ollama answer/thinking fragments. This is independent of the 14,000-character maintenance batch limit, which remains unchanged.
- Extended the Ollama degeneration guard to catch long coherent phrase loops (repeated sentence/paragraph blocks), not only low-entropy token loops; this covers the observed repeated-thought failure where normal-vocabulary prose could repeat indefinitely.
- Kept the 0.53.12 current-state reconciliation fix: summary refresh uses the coverage cursor, active current memories outrank stale prior text, and double model failure writes a deterministic current-state fallback rather than freezing old state.
- Hardened current-state reconciliation against the live monotonic-growth failure: summary replacements containing `Superseded information`, embedded `Recent messages`, or conversation-log sections are rejected and retried; a second invalid result advances to the deterministic current-state fallback. The thread summary row may shrink and is not required to preserve prior text.
- Tightened prompt reduction: model-derived intent/details remain useful, but an ordinary complete task sentence cannot be removed just because it was classified as durable memory. Only exact explicit-aside spans (plus non-task parentheticals) are eligible for removal from executable wording.
- `vision_parse` 0.2.1 retains the ten-page call limit and adds 1.5x/2.2x/2.9x adaptive render tiers: easy clean pages use 1.5x, moderately dense clean pages use 2.2x, and suspect/dense/split pages use 2.9x.
- Prompt interpretation remains enabled for intent, command classification, durable memories, corrections, preferences, and constraints. Executable text is now conservative: only exact, structurally separable sidecar spans may be removed; embedded task qualifiers such as `end of task summary` stay verbatim.
- Added regressions for conservative prompt reduction, immediate stream publication, complete durable-summary events, stale-summary fallback, and the unchanged `consolidation_batch_chars=14000` boundary.
- Documentation split was corrected: `CURRENT_STATUS.md`, the main README, and `SOURCE_PACKAGE.md` now describe only current behavior; historical implementation facts remain in `RELEASE_NOTES.md`. Vision history is recorded under the versions that actually shipped it: 0.53.8 = 4 pages/2.5x, 0.53.12 = 0.2.0 with 10 pages and 2.2x/2.9x, 0.53.13 = 0.2.1 with 1.5x/2.2x/2.9x.
- Reworked the live verification pool to the intended compact architecture: Redis now uses one `norm:validation:pool` hash instead of `facts` + `recent` + per-subject observation zsets/pending keys. Preflight reads Redis first and only then lets Norm choose reuse, optional PostgreSQL history, or a fresh information tool; check-in uses target-first candidate matching so minor wording changes cannot proliferate near-duplicate facts. Same-value checks increment one count, changed values reset to 1 with previous value/change time, and twice-daily migration merges eligible >24h hot records into PostgreSQL before deleting the Redis field. PostgreSQL verification retention is now about 14 days.
- Promoted the installer wrapper to **1.6.6-unified** and simplified release packaging to `Norm Installer 1.6.6.zip`; redundant outer audit/checkpoint/change-note copies were removed because authoritative project documentation already ships inside the portable source.

## 0.53.12 runtime-summary reconciliation hotfix — 2026-10-03

- Replaced the 0.53.9 rolling-summary failure mode that could preserve an old branch summary indefinitely after two output-budget hits. Summary refresh now advances from the last successfully covered message cursor instead of re-reading only the last 20 messages.
- Runtime summary semantics are current-state projection, not a supersession timeline: newer versions/values/statuses remove older ones unless the historical transition is itself operationally relevant. Completed/resolved work and stale unresolved state are pruned.
- Summary maintenance now consumes newly extracted active memories before rebuilding runtime state. Internal summary/memory JSON generation is silent and no longer dumps maintenance JSON into the normal runtime activity stream.
- If both model rebuild attempts fail, Norm advances to a deterministic current-state fallback from active durable memories plus the latest verified assistant results instead of freezing the stale prior summary.
- Turn interpretation may still classify sidecar durable details, but it may not rewrite the executable user request; the full original user wording remains authoritative.
- This hotfix does not change `memory.consolidation_batch_chars`; the unrelated maintenance batching value remains 14000.

## 0.53.12 verification/archive maintenance — 2026-10-03

- Replaced the optional/manual validation bookkeeping path with a mandatory live Redis verification protocol around information-gathering tools: preflight Redis check, explicit PostgreSQL-history yes/no decision, one evidence tool, then mandatory Redis check-in before any next evidence tool or final answer.
- Semantic identity decisions remain model-owned through stable key + who/what/where/how/why; Python only stores/compares the canonical key/value selected by Norm.
- Same-key/same-value observations increment the current generation; changed values immediately become the live Redis value with count 1. Timestamped old->new transitions remain pending in Redis until optional PostgreSQL history accepts them.
- Added twice-daily migration of Redis verification observations older than 24 hours to PostgreSQL historical batches. PostgreSQL counts are explicitly historical and are not compared to live Redis counts as contradictions. Weekly maintenance purges verification-history/change rows older than seven days.
- Added archive-aware `file_read` through a Python intermediary that prefers local 7-Zip and falls back to Python ZIP/TAR readers. Archive inspection is non-extracting and follows tree/sizes -> SHA-256 -> selective member read escalation.
- Bundled 7-Zip 26.03 x64 under `tools\\7zip` and changed archive resolution to prefer that package-managed copy after an explicit `NORM_7ZIP` override, so a target machine no longer needs a system 7-Zip installation.
- Made DB3 `prompt_id` a durable idempotency key. Any uncertain/redelivered submission first resolves the original root task by `source_prompt_id`; running work waits on that same task and terminal work replays its durable result without routing, inserting another user turn, or creating another `chat-*` task. Failed/cancelled terminal tasks also return their durable summary to the synchronous owner instead of throwing after finalization.
- Fixed the first-party `postgres_pool` tool adapter. It now resolves `runtime_bootstrap.build_postgres_pool()` instead of importing a second `_pool` module, so model-facing PostgreSQL tools borrow the exact same process-global pool object already used by the runtime. Added `postgres_query` as a bounded read-only query surface for the approved `norm`/`stocks` logical connections and structured `postgres_execute` for INSERT/UPDATE/DELETE only inside Norm's configured runtime schema through the shared `norm` connection. Full backup helpers resolve approved connection parameters without opening a pointless child-process psycopg pool before `pg_dump`; no host/user/password/arbitrary database parameters or direct `psycopg.connect()` path are exposed to the model.
- Repointed manual `/memory-condense` to the replay-validated deep-history pipeline. Manual normal mode bypasses the scheduled age/interval gate, compacts a bounded batch of new terminal task history, and replay-validates every compact record from that pass. `-full` refreshes all surviving compact/raw history newest-to-oldest in batches of up to 200 source tasks; older records receive relevant newer compact state so stale/superseded lessons can be corrected or reduced to historical context, and up to 12 stratified records per full batch are replay-checked. Both retain the SQL-backup + fail-closed replay gate before covered raw history can be removed or refreshed compact history committed. Validated compact history remains as individual searchable `norm_runtime.task_history` rows with no aggregate character cap; deep-history completion no longer forces the entire archive through one 12,000-character global merge.
- Removed the planner's fixed batch-of-eight guidance; repeated work now batches adaptively and file/archive work prefers machine-readable manifest/hash comparison first.
- Emergency `/stop-all-now` now closes helper PostgreSQL pools before process teardown and allows a brief bounded `norm.exe` shutdown window before the existing force-kill fallback.

## 0.53.12 control-race maintenance — suppress/flush resilience

- Fixed a race where `/suppress-task` could return before the active Ollama generation had fully unwound, then `/flush-suppressed` could delete the durable task and a second `/suppress-task` could hit a deleted task and surface as HTTP 503.
- The active suppression transition marker now survives until the worker acknowledges cancellation; duplicate suppress requests during that window are idempotent.
- Prompt control HTTP failures are displayed without terminating the Prompt console.
- The Prompt console now closes its one-shot PostgreSQL pool after recovering the latest turn, preventing `psycopg_pool` finalizer warnings on helper shutdown.

## Coverage note

The retained project history documents the formal release line from **0.51.0 through 0.53.9**, plus the reconstructed 0.53.11 baseline and the current 0.53.15 release. No standalone 0.53.0 or 0.53.10 release artifact/section was found in the retained source history, so this file does not invent changes for those version numbers. Same-version hotfixes remain under the version they actually modified.

## 0.53.12 — 2026-10-02 — voice profile, startup compatibility, and operator-console recovery

- Added first-party schema-2 `voice_profile` 0.3.0. Voice profiles can build from PDF folders through the existing `vision_parse` plugin or from already-extracted text; source quotes remain untrusted corpus data and factual corpus knowledge stays retrieval-based.
- Updated `vision_parse` to 0.2.0: one call can traverse up to ten pages; ordinary clean pages use 2.2x rendering while dense/suspect/split pages use 2.9x, dense two-column pages split into left/right crops, and very dense single-column pages split top/bottom. The shared Ollama vision client preserves `done_reason`/`eval_count` and accepts `num_predict`; full-page `done_reason=length` automatically falls back to split crops, token-repeat aborts retry only the affected page/crop once, and a bounded process-local 128-page cache avoids repeating identical page/model/mode/focus work.
- Restored the shared GUI helper API `load_runtime_config()` as a **lazy** compatibility wrapper. Prompt and Replies can load runtime/queue/PostgreSQL configuration again without making the lightweight launcher eagerly import the full runtime bootstrap.
- Added transition compatibility for the schema-2 `verbatim_lines` move. The maintained CLI remains `plugins\verbatim_lines\src\_cli.py`, while a root-level `plugins\verbatim_lines\_cli.py` shim keeps older frozen executables/configurations working during in-place upgrades. `verbatim_lines` 1.0.1 also adds a private exact replacement primitive, and core `write_file`/`replace_text` now delegate exact temporary-file content creation to that hash-verified plugin source while retaining their existing authorization, SHA guard, backup, retry, and atomic-replace logic; no unguarded public overwrite tool is exposed.
- `Run-Norm.bat` continues to use the Python launcher/service architecture; it now delegates all three operator windows to `tools\start_operator_consoles.py` instead of spawning each helper independently.
- Norm Runtime, Norm Replies, and Norm Prompt now use the persistent `operator_console_host.py` wrapper. If a helper exits, its console stays open and displays the exit code/error instead of flashing away.
- Runtime and Replies no longer close merely because `norm.exe` briefly disappears; they stay open and retry their data source until the operator closes the window.
- Early detached-service stdout/stderr remain captured in `logs\norm-bootstrap.stdout.log` and `logs\norm-bootstrap.stderr.log`, so import/frozen-startup failures that occur before normal runtime logging are visible.
- Synchronized `core\requirements.txt` with direct runtime/tool imports: NumPy, OpenCV, Kornia, prompt-toolkit, Rich and tzdata are now represented alongside Redis, psycopg/psycopg-pool, aiohttp, Pillow, cryptography and PyMuPDF. PyTorch remains intentionally installer-managed through `environment.torch_version`/`torch_index_url` so the configured CUDA build is preserved.
- Added/retained `BEHAVIOR_AUDIT_0.53.12.txt` documenting high-impact existing capabilities without silently changing their policy.
- Installer remains **1.6.5-unified**. Normal PyInstaller cache reuse remains intentional; this release does **not** switch normal builds to `--clean`.

## 0.53.11 — 2026-10-02 — schema-2 plugins, shared validation pool, PostgreSQL pool, and installer migration

- Standardized first-party plugins on schema 2: root `plugin.json` + README, executable code under `src/`, normally injected from `src/main.py`. `plugin.json.sha256` is the deterministic identity of the complete `src/` tree; generated `plugins/.registry.json` is runtime cache/state rather than package authority.
- Added the first-party bounded `postgres_pool` capability using `psycopg-pool==3.3.3`; approved named connections (`norm`, `stocks`) share process-global bounded pools instead of opening unrestricted direct connections.
- Added one shared semantic validation/reuse pool across tasks and tools. Redis owns live rolling-24-hour/current-generation counts; PostgreSQL stores compact durable snapshots/history on the 12-hour checkpoint policy. A changed value starts a new generation at count 1.
- Refreshed shared validation context before each model/tool-decision round so repeated checks can be recognized across unrelated work without treating counts as permission gates.
- Hardened suppression/resume reconciliation: durably suppressed pending work is not resurrected at startup, resume restores the captured work exactly once, and duplicate resume remains idempotent.
- Added UTC-aware validation timestamp normalization and regression coverage.
- Installer 1.6.5 migration distinguishes the old installed public `norm-imprint.json` baseline from the private local imprint. Effective precedence is manual Environment edit > existing customized value > private local imprint > new public package imprint; an absent old public field means there was no old default.
- Legacy PostgreSQL routing can be recovered from the external `%APPDATA%\Norm\.env`; passwords remain external and are not written into public/package imprints.

## 0.53.9 — 2026-10-01 — operator network map and public configuration split

- Added `/network-map` and `/network-map --json` as synchronous operator commands before normal DB3 prompt ingress.
- Active checks come only from explicit `config/network-map.json` targets; discovered peers never widen the probe set. Honeypot/decoy matches remain observable but are never probed.
- Added `tools/norm-network-map.cmd --json` for SSH/automation.
- Public defaults are topology-neutral; Unified Installer 1.6.0 supports a local non-secret imprint.

## 0.53.8 same-version reliability hotfix — vision plugin + suppression handoff

- Moved PDF vision parsing into the actual package-managed `plugins\vision_parse` tree. The dynamic plugin exposes `plugin_vision_parse__vision_parse__vision_parse`; the old native duplicate is no longer advertised.
- Suppressing a durable task now removes its live Redis work/retry/escalation copies after the resume snapshot is safely stored in PostgreSQL, so the worker can immediately advance to unrelated queued work.
- The synchronous conversation wait path now treats `suppressed` as a terminal-for-this-delivery state and returns promptly, allowing DB3 ingress to acknowledge that prompt and dispatch the next one without requiring `/flush-suppressed`.


## 2026-10-01 - 0.53.8 same-version PDF/stream hotfix
- Added first-party `plugins\vision_parse` PDF reading: PyMuPDF supplies page text/rendering and the local Ollama vision model reconciles visible page content against the untrusted text layer. This avoids treating mojibake, bad column ordering, or OCR-like corruption as ground truth.
- `vision_parse` handles at most four pages per call, renders each page at 2.5x for vision, and returns page-numbered readings plus a continuation page so large books remain resumable.
- Added PyMuPDF 1.28.2 to the managed dependency set.
- Added an Ollama stream watchdog for severe repetitive-token degeneration and bounded partial-line flushing. A no-newline token loop can no longer make the runtime window appear silent and then dump an enormous buffered string only when `/stop-all -now` closes the model response.
- Preserved the existing 0.53.8 unresolved-bits SQL typing fix and bounded rolling-summary refresh fixes.

## 0.53.8 — 2026-09-30 — Ingrained Details and unresolved-bit routing

- Added mixed-turn interpretation before planning: Norm preserves the full user message verbatim while separating the primary executable request from meaningful side information typed as **Ingrained Details**.
- Confident details are written directly to their final durable home using existing memory types or current-task context. Explicit corrections can supersede a matched active memory; future actions/backlog items use the existing `task` memory type.
- Added PostgreSQL `unresolved_bits` as the temporary catch basin only for meaningful details whose correct home is unclear, plus `unresolved_bit_trials` for test-fit evidence while unresolved. Exact repeated unresolved content increments mention count and merges source-thread provenance.
- Added bounded test-fitting of unresolved bits against later real tasks. Plausible matches are tested immediately; otherwise Norm occasionally explores a few least-tested bits. Promotion/application deletes the temporary unresolved record and its trial history instead of maintaining a resolved graveyard.
- Added evidence-based unresolved cleanup: by default a singly-mentioned bit may be removed after 15 non-useful trials spanning at least 3 distinct task domains. Repeated user mention or any useful trial prevents that automatic deletion; recency alone does not qualify.
- Added unresolved-bit state/trial aggregates to background-memory condensation input so uncertain context is visible to compact memory maintenance while still remaining unpromoted.
- Split task provenance into the full original user turn and the actionable primary task request throughout root, child, recovery, deferred-append, verifier, and worker envelopes. Norm-generated steps remain explicitly internal.
- Carried DB3 ingress `prompt_id` through `/api/chat` into durable task-plan provenance. `/queue` now joins ingress entries to the newest matching running task/child and shows its current step rather than presenting the first characters of a mixed user turn as runtime status.
- Same-version launcher presentation refresh: automatic `norm.exe --service` startup is fully hidden (`CREATE_NO_WINDOW` + hidden startup info), and `Run-Norm.bat` prefers a dedicated Windows Terminal window for **Norm Runtime** while keeping Prompt/Replies unchanged; the classic Runtime console remains the fallback when `wt.exe` is unavailable.
- Same-version startup-cleanup hotfix: `prompt_worker.py` now imports `pathlib.Path` at module scope. The 0.53.7 task-retention/temp-cleanup paths already used `Path` during startup and terminal cleanup; the missing import caused `NameError: Path is not defined` and forced Norm to preserve temp material on every startup.
- Same-version unresolved-bit query hotfix: explicitly cast the nullable exclusion parameter to PostgreSQL `text` when selecting unresolved candidates. PostgreSQL could not infer the type of a bare `%s IS NULL` placeholder and raised `IndeterminateDatatype`, preventing unresolved-context test fitting.
- Same-version conversation-summary output-budget hotfix: rolling thread summary refresh no longer uses the legacy single 900-token `generate()` call. It now requests bounded structured output with an 8,000-character summary ceiling and a 3,200-token budget, retries once with a 3,500-character compact target after a model length stop, and preserves the prior summary with a warning instead of emitting a background ERROR if even the compact retry cannot fit.

## 0.53.7 — 2026-09-30 — large-source streaming and semantic task storage

- Removed the whole-source `read_file` size gate. Large UTF-8 sources are streamed with byte/line continuation cursors; the source itself is not limited by the model-result cap.
- Reinterpreted `tools.max_read_bytes` as a 24 MiB processing buffer and added a separate 384 KiB per-tool-result ceiling so source size, processing window, and model-facing payload are distinct. Large sources skip full-file SHA-256 by default and expose a stable size/mtime/path fingerprint; callers can explicitly request a full SHA scan.
- Added task storage accounting under `temp\tasks\<task_id>` with a 54 GiB managed-storage ceiling and 3 GiB per-pass processing allowance. Reaching the processing ceiling writes a continuation ZIP and parks/suppresses the same task for explicit manual resume; resume resets only the per-pass counter.
- Added `append_task_note` for durable internal Markdown extraction/summary notes with automatic 5 MiB physical-file rotation. Aggregate logical notes remain unconstrained except by the task storage ceiling.
- Task-scoped image-analysis derivatives are now written under task temp as reproducible cache rather than long-lived workspace clutter. Verified terminal cleanup retains compact source/recipe lineage and internal notes in `.norm-task-retention` while deleting reproducible derivative bytes.
- Human-facing completed replies are capped at 384 KiB. The full durable PostgreSQL task summary remains intact, and oversized full replies are also spilled to `workspace\large-responses`.

## 0.53.6 — 2026-09-30 — recovery-state hygiene and prompt provenance

- Restored `/inject-context` end-to-end: the activity API now receives a live injection callback, injections resolve to the current active task tree, persist in PostgreSQL, and are consumed by the worker at subsequent model-call boundaries without creating a new task.
- Injected text is explicitly framed as a user-authored context interjection, while generated steps/recovery/verifier work remain Norm-internal.

- Weekly maintenance and manual `/memory-condense` now run recovery-state cleanup before background-memory condensation.
- Fully terminal task trees with verified summaries automatically drop obsolete `task_recovery_notes`; recovery notes no longer accumulate indefinitely after they have served their handoff purpose.
- Stale nonterminal recovery trees are eligible only when absent from live Redis, not suppressed, and older than the configured stale threshold. Norm summarizes task state plus recovery notes into compact `task_history`, replay-validates that summary, and only then prunes the dangling tree. Any summarize/replay failure preserves the original rows.
- Added conservative orphan archive cleanup: `task_evidence_archive` and legacy `task_step_archive` rows are deleted only when a validated compact-history record already covers the missing task; uncovered orphan archives remain preserved for inspection.
- Enforced the existing `request_type` / `prompt_origin` provenance in worker prompts. Norm-generated steps, recovery jobs, verification, and maintenance are explicitly labelled internal and cannot masquerade as user-authored prompts.
- Runtime child/recovery tasks now inherit the true `original_user_prompt`; generated child instructions are retained separately instead of overwriting the user request.
- Added `memory.recovery_stale_hours=24` and `memory.recovery_cleanup_max_trees=20` defaults. The dangling-tree cap does not limit cheap terminal-note cleanup.

## 0.53.5 — 2026-09-30 — pre-task suppression and incremental rebuild cache

- `/suppress-task` now covers the canonical DB3 ingress gap before a durable task UUID exists: it suppresses the currently dispatching prompt first, otherwise the oldest live queued prompt, and prevents uncertain retry resurrection.
- A dispatching pre-task prompt cancellation also cancels the active Ollama call so planning/generation does not continue after the operator suppressed it.
- Runtime PyInstaller work/spec state now persists under `state\build-cache\pyinstaller`; normal builds reuse analysis state and no longer pass `--clean`. `tools\build_norm.py --clean` remains available for deliberate cold rebuilds.
- Updated the cryptography lock from 46.0.4 to 50.0.2.

## 0.53.4 — 2026-09-30 — suppression/cancellation reliability

- Fixed mid-execution suppression acknowledgement to use the actual Redis Streams entry ID rather than the logical task-node UUID. This prevents `XACK` failures after a model generation is cancelled for `/suppress-task`.
- Normalized intentional Ollama response-close races into `ModelGenerationCancelled` without retaining the underlying `http.client` exception chain, so deliberate cancellation no longer surfaces misleading `NoneType.peek` transport tracebacks.
- Applied the same cancellation normalization to text generation, tool-chat generation, and vision streaming.
- Operator consoles now render intentional model cancellation as a yellow cancellation notice instead of a red Ollama error; genuine failures remain red.
- Suppression remains durable-first and preserves the queue entry in Redis after acknowledgement, matching the existing pre-execution suppression path.

## 0.53.3 — 2026-09-29 — about command and taskless manual memory condensation

- Added `/about` to both operator prompt surfaces with current project/version, runtime/executable path, source/frozen mode, Python, package, and plugin summary.
- Added `/memory-condense` for incremental background-memory snapshot refresh and `/memory-condense -full` for a full-source snapshot rebuild.
- Manual memory condensation is scheduled directly on the existing worker and runs only when that worker reaches idle; it does not enqueue a user prompt, create a new task UUID, or re-plan user work.
- Reused the checkpointed `DeepHistoryMaintainer.rebuild_background_snapshot()` path so interrupted condensation can resume safely. The manual operation records a PostgreSQL maintenance note and leaves the stronger destructive `/condense-memories` concept unimplemented.
- Updated visible `/help` output in both prompt consoles.

## 0.53.2 — 2026-09-28 — cipher/plugin separation and frozen cryptography support

- Split the optional five-machine Rotor5 message transform into its own first-party `plugins\rotor5_cipher` capability; `stegosplit_message` is again only the two-image carrier.
- Rotor5 R5E2 uses five independently derived 256-symbol rotor machines, a fresh per-message nonce, pre-rotor compression, and HMAC-SHA256 envelope authentication.
- Added optional `NORM_ROTOR5_SECRET` and `NORM_ROTOR5_PREVIOUS_SECRETS` values in the configured Norm `.env`; only those two already-redacted secret values are exported into the Norm process for the hot-loaded Rotor5 plugin.
- Added `cryptography==46.0.4`, `cffi==2.0.0`, and `pycparser==3.0` to the reproducible environment lock.
- PyInstaller now explicitly collects `cryptography`, `cffi`, and `_cffi_backend`, allowing the frozen `norm.exe` process to hydrate the ChaCha20-Poly1305 StegoSplit plugin without relying on imports from an external editable checkout.
- `stegosplit_message` 0.3.0 exposes typed native tools for text, Base64 bytes, files, password rotation, cover reconstruction, authenticated pair info, and differential stats while retaining the legacy `run(payload)` wrapper.

## 0.53.1 — 2026-09-28 — aiohttp control plane

- Replaced the hand-rolled ThreadingHTTPServer/socketserver chat and activity/control transports with aiohttp.web.
- Preserved the coordinator-facing lifecycle contract so startup/shutdown ordering remains stable.
- Blocking conversation, PostgreSQL-facing, context, and control callbacks run via asyncio.to_thread rather than blocking the HTTP event loops.
- Activity SSE is asyncio-native and treats client disconnect/reset/WinError 10053/10054 as normal stream termination.
- Pinned aiohttp==3.14.3 in runtime and installer locks.
- Retained loopback/Tailscale-only bind validation.
- Added bounded graceful AppRunner cleanup and explicit event-loop/default-executor shutdown.

## 0.53.1 — 2026-09-28 — ingress retry deduplication

- Serialized uncertain-prompt requeue by durable uncertain-record identity so duplicate retry timers cannot create multiple live Redis deliveries for one prompt.
- Requeue now atomically adds the replacement ingress entry and removes its uncertain source record.
- `/queue` and `/queue-full` now exclude acknowledged-but-preserved historical stream rows from the LIVE QUEUE view while failing open if consumer-group metadata is unavailable.
- Added regression coverage for concurrent retry contenders and live-queue historical-row filtering.

## 0.53.1 — 2026-09-27 — launcher ownership/readiness fix

- Restored the proven `cmd.exe start` path for the three operator consoles after both Norm health endpoints are ready.
- Removed `start_operator_consoles.py` from the normal `Run-Norm.bat` startup path.
- Detached service creation now returns and reports the owned `norm.exe` PID instead of discarding the process handle immediately.
- A pre-existing `norm.exe` is treated as an attach/wait condition; a process launched by the current helper is not subsequently described as a second instance.
- Startup timeout messaging now reports readiness failure without falsely claiming that the just-launched Norm process is a duplicate.


## 0.53.1 — 2026-09-27 — restore explicit operator consoles

- `norm.exe` remains detached in `--service` mode.
- `Run-Norm.bat` launches `Norm Prompt`, `Norm Runtime`, and `Norm Replies` as three explicit persistent consoles via `tools\start_operator_consoles.py`.
- Each console stays open if its Python helper exits during startup, so startup errors remain visible.
- Duplicate GUI-helper mutex detection prints which operator console is already running rather than silently exiting.

## 0.52.6 — 2026-09-27 — detached service launch + HTTP ownership hardening

- `Run-Norm.bat` no longer launches `norm.exe` in a transient/minimized helper console. It uses the shared detached service launcher instead.
- Every automatic `norm.exe` start now passes `--service`; the prior GUI fallback path could start manual-mode Norm and leave it vulnerable to console `KeyboardInterrupt` events.
- Run-Norm refuses to launch a duplicate `norm.exe` merely because HTTP is temporarily unavailable. An existing process is given the health window instead.
- Service mode now installs both Python signal handlers and a native Windows `SetConsoleCtrlHandler` guard for Ctrl+C/Ctrl+Break.
- The visible Ollama GIN server (`/api/tags`, `/api/generate`, `/api/chat`) is not Norm's `12543` chat API; Norm health is established only by the configured chat and activity `/health` endpoints.
- GUI DB3 ingress now catches per-entry dispatch/bootstrap exceptions instead of letting the background dispatcher thread die; enqueueing a later prompt also revives a dead dispatcher thread while preserving queued DB3 entries.

## 0.52.5 — 2026-09-27 — proportional plan verification

- Plan verification rejects only blocking defects: lost constraints, unauthorized/unsafe actions, materially contradictory or impossible designs, missing material verification, unbounded work, or missing final synthesis on multi-step plans.
- Clearly described tightly coupled implementation parts may remain in one bounded step; advisory organization/granularity concerns no longer veto execution.
- Redundant mechanisms are only blocking when they create a real contradiction, ambiguity, or user-constraint violation.
- The verifier no longer invents hypothetical implementation/boundary failures when the plan explicitly inspects/reuses an existing mechanism or includes execution tests for that invariant.
- Repair prompts require the smallest material fix and prohibit mechanically returning the same rejected plan.
- Runtime build tooling lock moves PyInstaller from 6.22.2 to 6.22.3.

## 0.52.4 — 2026-09-27 — canonical prompt ingress and startup reliability

- Local Rich-console prompts and SSH `P` prompts now use the same Redis DB3 ingress stream, consumer group, selected-thread key, default project, and core dispatcher implementation.
- Removed the second `norm:rich-console:*` durable ingress namespace. Frontends differ only in transport/UI; prompt interpretation and task creation no longer diverge.
- The canonical dispatcher creates/selects an explicit thread before `/api/chat`, so an accepted console prompt cannot be converted into a semantic-routing clarification before task creation.
- HTTP 200 is no longer sufficient to acknowledge a normal queued prompt: the response must contain a durable `task_id`. A protocol violation is parked visibly in uncertain state without automatic replay.
- Added ingress lifecycle logging for prompt/source/thread/task IDs to make queue-to-task handoff auditable.
- Put the activity API, worker, chat API, and their server threads under one startup ownership/cleanup boundary so a failed migration or later startup stage releases ports before retry.
- Removed the normal-start duplicate PostgreSQL health preflight; PostgreSQL connection/schema failures now occur inside the bounded startup retry loop.
- Added `connect_timeout=5` to generated PostgreSQL connection strings in addition to the existing lock/statement timeouts.
- Serialized plugin refresh/hydration/execution with one process-global `RLock`, covering global `sys.modules`, `sys.path`, stdout, and stderr mutations across multiple `PluginManager` instances.

This file is the concise, version-by-version record of **implemented and promoted changes** in Norm. It answers “what changed in this iteration?” rather than “what is true right now?”

Rules:
- Add an entry for every promoted version or promoted letter revision.
- List shipped behavior, configuration, architecture changes, migration notes, and release verification that materially define that version.
- Keep unfinished designs and backlog items in `FUTURE_IMPLEMENTATION_NOTES.md`.
- Keep current runtime facts in `README.md` / `CURRENT_STATUS.md` and current engineering contracts in `DEVELOPMENT_NOTES.md`.
- Historical incidents or superseded implementation details belong here only when they materially explain a shipped release.
- Internal staging labels that were never promoted are not separate releases here.

## 0.52.3 — 2026-09-27 — Windows service signal isolation

- `Run-Norm.bat` now launches `norm.exe --service`.
- Service mode installs explicit SIGINT/SIGBREAK handlers that log and ignore stray Ctrl+C/Ctrl-Break events instead of treating them as shutdown requests.
- Canonical operator shutdown remains routed through the activity/control API; direct manual `norm.exe` launches retain normal KeyboardInterrupt behavior.
- This addresses a live 0.52.2 failure where the runtime reached ready state and then exited on unsolicited `KeyboardInterrupt` without any logged `/shutdown` or `/stop-all` request.

## 0.52.2 — 2026-09-27 — startup controls, thread navigation, help cleanup

- Activity/control API now starts before bounded PostgreSQL/schema initialization and reports an explicit initializing phase until chat/worker readiness.
- `/status/busy` remains reachable and reports busy during initialization, preventing GUI dispatch from treating schema startup as idle.
- Added queue-ordered `/new [name]`, `/thread-list`, and `/thread-resume <name|id>` controls to GUI and Rich consoles.
- Named threads are created in PostgreSQL while the switch itself remains ordered in Redis ingress, preserving the thread assignment of older queued prompts.
- Visible help now lists canonical commands only; legacy aliases continue to be accepted for backward compatibility.
- Installer 1.3.4 reuses a compatible `.venv`, allows pip to upgrade to any available release satisfying `pip>=26.1`, retains `.ssh` as protected persistent state, and tightens the GUI layout.
- `/flush-suppressed` now clears both suppressed PostgreSQL task records and the matching parked/suppressed GUI delivery records; in-flight prompt-ID tombstones remain until the HTTP dispatch is no longer active so a late connection reset cannot resurrect the submission.

## 0.52.1 — 2026-09-27 — bounded startup/schema hardening

- Added PostgreSQL schema-migration lock and statement timeouts plus serialized advisory migration locking.
- Hardened startup failure logging and retry behavior so stale PostgreSQL transactions cannot wait indefinitely.
- This release retained the 0.52.0 portable layout, plugin, queue, recovery, and installer contract.

## 0.52.0 — 2026-09-27 — portable source, native plugins, integrity recovery, reusable installation

- Standardized the source/executable directory on `core\` and removed stale `app\` assumptions from the portable package/build path.
- Added reusable package-manifest-driven installation with in-place managed sync and compatible `.venv` reuse.
- Promoted dynamic plugin folders to native hot-reloaded tools with multi-file imports and last-known-good fallback; plugin root is now `C:\Norm\plugins`.
- Added `/queue-full` support and explicit `/status/busy` GUI recognition alongside `/status`.
- Hardened UUID/dependency migration and pruning against deleted-parent lineage; added conservative memory-thread recovery plus `tools\repair_norm_state.py`.
- Moved maintained docs into `C:\Norm\docs`, Norm SSH material to `C:\Norm\.ssh`, and external generated work into `Documents\Norm\workspace` / disposable `Documents\Norm\temp`.
- Added conservative temp cleanup: verified terminal task temp can be removed immediately; old scratch is age-cleaned; unresolved recovery/SOS material is preserved.
- Added a first-party backup plugin design producing installer-compatible private full-state ZIPs with source/docs/plugins/.ssh/secrets/workspace/PostgreSQL and environment rebuild metadata while excluding `.venv`.
- Added explicit `[paths].runtime_root`; the reusable installer binds it to the actual target directory after every source/full-backup sync.
- Fixed queued-worker runtime placeholder expansion by using the shared resolved runtime config, and made image analyzer Python/script validation lazy so plain text work is independent of optional image tooling.
- Fixed emergency-stop SOS path resolution by separating the Norm runtime root from the emergency snapshot output root.
- Preserved/fixed the 0.51.5 GUI suppression safeguards: queued suppressed prompt IDs are rejected before primary dispatch, duplicate suppression checks/helpers were removed, and Redis XRANGE queue inspection never passes `COUNT 0`.
- Added self-contained first-party `stegosplit_key`, `stegosplit_message`, and `verbatim_lines` plugins. The MessageCodec no longer depends on an editable external checkout.
- Added `/backup` (portable source), `/backup full` (sensitive full state), and kept `/backup-zip` as a full-backup compatibility alias.
- This entry describes the 0.52.0 clean source/release package. Machine-specific service health must still be verified after installation.


## 0.51.5 - 2026-09-24 - offline rebuild and helper fixes

- Promoted executable SHA-256: `A3F0714DB6E281086563D8E5D9EECE95AEF449204CAFBDDC21D5CD4AA436E635`. Retained version 0.51.5; 0.51.6 was not assigned.
- Included structural tool-output/evidence/history/log redaction and protected credential-file reads.
- Graceful-stop waiting output reduced to one message; existing worker event remains the completion signal.
- External GUI dispatcher uses quiet-driven busy checks with a slow fallback; existing DB3 fixes remain.
- External runtime viewer separates control messages from partial model lines; model-buffer/SOS storage was not changed.
- Added explicit editable StegoSplit restoration instructions for environment rebuilds.
- Validation: source compilation/offline tests, packaged help/version, bundle inspection, read-only persistence audit and synthetic recovery/render replay. No service start or end-to-end runtime health claim for this revision.

## 0.51.4 — 2026-09-22 — Tailscale authority and centralized network topology

- Replaced the old `[ports]` settings model with `[network]`, including `current_machine`, `current_domain`, per-service host selectors, ports, and `require_tailscale`.
- Central runtime resolution now derives Redis, prompt/deletion/console queues, PostgreSQL, Norm HTTP, activity/control, and Ollama endpoints from the settings resolver instead of duplicating live network values in `runtime.json`.
- Moved environment-specific PostgreSQL database/user/password values to the configured external `.env` file; runtime JSON no longer carries the connection string.
- PostgreSQL runtime uses `norm-host.example.invalid:25434`; Redis authority uses `norm-host.example.invalid:6379`; Norm HTTP/activity bind to NORM-HOST’s Tailscale address; Ollama intentionally remains loopback-only.
- Added Tailscale Serve TCP forwarding for Redis so Memurai remains bound to `127.0.0.1:6379` while Norm reaches it only through the tailnet authority path.
- Added a pre-tool-call authority gate: each native Norm tool call must successfully reach/PING the configured Tailscale Redis authority endpoint before execution proceeds.
- Promoted live executable SHA-256: `0EE7EA56B866F0DC6CC3B54C04A4549A97F76DC4C9CCD968CD13D6D7137CC671`; `norm.exe --version` reports `0.51.4`.
- Post-promotion companion-script repair updated the external GUI helpers to use the same 0.51.4 endpoint/config resolver instead of the removed `[ports]` section and raw `runtime.json` connection fields; this did not require replacing the packaged executable.
- 2026-09-23 companion-script hardening kept the packaged 0.51.4 executable unchanged: Redis-backed replies tolerate malformed legacy bytes, large replies bypass monolithic Markdown rendering, `Run-Norm.bat` uses the central settings resolver/noninteractive-safe startup wait, and GUI `/stop-all now` waits for the live Redis model-buffer to settle, writes Redis task state/events + raw model buffers + PostgreSQL checkpoints to `SOS.md`, fsync/read-back verifies it, then force-terminates Norm/Ollama.
- 2026-09-23 external capability extension kept the packaged executable unchanged: `tools\norm_plugins.py` plus the workspace `plugins\` directory provide filesystem-discovered Python plugins with README/capability matching, version/SHA/UUID identity, fresh per-call loading, and `run_command` access. A one-time process restart is required only for an already-running worker to pick up the new broker instruction; plugin swaps themselves require no rebuild/restart.
- The same 2026-09-23 maintenance pass repaired `tools\norm_backup.py` to use the centralized 0.51.4 settings/runtime resolver, added optional checkpoint labels, excluded runtime `state\file-backups` from ZIPs, and removed one-off convenience/probe/write helpers. A requested `0.52.5` backup label is a snapshot/checkpoint identifier, not a promoted executable release.
- A stale PostgreSQL `idle in transaction` session was found blocking startup schema DDL. Clearing the stale transaction allowed both Tailscale-bound APIs (`12543` and `8766`) to start normally. The underlying startup-lock hardening remains a separate unresolved item, not a claimed 0.51.4 fix.

## 0.51.3 — 2026-09-21 — Nonblocking durable GUI ingress

- Normal `norm_gui.bat` prompts became durable Redis DB3 ingress instead of blocking the prompt window on `/api/chat`.
- Added a background dispatcher that submits the oldest queued GUI prompt through `12543` while activity/control and immediate stop remain independently reachable on `8766`.
- DB3 preserves GUI thread/submission/answer state and queued ingress across Norm restart; interrupted dispatches are quarantined as uncertain instead of replayed automatically.
- `/repeat-submission` and `/repeat-answer` requeue the latest applicable persisted turn verbatim.
- Rich-console ingress received a separate DB3 stream/group namespace so frontends do not steal each other’s entries.
- Promoted executable SHA-256: `2409ad004cd2c2d17937e35d37e7bd415728e7d94a3d04f714692465fe5239a2`.

## 0.51.2b — 2026-09-21 — Unified maintenance correction

- Unified regular memory cleanup, deep-history cleanup selection, and derived image-output purge behind one idle scheduler.
- PostgreSQL `runtime_state` became the durable authority for maintenance cadence; Redis retains only the active crash/restart marker.
- Interrupted maintenance resumes the same recorded mode after restart; successful completion clears the active marker only after durable PostgreSQL success state is written.
- Build tooling gained promoted letter-revision support such as `0.51.2b`.
- Promoted executable SHA-256: `f3710205b3bbcf78e68d3c72eb9d0f7c742677cec19cba77c78d46135136db37`.

## 0.51.2 — 2026-09-20 — Weekly derived-image cleanup (superseded by 0.51.2b)

- Added an independent seven-day idle cleanup pass for derived `images\analysis` output with workspace-boundary validation.
- Added crash-visible Redis cleanup markers and `/status-context` visibility for incomplete cleanup state.
- First live cleanup removed 1,359 derived files / 873,675,067 bytes while preserving source/original images.
- This scheduler design was subsequently corrected and unified in promoted revision `0.51.2b`.
- Promoted executable SHA-256: `332f38e05a31fd4740ac23c0ad91cf60895b574889490f3b76b5be9e7f009b8b`.

## 0.51.1 — 2026-09-20 — Runtime/workspace split and reproducible backup

- Moved the runtime to `C:\Norm` while keeping the model-editable workspace at `C:\Users\NORM-HOST\Documents\Norm`; startup validates that the two roots do not overlap.
- Normal and recovery-child tools receive the configured workspace plus approved external roots, while the runtime tree itself is not model-writable.
- Added `/backup-zip` with PostgreSQL `norm_runtime`, workspace, runtime, manifest, and restore helpers while excluding disposable build/cache output.
- Removed the large rebuildable `.venv` from backups and added pinned Python/CUDA-Torch/dependency rebuild settings and documentation.
- Pruned obsolete build/staging artifacts after preserving the external rollback snapshot.
- Promoted executable SHA-256: `FB5E6E2BB7668D1C2CBD1CAEC60F356D193A50B70B208DD0E2B50C56CEF4BBF4`.

## 0.51.0 — 2026-09-20 — Versioned build, recovery scheduling, and opaque runtime identity

- Made `settings.ini` the canonical project metadata/document-pointer source and added repeatable `tools\build_norm.py` PyInstaller release tooling.
- Deferred append/follow-up planning now waits for the predecessor task to complete and pass final verification before generating the real follow-up plan.
- Oversized-recovery child replacement gained bounded retry handling without repeatedly consuming the parent retry budget.
- Added immutable task/node/dependency UUID identity (`task_uuid`, `node_id`, `edge_id`) and backfilled existing PostgreSQL runtime records while preserving readable compatibility IDs.
- Dependency/append/recovery linkage now uses opaque identities where available, with legacy lookup retained for compatibility.
- GUI companion windows were hardened for graceful runtime shutdown and real Windows Ctrl+C/Ctrl+Break behavior.
- The final promoted 0.51.0 executable after same-version fixes had SHA-256 `A72D7FC8E0268CB42A1F062BB63EFCAA4904B159330972D878779738A2DDAEC6`.

## Earlier development baseline — 2026-09-13 through 2026-09-19

Norm was already under active development before formal `0.51.x` release naming. That work established the PostgreSQL/Redis coordinator model, step-aware planning/verification, cancellation safety, temporary thinking persistence, targeted PostgreSQL pruning, shell evidence capture, busy-state reporting, `/status-context`, and the first GUI/operator shell. Detailed chronology remains in `DEVELOPMENT_NOTES.md`; these pre-version milestones are not presented as invented releases.

