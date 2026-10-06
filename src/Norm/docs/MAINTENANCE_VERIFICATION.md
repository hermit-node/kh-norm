# Norm maintenance verification

Current package under verification: **Norm 0.53.19 / Installer 1.6.8-unified**.

This file records current package evidence only.

## Source compilation

PASS: current maintenance/runtime/operator source compiles with Python py_compile, including:

- core\norm_main.py
- core\runtime_bootstrap.py
- core\norm_runtime\history_maintenance.py
- core\norm_runtime\durable_log.py
- core\norm_runtime\prompt_worker.py
- core\norm_runtime\activity_stream.py
- core\norm_runtime\ollama_client.py
- core\norm_runtime\model_switch.py
- core\norm_runtime\public_web.py
- core\norm_runtime\rich_console.py
- tools\norm_gui_common.py
- tools\norm_gui_prompt.py
- tools\fetch_weasyprint_runtime.py
- tools\test_maintenance_control.py
- tools\test_maintenance_reliability.py
- tools\test_model_switch.py
- tools\test_public_web.py

## Session model-switch / help regression

PASS: python tools\test_model_switch.py.

Current result: **7 tests passed**.

The regression set verifies:

- norm / norm:latest is ordered first and model names are case-insensitively deduplicated;
- one-based numeric, exact-tag, and unique base-name selectors resolve correctly;
- ambiguous, missing, and out-of-range selectors fail closed;
- norm and norm:latest are treated as the same logical model;
- Ollama model discovery uses /api/tags;
- docs\help_menu.txt exists as the single command-list authority and the previous hardcoded Python help menu is absent;
- runtime startup is hardwired to MODEL_NAME = norm rather than restoring a configured/session alternate.

Source inspection additionally verifies startup switches are blocked, tracked work is checked before and after candidate probes, every distinct live Ollama endpoint is probed, all live clients are committed together, and commit failure restores prior model pointers.

## Public-web regression and live smoke

PASS: python tools\test_public_web.py.

Current result: **10 tests passed**.

The regression set verifies:

- public HTTPS URLs are accepted while localhost, loopback, RFC1918/private, link-local/metadata, Tailscale CGNAT, *.ts.net, URL credentials, non-web schemes, and non-80/443 ports are rejected;
- private DNS answers are rejected before fetch;
- readable HTML extraction removes script/nav/footer noise while preserving title, description, article text, and links;
- Bing News publisher redirect URLs are decoded;
- reader fallback rejects token/signature/auth-looking query parameters;
- reader Markdown parsing preserves article metadata/text while dropping standalone image Markdown;
- web tool schemas are advertised only when public web is enabled;
- web_search and web_fetch remain mandatory verification-pool information tools;
- runtime.json enables/configures the public-web capability.

PASS: live FileToolExecutor smoke on the Windows test host.

Observed behavior:

- web_search(mode=news) returned Bing News RSS results with direct publisher URLs;
- web_search(mode=web) returned Bing RSS results with snippets;
- direct web_fetch of a publisher anti-bot shell returned status 202 and zero readable chars;
- configured reader fallback then returned the same publisher article as readable Markdown with title/published metadata and about 6.9K characters of article text;
- final tool result reported reader_used=true, reader_service=r.jina.ai, network_scope=public_web_only, and external_content_trust=untrusted.

## Maintenance control / tight-summary regression

PASS: python tools\test_maintenance_control.py.

Current result: **6 tests passed**.

The regression set verifies active scheduled maintenance can be suppressed when no user task is active/queued, suppression writes durable park state, requires_attention is non-active/parked, explicit maintenance resume clears the durable park and records operator resume, regular background-summary prompts reject source-by-source restatement and use the tight generation/character targets, deterministic ModelOutputTruncated is wired to requires_attention with auto-resume disabled, and the Prompt queue exposes maintenance plus its resume control.

PASS: python tools\test_suppression_resume.py --services-root <package-root>.

The existing user-task suppression/resume regression still passes: suppression captures exactly once, flush/suppress races remain idempotent, durably suppressed pending work is not requeued on startup, resume restores exactly one captured job, and test Redis keys are cleaned up.

## Memory-maintenance regression

PASS: python tools\test_maintenance_reliability.py.

Current result: **5 tests passed**.

The regression set verifies:

- durable checkpoint replacement of an existing checkpoint;
- isolated reconstruction through continuation-aware generation;
- 4,800-token replay segments with up to four continuation segments;
- literal compact-row QA batching: 450 compact rows become 200 / 200 / 50 even when some compact rows represent multiple raw retry tasks;
- exactly 12 unique replay samples selected from a 200-row QA batch;
- hierarchical merge windows are chronological neighboring rows and honor the configured maximum.

## Archive adapter

PASS: python tools\test_archive_adapter.py.

The archive test verifies the tree/size -> SHA-256 -> selective-member workflow. Test fixtures use exact bytes so Windows newline translation cannot change expected archive sizes.

## File-access policy

PASS: python tools\test_file_access_policy.py.

Current result: **4 tests passed**.

## N1 gate

PASS: python tools\test_n1_gatekeeper.py.

Current checks pass for schema instrumentation, pass-through identity, loop reset/block behavior, Redis evidence reuse, raw fresh-result forwarding, mutation pass-through, mutation invalidation, parent-directory invalidation, and repeated non-cacheable-call blocking.

## Validation refresh

PASS: python tools\test_validation_round_refresh.py.

The live Redis count is observed before reuse decisions and existing verified evidence can satisfy the request without executing N2's proposed tool.

## Requirements manifest

PASS: python tools\test_requirements_manifest.py.

Direct dependency manifests are complete; CUDA-specific PyTorch selection remains installer-managed.

## Runtime-summary protections

PASS: python tools\test_runtime_summary_refresh.py.

Current checks verify coverage-cursor advancement, stale-state pruning, rejection of transcript/supersession-ledger summary shapes, deterministic fallback after repeated model failure, conservative executable-request sidecar removal, and preservation of the unrelated 14,000-character consolidation batch setting.

## WeasyPrint / Pango

PASS: tools\fetch_weasyprint_runtime.py was tested against the exact pinned upstream archive with an empty destination. The helper verified SHA-256, safely extracted the official onedir build, reported WeasyPrint 70.0 / Pango 1.58.2, rendered a valid PDF, and cleaned up its test destination.

Observed helper validation:

    archive_sha256=ab1151f210b4e6bb7aa7a79e91a67e8ddb760094c107bfda55241b6aaefe7d53
    WeasyPrint version: 70.0
    Pango version: 15802
    WEASYPRINT_FETCH_HELPER_PASS

PASS: Installer 1.6.8's missing-runtime branch was invoked against a scratch target with no tools\weasyprint\runtime directory. It called the installed source helper through the Norm Python environment, verified the same pinned archive, then independently repeated --info and a real HTML-to-PDF render.

Observed installer fetch-path render:

    WeasyPrint version: 70.0
    Pango version: 15802
    WeasyPrint/Pango render smoke passed: 7042 bytes
    INSTALLER_FETCH_PATH_PASS

The public source/runtime ZIP intentionally omits the generated native runtime; the package manifest retains the official upstream URL and SHA-256. Existing valid installed runtime is persistent across source updates, and tools\weasyprint.cmd invokes the same helper for lazy repair.

## Emergency-console behavior

Source inspection verifies:

- internal shutdown signal under resolved state_root;
- about 5 seconds of post-stop visibility for non-prompt operator windows;
- about 10 seconds for Prompt;
- Enter/Ctrl+C immediate close during countdown;
- exact registered Norm helper-tree termination rather than generic Python-process killing.

## Checkpoint reliability

Source inspection verifies:

- unique PID/UUID temp checkpoint names;
- flush + fsync;
- six bounded Path.replace attempts;
- short increasing retry delay;
- final durable in-place fallback after persistent Windows PermissionError.

## Memory scope and schedule

Source inspection verifies:

- scheduled memory maintenance alternates successful regular/full passes;
- failed scheduled runs retain/resume the same recorded mode;
- manual /memory-condense is recent-only;
- /memory-condense -deep is manual-only bounded older-history compaction/validation without hierarchical merging;
- scheduled/manual full sweep the complete date-ordered historical archive;
- memory-maintenance settings are re-read when a pass starts.

## Full-condensation QA and merge safety

Source inspection plus regression verifies:

- default QA batch size: 200 date-ordered compact rows;
- default isolated samples per QA batch: 12;
- default chronological neighboring merge-window maximum: 6;
- QA batches cover the entire archive during a full pass;
- QA batch size does not cap pass scope;
- each sampled reconstruction is isolated from neighboring memories;
- one full pass performs one hierarchical merge level;
- an N-row neighboring window can remain N rows or propose 1..N outputs;
- every source primary ID must appear exactly once in the proposal partition;
- singletons remain their original compact record;
- multi-memory replacements retain full source provenance;
- every constituent represented by an actual merge is reconstructed from the merged record alone;
- a failed proposed merge falls back to original constituent compact records;
- replacement compact rows are upserted and marked validated before superseded original compact rows are deleted.

## Package boundary

These checks validate source/package behavior. They do not assert live health for a particular target machine's Ollama, Redis, PostgreSQL, CUDA, network authority, credentials, or SSH connectivity.
