## Norm 0.55.3 clean compiled portable source

Compatible with Norm Installer 1.6.12, Windows Python 3.14 x64. Norm core is portable Python source that the installer compiles into norm.exe. Proprietary StegoSplit internals are precompiled native .pyd modules, accessed through readable plugin src/main.py adapters. StegoSplit Auth reuses Message's native V3 modules instead of duplicating them. Each plugin includes a developer README.md and a user-facing help.txt available with /help stegosplit-message, /help stegosplit-auth or /help stegosplit-key. Master and credential images are NEVER distributed in this package. Filename-derived passwords and hidden PNG file selection are convenience/obscurity only. Native SSH or a network-facing authentication endpoint are NOT provided.

# Norm 0.55.3 — Compiled StegoSplit portable-source package

This archive is compatible with Norm Installer **1.6.12-unified**, with package schema 1, `package_type: portable-source`, the installer-owned `norm.exe` build (no legacy build script required), and a matching companion SHA-256 file. Norm's core is distributed as source to be built by the installer. The three StegoSplit plugins distribute their private implementations only as Windows CPython 3.14 x64 native `.pyd` modules; readable `src/main.py` adapters expose the stable Norm tool interface.

- `stegosplit_message` **0.4.3**: V3 paired-message carrier, paired SSH/SHA/MD5 identifier matching, and random 256-bit credential creation/unlock.
- `stegosplit_auth` **0.1.0**: distinct fixed-master/many-client signed image bearer authentication. Optional side plugin; disabled until enrolled/configured.
- `stegosplit_key` **0.2.0a1**: legacy paired key, rotation and cover reconstruction methods.
- The existing compiled V3 carrier engine remains **0.4.0** internally; its native module bytes were not modified.
- `package-manifest.json` and every plugin `plugin.json` carry the matching current versions and schema-2 hashes of the exact shipped source/native trees.
- `plugins/.registry.json` is runtime-generated and deliberately excluded. No private authentication Python implementation, real master image, private client images, SQLite authentication state, or configured secrets are included.

**Verification:** The exact 1.6.12 installer accepted the compiled ZIP in `--validate-only` mode. Windows CPython 3.14 smoke tests passed for Message paired secret unlock, Auth issuance/verify/revoke, Key imports, and 18 plugin identities. Full deployment and SSH-service integration remain untested/not implemented.

**Help:** `README.md` contains operator installation and user procedures. `plugins/stegosplit_message/README.md`, `plugins/stegosplit_auth/README.md`, and `plugins/stegosplit_key/README.md` explain interfaces for Norm; each plugin's `help.txt` summarizes user-facing operations. Complete historical development detail is preserved in `docs/RELEASE_NOTES.md`.

**Security note:** A signed client PNG is a copyable bearer credential. A filename used as a password is exposed with the file. The master issuer seed is in server-master PNG metadata, so secure the master and never publish operational credentials. Auth is not native OpenSSH.
