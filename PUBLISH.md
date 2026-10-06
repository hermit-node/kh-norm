# Publishing 0.53.19

This is a public source release. The portable-source ZIP is rebuilt from the saved `src/Norm` snapshot. The generated installer EXE is not included.

    python -B tools/public_release_guard.py --tree .
    python -B tests/test_public_release.py

Do not add machine-local state or downloaded native runtime files.
