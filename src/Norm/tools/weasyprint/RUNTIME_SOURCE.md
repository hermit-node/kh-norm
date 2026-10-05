# WeasyPrint Windows runtime source

The turnkey release bundles the official WeasyPrint Windows onedir runtime. Git intentionally omits the frozen binary directory.

- WeasyPrint: 70.0
- Upstream asset: https://github.com/Kozea/WeasyPrint/releases/download/v70.0/weasyprint-windows-onedir.zip
- Archive SHA-256: ab1151f210b4e6bb7aa7a79e91a67e8ddb760094c107bfda55241b6aaefe7d53
- Runtime family: MSYS2 UCRT64
- Verified release build: Pango 1.58.2
- Runtime target: tools/weasyprint/runtime
- Norm wrapper: tools/weasyprint.cmd

Reconstruct the exact runtime for a release build:

    python tools/fetch_weasyprint_runtime.py

The helper verifies SHA-256 before extraction. The runtime directory is gitignored.
