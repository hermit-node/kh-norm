# Norm

Norm is a persistent, self-hosted Windows AI-agent runtime built around local Ollama inference, PostgreSQL durable state, Redis live coordination, controlled tools, resumable work, and hot-loaded Python plugins.

Current public release: Norm 0.53.19 / Installer 1.6.8-unified.

## Install / update

The public repository is the complete distributable release tree. It intentionally contains both browsable source and the exact portable-source ZIP consumed by the installer.

1. Keep the repository/release files together.
2. Run `python Norm-Installer.py` (Python and Tkinter required).
3. Select/install/update the Norm target.
4. Installer 1.6.8 preserves local state/configuration and a valid native WeasyPrint runtime.
5. If WeasyPrint/Pango is absent or stale, the installer downloads the package-pinned official WeasyPrint 70.0 Windows runtime, verifies SHA-256, and proves it with a real PDF render.

No MSYS2/Pango compilation is required on the target.

## Repository layout

    Norm-Installer.py
    installer_environment.py
    norm-imprint.example.json
    Norm-0.53.19-portable-source.zip
    Norm-0.53.19-portable-source.zip.sha256
    SHA256SUMS.txt

    src/
      Norm/                       exact exploded contents of the portable-source ZIP

    tools/public_release_guard.py
    tests/test_public_release.py

The portable-source ZIP and src/Norm are byte-verified representations of the same source payload. The ZIP simply adds the Norm-0.53.19/ archive prefix required by the installer.

## Local/private state

There is no separate private code edition. The public release is the canonical Norm code.

Machine-local state remains outside Git/public release: credentials, local imprints, PostgreSQL/Redis runtime data, .ssh, logs, workspace, state, generated .venv, compiled core/norm.exe, and the downloaded src/Norm/tools/weasyprint/runtime tree.

## Maintenance controls

Scheduled regular maintenance is intentionally a tight working-memory synthesis rather than a rehash of every recent record. It is visible in /status/busy, /queue, and /queue-full.

/suppress-task durably parks active scheduled maintenance when no user task is ahead of it and cancels the active model generation.

/resume-task maintenance explicitly resumes a suppressed or requires_attention maintenance checkpoint.

A deterministic model output-budget failure parks scheduled maintenance instead of automatically retrying forever.

## Public web

Norm can search the public web/news and read page/article text through native web_search and web_fetch tools. General search and news discovery use structured Bing RSS. Fetch is direct-first, with a configurable reader fallback for anti-bot/JavaScript shells.

Public-web access does not open the private network: localhost, private/link-local/non-global addresses, Tailscale 100.64.0.0/10, *.ts.net, unsafe redirects, URL credentials, non-HTTP(S) schemes, and non-80/443 ports remain blocked. Web content is explicitly untrusted external evidence and stays inside the normal N1/Redis verification workflow.

## Models

Norm always boots on canonical Ollama model norm. /switch-model lists installed Ollama models and supports session-only numbered/name switching after a successful candidate probe. Restarting Norm returns to norm.

## Documentation

Current implementation/runtime documentation is under src/Norm/docs/. src/Norm/docs/RELEASE_NOTES.md is the maintained historical ledger.

## Source release packaging

This publication is reconstructed from the saved public source snapshot. The portable-source ZIP is newly packaged from `src/Norm`; its checksum describes this download, not the historical binary release. The prebuilt installer EXE is not included; run the supplied Python installer source.
