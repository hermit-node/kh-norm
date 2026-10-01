# Release

## Norm 0.53.9 / Unified Installer 1.6.0

- Added synchronous `/network-map [--json]`.
- Added passive peer inventory plus explicit allowlist probing.
- Added fail-closed honeypot/decoy no-probe policy, including resolved-address CIDR checks and no-follow HTTP probes.
- Added machine-readable `norm-network-map.cmd --json`.
- Replaced deployment-specific source defaults with topology-neutral public defaults.
- Added four-stage installer Environment page.
- Added local non-secret imprint auto-fill/save.
- Added masked secret entry and secret/imprint separation.
- Added headless secret-file inputs.
- Added reusable Windows installer-builder venv.

### Correct merged baseline

This release preserves `vision_parse`, PyMuPDF 1.28.2, the Ollama stream degeneration watchdog, and suppression handoff cleanup from the merged 0.53.8 source before layering the 0.53.9 network-map/public-imprint changes.
