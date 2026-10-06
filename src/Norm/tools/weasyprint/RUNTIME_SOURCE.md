# WeasyPrint Windows runtime source

Norm uses the official WeasyPrint 70.0 Windows onedir runtime.

The public source and portable-source ZIP intentionally do not vendor the generated native runtime directory.

- Upstream asset: https://github.com/Kozea/WeasyPrint/releases/download/v70.0/weasyprint-windows-onedir.zip
- Archive SHA-256: ab1151f210b4e6bb7aa7a79e91a67e8ddb760094c107bfda55241b6aaefe7d53
- Runtime family: MSYS2 UCRT64
- Verified Pango: 1.58.2 (reported as 15802)
- Runtime target: tools\weasyprint\runtime
- Fetch/repair helper: tools\fetch_weasyprint_runtime.py
- Wrapper: tools\weasyprint.cmd

Installer 1.6.8 preserves an already-valid installed runtime. If the runtime is absent or stale, it invokes the helper, verifies the pinned archive hash, extracts the official onedir build, and then repeats --info plus a real HTML-to-PDF render.

The wrapper uses the same helper for lazy repair. No local MSYS2 installation and no Pango compilation are required.
