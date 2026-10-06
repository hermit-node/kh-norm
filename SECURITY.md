# Security

The public repository contains package code and distributable installer artifacts only.

Never commit local imprints, .env/secret files, passwords/tokens, SSH private keys, runtime PostgreSQL/Redis state, logs, workspace content, full-state backups, or downloaded machine-local WeasyPrint runtime output.

src/Norm/tools/weasyprint/runtime/ is generated locally from a package-pinned official upstream archive and is intentionally Git-ignored.

Run python tools/public_release_guard.py --tree . before publishing.
