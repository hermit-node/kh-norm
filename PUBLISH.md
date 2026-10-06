# Publishing Norm 0.53.20

This public source/install kit contains Installer 1.6.10-unified source, its build recipe, and the portable-source ZIP. Generated installer EXEs are not tracked.

Validate before publishing:

    python -B tools/public_release_guard.py --tree .
    python -B tools/public_release_guard.py --zip Norm-0.53.20-portable-source.zip
    python -B tests/test_public_release.py

The `Src/` tree must match the ZIP byte for byte. Publish only public ancestry; never merge machine-local/private history into the public branch.
