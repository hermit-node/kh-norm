# voice_profile

First-party Norm plugin for deriving inspectable AI voice **and persona** profiles from a PDF or
already-extracted text corpus.

## What it learns

The profile measures and synthesizes recurring tone, cadence, sentence/paragraph structure,
vocabulary, register, reasoning habits, information density, and apparent domain literacy.
It also selects a small set of especially rich, source/page-labeled excerpts as few-shot
**style anchors**.

Style is kept separate from factual authority. Corpus-dependent factual claims remain
retrieval-based through `retrieve_context` / `compose_context`.


## User voice from chat history

`build_from_chat_history` rebuilds a voice-only profile from Norm's durable `user`-role conversation messages. Assistant/system messages are excluded, existing non-chat corpus material is preserved, and the chat-derived corpus is replaced on refresh rather than appended forever. It never creates or changes the operator-authored user persona in `config/profiles.json`.

## PDF handling

`build_from_pdf_folder` reuses Norm's existing `vision_parse` plugin. Native PDF text is used
when healthy; rendered-page vision is used for weak/suspect pages. When vision_parse returns
`TRANSCRIPTION` plus `SEMANTIC NOTES`, only `TRANSCRIPTION` enters the voice corpus so Norm's
own generated notes do not recursively shape the learned voice.

Corpus paths are hard-locked to Norm's configured readable roots even when the general
file-access enforcement switches are disabled.

## Prompt-injection boundary

Every quoted corpus excerpt is emitted inside `SOURCE_QUOTE` markers and is explicitly
untrusted data. Instructions, role changes, or tool directives occurring inside source
documents are analyzed as text and are not instructions to Norm.

## Activation

Building a profile does not activate it. `activate_profile` writes a bounded style-only active
prompt under `state/voice_profiles`. Norm's normal conversation path reads this prompt on every
request, so activation/deactivation takes effect without rebuilding model weights.

Stored profile artifacts:

- `corpus.jsonl`
- `voice_profile.json`
- `voice_prompt.txt`
- `build_report.json`
- global `active.json` / `active_prompt.txt` only while a profile is active

Version 0.3.0 adds native schema-2 packaging, current FileAccessPolicy hard-locking,
vision_parse source-identity verification, transcription-only ingestion, and active-profile
conversation integration.

Corpus reads use the shared file-access policy and can be extended with `[file_access_overrides] voice_profile.read_add`; profile state itself remains under the internal `state_root`.


## Persona profile

Version 0.4.0 also synthesizes `persona_profile.json` and `persona_prompt.txt` from the same
corpus. Persona evidence is treated as untrusted corpus data: it can enrich a role selected in
`config/profiles.json`, but it cannot override operator-authored direct persona or runtime/tool
instructions. `persona_summary` and `render_persona_prompt` expose the generated profile.

## Direct operator commands

The Norm Prompt console implements direct `/voice-*` commands without submitting a
Redis prompt/task. Run `/help voice-profiler` to inspect all supported operations,
including text-only PDF build, inspection, idempotent profile activation, and deletion.
The commands verify the plugin source identity before executing it. Activating an
already-current prompt is a no-op; an edited `voice_profile.json` causes regeneration
and updated active SHA.

## PDF extraction policy

`extraction_mode="auto"` delegates directly to `vision_parse(mode="hybrid")`. The shared PDF reader owns grayscale pixel-coverage assessment and vision fallback; the voice-profile module does not make independent text-length/quality decisions. Explicit `text`, `vision`, and `hybrid` are forwarded unchanged.

## 0.4.2 distinctive voice and testing

The model-based synthesizer now asks for distinctive regional/sociolect/idiomatic
constructions rather than polishing them into generic professional descriptions.
Operator-authored `state/voice_profiles/NAME/style_override.txt` takes precedence
over generic synthesis language, survives corpus rebuilds, and is included in the
compiled active prompt (and SHA). `/voice-tune` manages this override, while
`/voice-test` (operator-only, not a model-facing tool) compares an unconditioned local model response to one with the
selected profile. Direct conversation and queued N2 worker steps consume the same
hash-verified active prompt. Neither route trains model weights.

## 0.4.3 Cockney compiler (r25)

Cockney-specific PDF metadata exclusion, dialect-aware anchor selection, balanced source volumes and unsanitized model synthesis. Compiled prompts also forbid copying memorable source lines and limit unnecessary length for casual questions. Other profiles use the original selection algorithm. State-backed corpus PDF reads still need an explicit operator-configured voice_profile.read_add folder. See docs/R25_COCKNEY_VOICE_COMPILER.md.
