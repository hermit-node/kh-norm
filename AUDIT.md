# Publication audit — Norm 0.53.14 / Installer 1.6.6

Public bundle generated from the 0.53.14 checkpoint package.

Publication-specific changes:

- replaced private deployment chronology in `src/Norm/docs/DEVELOPMENT_NOTES.md` with a public boundary note;
- removed a machine-specific Ollama model-store fallback from public source in favor of `OLLAMA_MODELS`/explicit configuration;
- strengthened `.gitignore` for local secrets, SSH material, runtime state, logs, workspace, and local imprints;
- retained generic/example topology only;
- generated a fresh public portable-source ZIP and companion SHA-256.

Portable source SHA-256: `b856a9a3ce62956aa2661278391d2fec4bc51671e1b46d00e2fa53659a18186e`
