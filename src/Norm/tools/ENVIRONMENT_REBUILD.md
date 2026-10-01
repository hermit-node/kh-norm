# Norm environment rebuild

Norm source packages intentionally exclude `.venv` and compiled executables. Both are reproducible build artifacts.

The canonical dependency lock is `tools/requirements-lock.txt`. The configured Python and Torch versions live in `config/settings.ini`.

A clean installation should:

1. Select/install the configured Python version.
2. Create `<NORM_ROOT>\.venv`.
3. Upgrade pip in that virtual environment.
4. Install the configured Torch build from the configured Torch index.
5. Install `tools\requirements-lock.txt`.
6. Validate imports required by Norm.
7. Run `tools\build_norm.py` to create the executable under `core\`.
8. Validate the configured secrets, Redis, PostgreSQL, Ollama, Tailscale, workspace, and documentation paths before starting Norm.

The install script is intentionally separate from this source package so the package remains portable and does not contain machine-specific generated state.


StegoSplit is now self-contained under the built-in plugin tree. No editable `D:\Data\Code Projects\StegoSplit-MessageCodec` reinstall is required after recreating `.venv`; Pillow and NumPy from the normal dependency lock are sufficient for the bundled codecs.
