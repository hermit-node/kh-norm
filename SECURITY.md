# Security

Never commit local imprints, .env files, passwords, API/OAuth/Tailscale tokens, SSH private keys, private certificates, PostgreSQL dumps containing runtime/user state, logs, workspace content or full-state backups.

The public tree contains topology-neutral source/configuration only. Runtime Redis/PostgreSQL data can contain sensitive tool results and user context and must remain local.

The frozen WeasyPrint runtime is reproducible third-party binary material and is intentionally ignored by Git.

Run this before publication:

    python tools/public_release_guard.py --tree .
