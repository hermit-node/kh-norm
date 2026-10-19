# Norm 0.55.10r26: Generic Vision Media

Built from the exact 0.55.10r25 portable-source ZIP.
The original r26 implementation added the built-in vision_parse media tools. This packaging correction also aligns the installer-ready manifest, sanitized settings, and Python requirements. The plugin adds face occurrence UUIDs,
YuNet/SFace embeddings, two-pass scene edges with minimum 30-second scenes,
scene/video categorization through Norm's shared vision client, and read-only
batch identity reconciliation with protected manual aliases.

No change to N1/N2 tool-approval semantics, Ollama, Redis/PostgreSQL behavior, state, or installer code. The 1.6.17 installer consumes this flat-root portable-source ZIP and builds norm.exe.
GPU-capable ONNX Runtime and imageio-ffmpeg are pinned Python dependencies. Media model weights require explicit approved download. CUDA inference auto-falls back to OpenCV CPU, and FFmpeg is resolved via PATH or the Python wheel.

The original r25 archive and running C:\Norm are unmodified.

The public source package includes a generic sanitized config/settings.ini template for installer validation. Installer 1.6.17 creates a virtual environment, installs dependencies and builds norm.exe; operator secrets and runtime state remain external.
