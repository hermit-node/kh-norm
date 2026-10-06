# Publication audit - Norm 0.53.17 / Installer 1.6.7

This bundle was generated from the verified 0.53.17 release source as a GitHub-oriented source tree.

Publication boundary:

- retained runtime source, docs, tests, plugin source/manifests, installer source, generic config and bundled 7-Zip;
- excluded generated installer executables, generated release/source ZIPs, caches/build output, runtime state/logs/workspace/temp, SSH material, local imprints and secrets;
- excluded src/Norm/tools/weasyprint/runtime from Git history while retaining upstream license/source metadata and a SHA-verified fetch helper;
- retained RELEASE_NOTES.md as the historical ledger;
- retained current-state/current-contract/current-backlog/current-evidence docs without old-version chronology;
- intentionally did not restore Publish-To-GitHub.ps1.

Validation before packaging:

- private deployment/token literal scan: PASS;
- public release guard: PASS;
- Python syntax scan: PASS (107 files);
- public-boundary regression: PASS;
- selected model-switch/help, maintenance, archive, and N1 regressions: PASS;
- pre-audit public tree files scanned: 166.
