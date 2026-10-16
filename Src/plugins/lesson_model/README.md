# Norm Lesson Model + Code Memory plugin — skeleton

`lesson_model` is a schema-2 Norm plugin with two related long-term-memory paths: operator-reviewed procedural lessons for future fine-tuning, and versioned code memory for reconstructing source projects from evidence.

## What the current skeleton does

- Creates **lesson candidates** without silently approving them.
- Requires a separate explicit approval call before a lesson can become training eligible.
- Preserves rejection/supersession provenance instead of deleting old lessons.
- Deduplicates equivalent live lessons by normalized SHA-256.
- Stores plugin-owned state in `C:\Norm\state\lesson_model\` by default.
- Exports approved, training-eligible lessons as canonical JSONL.
- Freezes the current eligible lesson set into a dated build manifest such as:
  - `norm:2026-10-06`
  - another same-day build becomes `norm:2026-10-06.2`
  - successful candidates are intended to be promoted later to `norm:latest`.
- Does **not** train, merge weights, call Ollama, or promote a model yet.

## Required conversation contract

The plugin deliberately cannot infer user consent.

Recommended runtime sequence:

1. Norm notices a genuinely reusable procedural lesson.
2. Norm calls `propose_lesson(...)`.
3. Norm shows the returned candidate to the user and asks whether to tag it as a lesson.
4. Only after an explicit affirmative response may Norm call `approve_lesson(...)`.
5. `approve_lesson(..., training_eligible=true)` makes it available to future exports/builds.

Do **not** auto-call `approve_lesson` merely because `propose_lesson` succeeded.

### Keep these categories separate

- **lesson** — generalized procedural behavior potentially useful in weights.
- **fact** — mutable truth; keep in Norm evidence/memory, not model weights.
- **rule/invariant** — deterministic requirement; enforce in runtime code/config.
- **preference** — user-specific behavior; keep in persistent preference/memory unless it is genuinely generalizable.

## Public tools

- `propose_lesson`
- `approve_lesson`
- `reject_lesson`
- `set_training_eligibility`
- `supersede_lesson`
- `get_lesson`
- `list_lessons`
- `export_training_corpus`
- `prepare_model_build`
- `list_model_builds`
- `lesson_model_status`

## Storage

Default:

```text
C:\Norm\state\lesson_model\
├─ lessons.sqlite3
├─ exports\
└─ builds\
```

For testing, `NORM_LESSON_MODEL_STATE` can point at an isolated directory.

The canonical export is intentionally backend-neutral:

```json
{
  "schema": "norm.lesson.v1",
  "lesson_id": "L-20261006-...",
  "category": "tool_use",
  "lesson": "When a size-limit failure occurs, reduce scope before retrying.",
  "rationale": "...",
  "tags": ["retry_strategy", "chunking"],
  "source_refs": ["task:...", "turn:..."],
  "approved_by": "user"
}
```

This is **not yet** SFT/ChatML/DPO data. A future training backend should transform the reviewed lesson set into whichever training representation is appropriate for the selected base model.

## Build preparation

`prepare_model_build(base_model="...")`:

- exports a frozen approved/eligible corpus;
- computes its SHA-256;
- records the exact lesson IDs;
- allocates a dated candidate tag;
- writes a build manifest;
- sets `training_enabled=false`;
- sets `auto_promote=false`.

That keeps the version/provenance machinery usable before the trainer exists.

## Future integration boundary

`src/_trainer.py` defines a private `TrainingBackend` protocol. The later Norm integration can add a reviewed backend for Unsloth/TRL/PEFT/etc. without changing lesson storage.

The intended later pipeline is:

```text
Norm runtime
  -> propose candidate
  -> user approve/edit/reject
  -> approved lesson registry
  -> frozen dated dataset
  -> LoRA/QLoRA backend
  -> norm:YYYY-MM-DD candidate
  -> regression/eval gate
  -> explicit promotion
  -> norm:latest
```

Promotion should remain test-gated. Training success alone must not move `norm:latest`.

## Installation later

When the current Norm work settles, copy the `lesson_model` directory into:

```text
C:\Norm\plugins\lesson_model\
```

The included `plugin.json` is schema-2 and its `sha256` covers the complete `src/` tree using Norm's current path+length+bytes hashing algorithm.

This plugin is now packaged as a built-in source plugin in this integration candidate; installing the package may therefore add/update this plugin through the normal managed-plugin path.


## Code-memory subsystem

The code-memory side intentionally produces **two different artifacts**.

### PostgreSQL: dense raw project memory

PostgreSQL stores the analysis-oriented version:

- project/version identity and source ZIP SHA-256;
- scanned file manifest and per-file SHA-256s where practical;
- runtime-priority order and likely entrypoints;
- architecture/components and runtime/control flow;
- dependencies, configuration, persistence and network behavior;
- implemented, partial, stubbed/dead, and uncertain areas;
- inferred project intent and completeness;
- suggested next actions;
- evidence refs back to exact source chunks;
- uncertainties instead of invented certainty.

This lives in the configured Norm runtime schema as `code_memory_snapshots`. The raw memory is JSONB so later Norm versions can consume more structure without losing older fields.

### README: human-facing project explanation

The README is separate and should be written for the operator/developer:

- what the project is;
- what problem it solves;
- how the architecture fits together;
- setup/run/use instructions supported by the code;
- current completeness/status;
- meaningful limitations;
- sensible next steps.

The README is not the canonical machine memory.

### ZIP analysis flow

```text
source ZIP
   ↓
code_memory_scan_zip()
   ↓
manifest + hashes + runtime priority + likely entrypoints
   ↓
code_memory_next()   ← one bounded source chunk at a time
   ↓
Norm follows runtime/dependencies and accumulates understanding
   ↓
code_memory_save_analysis()
   ↓
code_memory_persist_postgres()
   ↓
raw structured memory in PostgreSQL

same analysis
   ↓
Norm writes human README
   ↓
code_memory_write_readme()
```

`code_memory_next()` deliberately returns one file/chunk per call. Large archives are not sprayed into model context in parallel.

### Recommended raw summary shape

```json
{
  "project_overview": {},
  "architecture": {},
  "entrypoints": [],
  "runtime_flow": [],
  "components": [],
  "dependencies": {},
  "configuration": {},
  "persistence": {},
  "network_interfaces": [],
  "features_complete": [],
  "features_partial": [],
  "features_stubbed_or_dead": [],
  "tests_and_validation": {},
  "intent_inferred": [],
  "known_issues": [],
  "next_actions": [],
  "uncertainties": [],
  "evidence": [
    {
      "claim": "...",
      "refs": ["C-...:src/main.py:0-18000"]
    }
  ]
}
```

The split is deliberate: **README for the human; raw structured memory for PostgreSQL.**

### Versioned self-memory

For Norm itself, scan each release/source ZIP using the actual version:

```text
project_name = Norm
version_label = 0.54.1a
version_label = 0.54.2a
```

Each snapshot records project, version label, and source SHA-256. A later comparison tool can diff the stored manifests and raw summaries between versions rather than rebuilding understanding from scratch.


### Wholesale source retention

For now, every ingested ZIP is retained **wholesale** in plugin-owned storage:

```text
C:\Norm\state\lesson_model\code_memory\sources\<source-sha256>.zip
```

The archive is copied before analysis and SHA-256 verified. Identical archives deduplicate naturally because the content hash is the filename.

This means code memory does **not** require:

- a Git repository;
- a checked-out work tree;
- later access to the original uploaded/downloaded ZIP;
- reconstructing the project from PostgreSQL.

PostgreSQL stores the structured understanding and a pointer/hash for the retained immutable source snapshot. `code_memory_next()` reads future chunks from that retained archive.

If storage becomes a concern later, a retention policy can prune old wholesale snapshots only after confirming that the corresponding PostgreSQL memory remains durable.


## Executable Architecture / Capability Topology

A code-memory scan can also produce two linked topology artifacts:

```text
<project>.codegraph.json   canonical machine-readable graph
<project>.topology.html    standalone interactive user-facing map
```

The CodeGraph is not merely a picture. It is another consumer/producer of the same source evidence used by PostgreSQL memory and README generation.

### Layers

The graph model can carry several layers at once:

- **structural** — files/modules, classes, functions, imports, inheritance;
- **call** — calls and awaits;
- **data-flow** — reads/writes/returns/passed data when statically resolved or semantically annotated;
- **runtime** — reserved for observed execution evidence; a ZIP alone does not fabricate this;
- **capability** — semantic clusters such as retrieval, ranking, persistence, rendering;
- **dependency** — external modules/services and other boundaries;
- **change-impact** — later comparison/blast-radius overlays.

### Connector inspection

Selecting a connector exposes:

- caller and callee;
- source file and line;
- passed argument expressions;
- variable names involved;
- parameter→argument binding when the target signature is known;
- expected response from return annotation/observed static returns;
- semantic data meaning supplied by Norm;
- static-confidence level;
- runtime evidence if a later instrumented run has attached any.

Selecting a node or edge can show direct neighbors, upstream provenance, downstream consequences, or the full connected chain while unrelated nodes fade.

### Static evidence + Norm semantics

`_code_graph.py` provides deterministic evidence first. Python uses AST parsing; JavaScript/TypeScript/Vue/Svelte currently use a deliberately conservative static approximation. Unknown targets become low-confidence external nodes rather than being silently invented.

`code_map_save_semantics()` lets Norm add evidence-backed capability labels, intent, expected I/O meaning and additional data-flow edges without overwriting static evidence.

The standalone HTML contains its graph data and renderer directly. It does not require a Git work tree or a web service to open.

### Public topology tools

- `code_map_build(scan_id)` — build canonical JSON + interactive HTML.
- `code_map_save_semantics(scan_id, semantics)` — add semantic clusters/contracts and rebuild.
- `code_map_get(scan_id)` — retrieve generated artifact metadata.

Future runtime instrumentation can attach observed call counts, timings, exceptions and traces to the same edge IDs rather than creating a second incompatible graph.
