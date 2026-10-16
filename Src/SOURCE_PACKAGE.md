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

# Norm 0.55.4 — Compiled StegoSplit portable-source package

This archive is compatible with Norm Installer **1.6.12-unified**, with package schema 1, `package_type: portable-source`, the installer-owned `norm.exe` build (no legacy build script required), and a matching companion SHA-256 file. Norm's core is distributed as source to be built by the installer. The three StegoSplit plugins distribute their private implementations only as Windows CPython 3.14 x64 native `.pyd` modules; readable `src/main.py` adapters expose the stable Norm tool interface.

- `stegosplit_message` **0.4.3**: V3 paired-message carrier, paired SSH/SHA/MD5 identifier matching, and random 256-bit credential creation/unlock.
- `stegosplit_auth` **0.1.0**: distinct fixed-master/many-client signed image bearer authentication. Optional side plugin; disabled until enrolled/configured.
- `stegosplit_key` **0.2.0a1**: legacy paired key, rotation and cover reconstruction methods.
- The existing compiled V3 carrier engine remains **0.4.0** internally; its native module bytes were not modified.
- `package-manifest.json` and every plugin `plugin.json` carry the matching current versions and schema-2 hashes of the exact shipped source/native trees.
- `plugins/.registry.json` is runtime-generated and deliberately excluded. No private authentication Python implementation, real master image, private client images, SQLite authentication state, or configured secrets are included.

**Verification:** The exact 1.6.12 installer accepted the compiled ZIP in `--validate-only` mode. Windows CPython 3.14 smoke tests passed for Message paired secret unlock, Auth issuance/verify/revoke, Key imports, and 18 plugin identities. Full deployment and SSH-service integration remain untested/not implemented.

**Help:** `README.md` contains operator installation and user procedures. `plugins/stegosplit_message/README.md`, `plugins/stegosplit_auth/README.md`, and `plugins/stegosplit_key/README.md` explain interfaces for Norm; each plugin's `help.txt` summarizes user-facing operations. Complete historical development detail is preserved in `docs/RELEASE_NOTES.md`.

**Security note:** A signed client PNG is a copyable bearer credential. A filename used as a password is exposed with the file. The master issuer seed is in server-master PNG metadata, so secure the master and never publish operational credentials. Auth is not native OpenSSH.
