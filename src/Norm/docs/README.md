# Norm

**Norm 0.53.11** is the local assistant/coordinator runtime maintained under `C:\Norm`. It plans bounded work, executes local tools and hot-loaded plugins, persists task/memory state in PostgreSQL, uses Redis for live queues/buffers, and returns normal English/Markdown.

## 2026-10-02 maintenance source checkpoint

Norm remains **0.53.11**, with Installer **1.6.5-unified**. These changes are implemented in the maintenance source; this checkpoint does not claim a rebuilt executable, live promotion, installer payload refresh, or GitHub publication. See [current status](CURRENT_STATUS.md) and [verification evidence](MAINTENANCE_VERIFICATION.md).

### Advisory validation checklist

`record_validations` and `review_validations` share semantic subjects across unrelated tasks. Redis holds the live checklist: `recent_checks` counts observations of the current value in the rolling 24-hour window; `generation_checks` counts all confirmations since that value began. An unchanged value increments its generation and preserves the generation start. A changed value starts at one, resets generation timestamps and recent observations, and queues the old compact generation for PostgreSQL history. Repeated checks remain allowed: no count, reason or status grants or denies permission.

PostgreSQL `global_validations` stores compact current snapshots and `global_validation_generations` stores prior generations. Same-value snapshots are due after 12 hours, written on the next observation; new/changed values checkpoint immediately. Counts may lag Redis, especially during inactivity or outages. A PostgreSQL fallback has an **unknown** live recent count, not a fabricated zero. Redis is not seeded with imaginary recent observations. Completed generations remain pending in Redis until the history write succeeds; losing Redis before a checkpoint can still lose unsnapshotted counts/history.

`config/runtime.json` `validation_pool` defaults: `recent_window_seconds=86400`, `observation_retention_seconds=604800`, `durable_snapshot_interval_seconds=43200`, `reuse_after_checks=3` (advice only), `context_token_budget=2500`, `context_char_budget=10000`, `emergency_row_cap=512`. The token allowance uses a four-characters-per-token estimate; the character limit is deterministic. The old `max_context_items` setting no longer limits context. Omitted rows can be explicitly reviewed by subject.

### Recovery units and write verification

Oversize units return internal answer results, so an evidence-backed finding that no edit is needed can complete without manufacturing a file. Independent step/final verification still checks the evidence. Root artifact requests still require artifacts. Native file writes and plugins reporting a `file_mutation` require a matching read **after the final write**; a successful write alone, pre-write read, missing hash, or mismatched hash cannot verify an artifact. The exact-text writer now supplies mutation/hash metadata to this same path.

The compact durable recovery handoff remains unchanged: immutable scope, completed and unresolved work, authoritative facts, source/database locators, and next action; the parent parks while bounded children work from that note.

## Plugin operation and verification

Each visible direct plugin subfolder is a local capability. Schema-2 plugins keep metadata at the plugin root and executable code under `src/`; public functions defined in the declared `src/main.py` entrypoint become native tools while helper modules remain private implementation. Legacy third-party plugins may still use the older layout. The nine first-party plugins now carry a README, `plugin.json`, and `src/`.

| Plugin | Purpose |
|---|---|
| [backup](../plugins/backup/README.md) | Portable source or sensitive full-state backup |
| [file_read](../plugins/file_read/README.md) | Bounded text/byte reads under the file-access policy |
| [postgres_pool](../plugins/postgres_pool/README.md) | Single bounded PostgreSQL connection pool tool with approved named connections |
| [rotor5_cipher](../plugins/rotor5_cipher/README.md) | Independent reversible Rotor5 transform |
| [soft_delete](../plugins/soft_delete/README.md) | Reversible trash, restoration and reconciliation |
| [stegosplit_key](../plugins/stegosplit_key/README.md) | Password/map-key protected key-pair prototype |
| [stegosplit_message](../plugins/stegosplit_message/README.md) | Two-PNG authenticated message carrier |
| [verbatim_lines](../plugins/verbatim_lines/README.md) | Exact UTF-8 append/insert and private stdin CLI |
| [vision_parse](../plugins/vision_parse/README.md) | Rendered PDF vision with text-layer reconciliation |

`plugin.json` schema 2 declares the plugin name, version, release date, `src/main.py` injection point, description/capabilities, and one SHA-256 for the complete `src/` tree. The tree hash is deterministic over each source file's relative path and exact bytes, so editing or renaming anything under `src/` changes the identity hash. README and other root metadata do not affect the source SHA.

At startup Norm recalculates each schema-2 source tree hash before loading it. A matching SHA means the declared code build is unchanged; a mismatch prevents the candidate from loading. Norm generates `plugins/.registry.json` from the plugin folders actually present on that installation and refreshes it as plugins are hot-swapped; the registry is disposable generated state and is not shipped as package authority. A damaged or failed update retains the in-process last-known-good plugin when one exists.

Normal installer updates synchronize the package-managed built-ins while preserving unrelated local plugin folders. A full-backup restore intentionally restores the captured plugin tree. Stage complete plugin updates together to avoid transient refresh errors. Explicit export declarations and enforcing canonical allowed roots in `verbatim_lines` remain deferred; see [the backlog](FUTURE_IMPLEMENTATION_NOTES.md).

## Canonical layout

```text
C:\Norm\
├─ core\                 runtime source + norm.exe
├─ config\               settings/runtime configuration
├─ docs\                 maintained Norm documentation
├─ plugins\              hot-swappable local capabilities
├─ .ssh\                 Norm-specific SSH keys/known_hosts (local, private)
├─ .venv\                reusable Python environment (generated, not backed up)
├─ tools\                build/operator/maintenance helpers
├─ logs\                 runtime logs
├─ state\                local runtime state
└─ (first-party exact writer is plugins\verbatim_lines\)

%USERPROFILE%\Documents\Norm\
├─ workspace\            durable generated artifacts and working files
└─ temp\                 disposable scratch/recovery material
   ├─ tasks\              task-scoped temporary work
   ├─ scratch\            one-off convenience scripts
   └─ recovery\           SOS/status-context recovery material
```

The installer treats `.venv`, `.ssh`, `plugins`, `logs`, and `state` as persistent local state during normal base updates. Package-managed files are synchronized in place: changed files are replaced and package-owned files removed from a newer base are deleted. A compatible `.venv` is reused.

## Large-source and task-storage model

Native file reads distinguish source size from processing/output size. A source file may be arbitrarily large within the backing filesystem. Text is streamed through a 24 MiB processing buffer, while one `read_file` result is bounded to 384 KiB and supplies byte/line continuation cursors. Large sources are not fully hashed unless explicitly requested.

A task may process up to 3 GiB in one pass and own up to 54 GiB of temporary working storage. Reaching the per-pass ceiling writes a compact continuation ZIP and parks the same task for manual `/resume-task`; resume preserves lifetime progress and resets only the current pass allowance. Internal extraction/summary Markdown uses automatically rotated 5 MiB chunks. Task-local reproducible media/image derivatives are temporary; after verified completion Norm retains their lineage/recipes and useful internal notes but removes the reproducible bytes.

Human-facing completed replies are capped at 384 KiB. The full terminal summary remains durable in PostgreSQL, and oversized complete reply text is also saved under `workspace\large-responses`.

## Runtime services

`config\settings.ini` is the canonical operator configuration. `[paths]` records the installed `runtime_root` (normally `C:\Norm`), and `[network]` defines service topology. Current defaults are Ollama `11434`, Norm HTTP/chat `12543`, activity/control `8766`, Redis `6379`, and PostgreSQL `25434`. Credentials are loaded from the configured external secrets file and are never included in the portable source package.

## Tool and plugin model

The runtime lock includes `cryptography 50.0.2`/`cffi 2.0.0`, and `tools\build_norm.py` explicitly collects those packages into the one-file executable so dynamic plugins can use ChaCha20-Poly1305 from the frozen process.

`core\norm_runtime\plugin_manager.py` automatically rescans `C:\Norm\plugins` before native tool schema use/dispatch. Public functions in non-underscore Python files become namespaced native tools; helper files/functions beginning with `_` stay private. Multi-file plugins and sibling imports are supported. If a changed plugin fails to load, the last-known-good hydrated version remains active and `.registry.json` records the refresh error.

First-party plugins currently ship under `plugins\backup`, `plugins\verbatim_lines`, `plugins\stegosplit_key`, `plugins\stegosplit_message`, `plugins\rotor5_cipher`, `plugins\vision_parse`, `plugins\file_read`, and `plugins\soft_delete`. The StegoSplit plugins bundle their Python implementation so a `.venv` rebuild no longer depends on an external editable checkout. `stegosplit_message` is the two-image authenticated carrier; `rotor5_cipher` is an independent optional pre-encoding layer; `stegosplit_key` remains the password/map-key protected 256-bit key prototype.

The backup plugin supports two package types. `/backup` creates a portable installer/source ZIP without private state. `/backup full` creates a sensitive full-state ZIP containing source/runtime, docs, all plugins, `.ssh`, configured secrets, external workspace, selected recovery/log/state, PostgreSQL, and environment rebuild metadata. `.venv` itself is intentionally omitted. Older backup aliases remain accepted for compatibility but are not advertised in help.

## Recovery-state hygiene and work provenance

The weekly maintenance cycle and manual `/memory-condense` / `/memory-condense -full` passes first clean recovery plumbing. Fully terminal verified trees lose obsolete recovery notes. Stale nonterminal trees are never deleted merely because they are old: Norm requires no live Redis membership and no suppression, summarizes the surviving task/recovery state into compact `task_history`, replay-validates that compact record, and only then prunes the stale task tree. Orphan archive rows are removed only when validated history already covers them.

Queued work keeps its existing first-class `request_type` and `prompt_origin`. Only real prompt-interface input is treated as user-authored. Planner steps, child tasks, recovery work, verification, and maintenance are explicitly labelled as Norm-internal instructions in model envelopes, while `original_user_prompt` remains the immutable user request carried through descendants.

## Mixed-turn interpretation and Ingrained Details

A user turn is stored verbatim, but Norm no longer assumes that every meaningful sentence belongs to the same executable task. Before planning, one structured interpretation separates the **primary task request** from **Ingrained Details**: meaningful side information such as terminology corrections, reusable preferences, future work, project intentions, or corrections to prior context. Details required to understand the primary task stay in that task; reusable details may both influence the current task and be stored durably.

Confident Ingrained Details go directly to their final home using the existing durable memory types (`fact`, `preference`, `decision`, `constraint`, `task`, `assumption`) or current-task context. A confident correction may supersede the specific prior memory it replaces. Norm does not create a permanent intermediate ledger for already-resolved details.

Meaningful details whose correct destination is genuinely unclear are temporarily stored in PostgreSQL `unresolved_bits`. `unresolved_bit_trials` records only the experiments performed while a bit is unresolved. On later real tasks Norm test-fits at most a few plausible bits, recording the task domain and whether the bit applied, found a durable home, remained ambiguous, or proved irrelevant. A resolved bit is written to its final home and its temporary unresolved row is deleted; trial rows disappear by cascade. By default a singly-mentioned bit with no useful trial may be garbage-collected after at least 15 genuine `not_relevant` trials spanning at least 3 task domains. Repeated user mention protects it from that automatic discard rule. Recency alone is never the deletion criterion.

Background-memory condensation also sees the current unresolved-bit state and its aggregate trial evidence, allowing useful uncertain context to survive compactly without pretending it has already become a curated memory.

Queue status preserves the same provenance split. DB3 `prompt_id` is carried into the durable task plan, and `/queue` joins the ingress row to the newest matching running task/child so it can show the actual task title and current step. The beginning of the raw mixed user message remains provenance, not a false description of what the worker is currently doing.

## External workspace and temp policy

Use `%USERPROFILE%\Documents\Norm\workspace` for artifacts or working files that should survive normal maintenance. Use `%USERPROFILE%\Documents\Norm\temp` for disposable helper scripts, scratch/intermediate files, blocked-write staging, and recovery output. Task-scoped temp is removed only after durable terminal verification. Older scratch is age-cleaned by maintenance. SOS/recovery files are retained unless their referenced tasks are durably terminal.

`temp\recovery\SOS.md` is the current emergency recovery snapshot path. PostgreSQL remains the durable source of truth; Redis working/model buffers are best-effort interrupted-step context.

## Operator controls

The GUI recognizes `help`/`/help`, `/about`, `/status`, `/status/busy`, `/queue-full`, `/multi`, `/repeat-submission`, `/repeat-answer`, suppression/resume controls, `/backup`, `/backup full`, `/memory-condense`, `/memory-condense -full`, `/new [name]`, `/thread-list`, `/thread-resume <name>`, graceful shutdown, and emergency stop controls. `/memory-condense` incrementally refreshes the consolidated PostgreSQL background-memory snapshot; `-full` rebuilds that snapshot from the complete surviving source set. Both are maintenance requests handled by the existing worker when idle and never create a user task. The stronger destructive curated-memory `/condense-memories` design remains intentionally separate and unimplemented. Legacy aliases remain accepted but are intentionally omitted from visible help. `GET /status/busy` is the authoritative live busy probe; `/status-context` builds a fresh handoff from maintained docs plus live runtime/Redis/PostgreSQL evidence.

## Build/update

`tools\build_norm.py` is the repeatable PyInstaller path. Its analysis cache is reusable under `state\build-cache\pyinstaller`; use `--clean` only for an explicit cold rebuild. `package-manifest.json` defines the reusable installer contract. Normal updates should use the reusable Norm installer rather than deleting `C:\Norm`: it mirrors package-owned source files, preserves local/persistent state, reuses the venv when compatible, installs only missing/changed dependencies, and optionally rebuilds `core\norm.exe`. Installer 1.6.5 binds one exact source payload by filename and SHA-256. Its builder reads this package's requirements lock and can create a derived payload using either the locked version or the newest eligible stable/release-candidate version for each package; alpha, beta, and dev builds are excluded. The package schema remains 1.

## Documentation

- `docs\README.md` — operator overview.
- `docs\CURRENT_STATUS.md` — current source/layout facts and known limitations.
- `docs\RELEASE_NOTES.md` — promoted/source-release delta ledger.
- `docs\DEVELOPMENT_NOTES.md` — detailed engineering chronology and lessons.
- `docs\FUTURE_IMPLEMENTATION_NOTES.md` — active backlog/design notebook only.

Historical notes may mention earlier `app\` and Documents-root layouts. Those are historical records, not current paths.


## 0.53.1 operator console launcher

`norm.exe --service` is launched headlessly with Windows `CREATE_NO_WINDOW` plus a hidden startup window, so the service process no longer leaves an inert console on the desktop. `Run-Norm.bat` still opens the three operator surfaces, but **Norm Runtime** prefers a separate Windows Terminal (`wt.exe`) window for the more compact/refined terminal host and falls back to the classic console when Windows Terminal is unavailable. Norm Prompt and Norm Replies keep their existing explicit console behavior. The Runtime stream already exits when `norm.exe` exits, so shutting Norm down also closes that terminal naturally.

## 0.52.6 proportional plan verification

The independent plan verifier now rejects only blocking execution defects. Cohesive bounded steps may contain multiple tightly coupled implementation parts when their roles and verification are explicit. Advisory organization/style concerns no longer veto a plan, and the verifier is instructed not to invent hypothetical implementation failures when the plan explicitly inspects/reuses an existing mechanism or verifies the invariant during execution. Repair cycles must make the smallest material correction instead of mechanically splitting cohesive work or returning the same rejected plan.

## 0.52.4 canonical prompt ingress

Local Rich-console prompts and SSH `P` prompts now share one Redis DB3 ingress stream/group/thread key and the same core dispatcher implementation. Both submit to project `default`. The dispatcher establishes an explicit current thread before chat submission and only acknowledges a normal prompt after `/api/chat` returns a durable `task_id`; HTTP 200 without task creation is parked as a visible protocol violation rather than silently discarded.

Startup ownership is fail-closed: the activity API, worker, and chat API are all released if any later startup stage fails, so a bounded PostgreSQL retry cannot collide with a leaked `:8766` listener. Normal startup no longer performs a PostgreSQL preflight outside the retry loop, generated PostgreSQL conninfo uses a 5-second connection timeout, and plugin hydration/execution is serialized process-wide around Python's global import/stdio state.

## 0.52.3 service-start stability

`Run-Norm.bat` launches `norm.exe --service`. In service mode, stray Windows Ctrl+C/Ctrl-Break events are logged and ignored instead of being interpreted as operator shutdown. Canonical shutdown remains `/shutdown`, `/shutdown now`, `/stop-all`, or `/stop-all now` through the control API. Directly running `norm.exe` without `--service` retains normal KeyboardInterrupt behavior for debugging.

## 0.52.2 operator/startup refinements

The activity/control API starts before PostgreSQL schema initialization and reports an explicit `initializing` phase until the worker/chat runtime is ready. This keeps `/status/busy` and shutdown/stop controls reachable during bounded schema waits without treating a half-started runtime as healthy. Conversation consoles support queue-ordered thread controls: `/new [name]`, `/thread-list`, and `/thread-resume <name-or-id>`.
`/flush-suppressed` removes suppressed task records and their parked GUI delivery records together; active socket dispatches retain only the hidden retry-block tombstone needed to prevent a late connection reset from recreating the prompt.


## 0.53.1 aiohttp transport

Chat and activity/control HTTP are served by pinned aiohttp 3.14.3 on dedicated asyncio loops; on Windows those socket-only server threads explicitly use SelectorEventLoop to avoid Proactor accept-loop listener loss. Existing coordinator lifecycle semantics remain compatible; blocking runtime callbacks are offloaded with asyncio.to_thread. SSE disconnects are benign transport events.

### PDF semantic reading
For PDF study/extraction, use `vision_parse` when exact wording, names, dates, columns, or classifications matter. It combines the PDF text layer with a rendered-page vision pass; the rendered page wins when extraction is garbled. Calls cover up to four pages and return `next_page` for resumable traversal. Raw extraction is evidence, not authority, and Norm should not generate typo-regex variants merely to chase corrupted OCR/encoding.
