# Publication audit — Norm 0.53.9 / Unified Installer 1.6.0

Audit date: 2026-10-01

## Scope

This audit reviews the corrected public repository bundle built from the true merged Norm 0.53.8 source baseline, then layered with the 0.53.9 operator network-map and public-installer/imprint changes.

## Findings fixed before publication

### 1. Current-version documentation drift

`src/Norm/SOURCE_PACKAGE.md` and `src/Norm/docs/CURRENT_STATUS.md` still presented the package as the current 0.53.8 line even though `package-manifest.json`, the runtime docs, and the release package were 0.53.9.

Resolution:
- current package/status headings now say 0.53.9;
- the 0.53.9 network-map/public configuration boundary is documented explicitly;
- historical 0.53.8 sections remain intact as history;
- `DEVELOPMENT_NOTES.md` now includes the 0.53.9 change.

### 2. Never-probe CIDRs were checked only against literal configured IPs

A configured hostname could be allowlisted by name and later resolve to an address inside `never_probe_cidrs`.

Resolution:
- every probe hostname is resolved before the connection;
- every resolved address is checked against every configured never-probe CIDR;
- if any resolved address is forbidden, the active probe fails closed;
- TCP probes connect to the already-approved resolved address rather than resolving again for the connection.

### 3. HTTP probes could follow redirects

The previous urllib-based HTTP check could follow an allowed service's redirect to a second destination, defeating the exact-target policy.

Resolution:
- HTTP/HTTPS checks now use an address-pinned connection;
- the logical Host/SNI identity is preserved;
- redirects are returned as 3xx results but never followed;
- regression coverage asserts that only one destination is contacted.

## Plugin/baseline verification

The corrected 0.53.9 source carries forward the merged 0.53.8 package-managed plugin set:

- `backup`
- `verbatim_lines`
- `stegosplit_key`
- `stegosplit_message`
- `rotor5_cipher`
- `vision_parse`

It also preserves:
- PyMuPDF 1.28.2 and PyInstaller `--collect-all pymupdf`;
- the Ollama repetitive-output degeneration watchdog;
- suppression handoff/live-queue cleanup.

## Public/private boundary

The repository contains only topology-neutral public defaults. `norm-imprint.local.json`, `.env`, SSH material, private keys, CA keys, tokens, and backup archives are excluded from publication.

The public-release scan rejects:
- Tailscale CGNAT addresses;
- known private host/domain signatures from the deployment used to build this release;
- common private-key and credential signatures;
- a local imprint accidentally included in the repository.

## Validation performed

- Python syntax compile: all 79 Norm source Python files plus installer/tests/tools.
- Installer regressions: PASS.
- Network-map passive/allowlist/never-probe regressions: PASS.
- Hostname resolving into never-probe CIDR: PASS.
- HTTP redirect no-follow policy: PASS.
- Public source checksum: PASS.
- Public topology/credential scan: PASS.

## Remaining acceptance boundary

The Windows Tkinter installer UI, PyInstaller executable build, Windows service launch, and full live runtime startup still require acceptance on Windows. The Linux packaging environment cannot prove those Windows-specific behaviors.
