# Norm Future Implementation Notes

## Persistent local-file knowledge references

Add a durable local knowledge-source layer so PostgreSQL can reference files on local storage without copying the full source text into memory.

Target workflow:
- User can place reference files (for example, two Yakuza walkthrough `.txt` files) in an approved local folder and tell Norm they should be used as reference material.
- Norm scans/indexes them once and stores persistent PostgreSQL metadata: stable `source_id`, canonical path, hash, modified time, title/type/domain/subject, tags/aliases, priority, summary, and status.
- Store a section/table-of-contents index with headings, line ranges, summaries, keywords/entities, and retrieval/search data so Norm can fetch only the relevant section from disk.
- The filesystem remains the source of truth; PostgreSQL is the durable map/index. At answer time, Norm should read the referenced file section rather than rely only on an old summary.
- Local source records must participate in the same proximity-based working-memory retrieval path as messages, notes, and memories. A relevant hit should inject a compact machine-oriented file reference into the live prompt, e.g. source ID, relevance, path, section, line range, and priority.
- Support source priorities such as `local_authoritative`, `local_preferred`, and `reference`. For `local_preferred`, consult matching local sources before general web search.
- Detect file changes using SHA-256/mtime and re-index changed files. If a file disappears, mark the source `missing` rather than silently deleting its memory record. Track relocation by stable source identity/hash rather than path alone.
- When a section successfully answers a task, persist/reinforce that association so semantically similar future tasks route to it more strongly.

Example desired path: prompt -> coordinator cleanup/classification -> proximity retrieval over PostgreSQL memories + local-source index -> load relevant file section(s) from HDD -> Norm reasoning -> web search only if local evidence is insufficient.

## Dedicated coordinator step-result verification (future)

The live worker already has structured verification on applicable result paths, but intermediate chained steps are not yet independently gated by a separate coordinator. The future dedicated coordinator should perform a fast independent pass/fail check after every worker step before marking that step trusted or allowing dependent steps to proceed.

Required behavior:
- Compare the returned step result against that step's explicit verification requirement and requested scope.
- Return/store a compact verdict such as `PASS` or `FAIL` plus a short explanation of why.
- A non-empty model response is not enough to count as success.
- If the result is incomplete, contradictory, off-scope, or fails to satisfy required fields/calculations/comparisons, mark it `FAIL` and retry or repair that same step instead of advancing the chain.
- Preserve the failed result and verifier explanation in PostgreSQL for debugging/training, but do not treat it as trusted working memory for later steps.
- Prefer a quick/cheap verifier path so this check adds minimal latency; the verifier does not need to redo the entire task, only determine whether the bounded step actually satisfied its contract.

Mahjong test case that motivated this: a step whose requirement demanded comparison of 2-3 candidate discards with shanten, ukeire, and risk returned only `I need to correct an error in my analysis.` The runtime still marked it completed. The future coordinator should reject that immediately with a concise explanation and rerun/repair the step.

## Evolving user communication / semantic profile

Create a PostgreSQL-backed profile derived from the user's own inputs so Norm develops an evolving working model of how the user communicates, rather than treating every prompt as isolated prose.

Desired behavior:
- Persist analyzable features from user inputs and periodically synthesize them into a compact working profile covering communication style, tone, prose, semantics, recurring shorthand, dialectic tics, preferred framing, and characteristic sentence/argument structure.
- Track both stable patterns and context-specific patterns (for example technical work, market analysis, casual conversation, corrections, or rapid live interaction) so one mode does not contaminate every other mode.
- Record expected interpretations of recurring phrases, abbreviations, implied references, unusual word choices, spelling/semantic substitutions, and other personal language conventions that help the coordinator recover intended meaning.
- When a new input materially deviates from the established profile, store the deviation rather than immediately rewriting the profile around one anomalous message. Repeated deviations should gradually update the expected pattern.
- Keep evidence/provenance for profile claims: source message IDs, first/last seen timestamps, frequency, confidence, contexts, and examples. Profile summaries should be regenerable from the underlying observations.
- Use recency and repetition weighting so the profile can evolve over time while older habits decay rather than disappearing abruptly.
- Distinguish literal user meaning from inferred communication habits. The profile should help interpret ambiguous wording and improve coordinator cleanup, but it must never override an explicit current instruction.
- Capture corrections as especially strong learning signals: when the user says Norm misunderstood a phrase, semantic relationship, tone, or intended distinction, update the relevant profile expectation and retain what changed.
- Track expectation/deviation pairs so Norm can learn not only "how the user usually writes" but where its own interpretation repeatedly diverges from the user's intended semantics.
- Feed only the most relevant compact slices of this profile into the live prompt based on task/context proximity; do not dump the entire user model into every request.

Possible PostgreSQL design: raw/derived `user_input_observations`, synthesized `communication_profile_items`, context-specific variants, `interpretation_corrections`, and `profile_deviations`, with confidence/version/history rather than destructive overwrite.

Desired path: user input -> coordinator normalization/classification -> compare against communication profile -> note meaningful deviations/corrections -> retrieve relevant profile slice -> build clarified Norm prompt -> after task, update observations and periodically re-synthesize the durable profile.
