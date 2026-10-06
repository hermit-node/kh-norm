# Vane remote adapter

Norm adapter for a small Vane HTTP bridge on a Tailscale peer. Vane itself remains an embedded retrieval library; the bridge is intentionally separate and uses `@vane-rs/node` on Windows. The bridge performs embeddings through the shared embedding endpoint configured in Norm, while Vane supplies BM25 + HNSW + RRF retrieval. The optional `NORM_VANE_TOKEN` stays in Norm's secrets file.
