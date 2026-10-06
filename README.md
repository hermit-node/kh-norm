# Norm

A persistent, self-hosted Windows AI-agent runtime built around local Ollama inference, PostgreSQL durable state, Redis live coordination, controlled tools, resumable work, and hot-loaded Python plugins.

Current public source: Norm 0.53.17 / Installer 1.6.7.

## Current architecture

N2 performs normal reasoning and work. N1 gates model-requested tools, verified-result reuse/check-in, and repeated reasoning/tool loops. PostgreSQL is the durable authority; Redis is the live coordination/evidence layer.

## Memory maintenance

Norm separates three operations.

- /memory-condense: recent-only, default 14-day window, validating every compact record it produces.
- /memory-condense -deep: manual bounded older-history compaction/validation without hierarchical merging.
- /memory-condense -full: sweeps the complete date-ordered historical archive.

Full-mode defaults in config/runtime.json are:

    deep_history_full_batch_rows = 200
    deep_history_full_samples_per_batch = 12
    deep_history_full_merge_max_records = 6

The 200-row size is a QA grouping, not a pass limit. Every 200 dated compact rows contributes up to 12 isolated reconstruction samples. Each replay gets up to 4,800 output tokens per continuation segment and up to four segments, then is discarded.

After all QA batches pass, one hierarchical merge level examines chronological neighboring windows of up to six compact rows. Unrelated neighbors remain separate. Related/redundant subsets can reduce to 1..N replacement rows. Every constituent represented by an actual merge must reconstruct successfully from the replacement before superseded originals are deleted.

Scheduled maintenance alternates successful regular -> full -> regular -> full passes. Interrupted scheduled work resumes the same mode.

## Session model switching

Norm always starts on canonical model norm. /switch-model asks the live Ollama API for installed models, supports numbered or exact-name selection, probes the candidate on every distinct live Ollama endpoint before commit, and changes all live model clients together only while Norm is idle. Switching is session-only; restart returns to norm.

## Operator help

docs/help_menu.txt is the single operator command-list authority. Both prompt frontends read it at help or /help time instead of embedding duplicate Python print menus.

## WeasyPrint and Pango

The release installer bundles the official WeasyPrint 70.0 Windows onedir runtime, verified with Pango 1.58.2.

Git intentionally does not track that frozen third-party runtime. Upstream license/source metadata is retained under src/Norm/tools/weasyprint and the repo includes:

    python src/Norm/tools/fetch_weasyprint_runtime.py

The helper downloads the official archive, verifies SHA-256, and reconstructs src/Norm/tools/weasyprint/runtime. That directory is gitignored.

## Repository layout

    src/Norm/                       runtime source, docs, plugins and tests
    Norm-Installer.py               readable installer source
    installer_environment.py        installer environment helper
    norm-imprint.example.json       generic topology/config example
    tools/public_release_guard.py   publication scanner
    tools/build_source_package.py   portable-source build helper
    tests/test_public_release.py    public-boundary regression

Generated installer executables, release/source ZIPs, local native runtime files, runtime state, secrets, SSH material, logs, workspaces, local imprints and build caches are intentionally excluded from Git.

Detailed current runtime documentation lives under src/Norm/docs. Historical shipped changes live only in src/Norm/docs/RELEASE_NOTES.md.
