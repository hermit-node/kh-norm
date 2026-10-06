# Publishing kh-norm 0.53.17

This ZIP is a repository source/update bundle, not the turnkey installer release.

Recommended workflow from a clean clone of hermit-node/kh-norm:

    replace src/Norm with this bundle's src/Norm
    overlay the root source/docs/tools/tests files
    python tools/public_release_guard.py --tree .
    python tests/test_public_release.py
    git status
    git add -A
    git commit -m "Release Norm 0.53.17 / Installer 1.6.7"
    git push origin main
    git tag -a v0.53.17 -m "Norm 0.53.17 / Installer 1.6.7"
    git push origin v0.53.17

Do not add generated installer executables, portable-source ZIPs, local WeasyPrint runtime files, runtime state or private deployment configuration.

Publish-To-GitHub.ps1 is intentionally not part of this bundle.
