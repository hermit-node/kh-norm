# Norm 0.55.3 — Installer-ready portable source

Norm is a Windows local Ollama/Redis/PostgreSQL agent runtime with persistent task queues, operator controls, history maintenance, and native plugins. The core Norm code remains portable Python source; StegoSplit internals are compiled.

## Install or upgrade

Use the working **Norm Installer 1.6.12** with **Norm-0.55.3-portable-source.zip** alongside its matching `.sha256` file. The installer reads `package-manifest.json` (package schema 1, type `portable-source`), installs/manages the source tree, and builds `core/norm.exe` using `tools/build_norm.py` if compilation is selected. This ZIP is not a prebuilt Norm EXE. Package discovery and non-destructive installer validation pass; a complete live installation/upgrade has not been performed.

## Compiled StegoSplit plugins

| Plugin | Version | Native code | Public adapter |
|---|---|---|---|
| StegoSplit Message | 0.4.3 | 12 Windows CPython 3.14 x64 .pyd modules, including V3, paired identifiers, and 256-bit credentials | `plugins/stegosplit_message/src/main.py` |
| StegoSplit Auth | 0.1.0 | 11 Windows CPython 3.14 x64 .pyd modules for fixed-master signed client PNG authentication and local GUI/admin utilities | `plugins/stegosplit_auth/src/main.py` |
| StegoSplit Key | 0.2.0a1 | 3 Windows CPython 3.14 x64 .pyd modules | `plugins/stegosplit_key/src/main.py` |

All proprietary StegoSplit implementation modules are compiled. Their corresponding Python implementations are **not** included. The adapter files remain human-readable so Norm can discover its tool endpoint names, argument types, and return values. Each plugin has a public README and help.txt. The private developer source remains separately available and must not be committed to a public repository.

### How to use

**Message:** Create two lossless PNG shares from a cover image and a password. Both images and the password are required to decode. `create_unlock_pair` writes a randomized 256-bit application credential across the two PNGs; `unlock_image_pair` authenticates/recovers it in one call. `embed_identity_pair` and `unlock_identity_pair` handle paired SSH public-key / SHA-256 / MD5 identifier matching. These are not directly OpenSSH authentication.

**Auth:** A locally trusted administrator generates a private master PNG and issues individually signed client PNG bearer credentials. Present one client PNG to the server-side verification adapter to check it against the master, permissions, expiration, revocation, and optional password. It supports five failed attempts in five minutes followed by administrator unlock. Its TXT-filtered file chooser can accept a manually typed PNG filename. The plugin is disabled by default until locally configured; no network listener, remote upload endpoint, or OpenSSH authentication module is supplied.

**Key:** Existing paired-image key recovery, cover reconstruction, and password rotation remain available through the compiled key engine.

### Security and compatibility

Lossless PNG is required. Filename-derived passwords expose their secret to anyone who obtains the filename; a copy of an unprotected client PNG is a transferable bearer credential. The master issuer seed is stored in protected PNG metadata; its secrecy depends on local filesystem permissions. Never commit actual credential images, authentication SQLite databases, `.env` files, or issuer key material.

The 1.6.12 package validator accepts this ZIP, and the compiled StegoSplit adapter smoke tests and all 18 bundled plugin identity checks passed on KHzz. No full installer-upgrade test or adversarial cryptographic audit has been performed.

See `docs/README.md`, `docs/CURRENT_STATUS.md`, `docs/RELEASE_NOTES.md`, `SOURCE_PACKAGE.md`, and the individual plugin README/help files.
