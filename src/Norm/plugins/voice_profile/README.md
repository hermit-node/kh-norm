# voice_profile

First-party Norm plugin for deriving an inspectable AI writing/knowledge voice from a PDF or
already-extracted text corpus.

## What it learns

The profile measures and synthesizes recurring tone, cadence, sentence/paragraph structure,
vocabulary, register, reasoning habits, information density, and apparent domain literacy.
It also selects a small set of especially rich, source/page-labeled excerpts as few-shot
**style anchors**.

Style is kept separate from factual authority. Corpus-dependent factual claims remain
retrieval-based through `retrieve_context` / `compose_context`.

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
