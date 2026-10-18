# Norm 0.55.10r26: Generic Vision Media

Built from the exact 0.55.10r25 portable-source ZIP.
Changes are limited to the built-in plugins/vision_parse plugin, metadata,
and this source-release documentation. The plugin adds face occurrence UUIDs,
YuNet/SFace embeddings, two-pass scene edges with minimum 30-second scenes,
scene/video categorization through Norm's shared vision client, and read-only
batch identity reconciliation with protected manual aliases.

No changes to N1/N2, Ollama, Redis/PostgreSQL, runtime settings, state,
installer code or model binaries. The existing installer consumes this complete,
flat-root portable-source ZIP normally; there is no addon installer step.
ONNX Runtime GPU and YuNet/SFace model download are optional runtime setup
operations, not required to install Norm. GPU backend auto-falls back to CPU.

The original r25 archive and running C:\Norm are unmodified.

Public GitHub snapshots intentionally omit private compiled components and local config; only the original private ZIP on KHzz is a full internal installation source.
