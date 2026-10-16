# Vane remote adapter

Norm adapter for a small Vane HTTP bridge on a Tailscale peer. Vane remains a retrieval library; the bridge is intentionally separate and may use `@vane-rs/node` on Windows. The optional `NORM_VANE_TOKEN` stays in Norm's external secrets file.

## Configuration

This plugin declares its non-secret defaults in `plugin.json`. `PluginManager.refresh()` reconciles them into `config/plugins.ini` on startup and hot-rescan. `[vane] enabled = false` is the default. The shared `[model_provider]` section is declared consistently by Jan/Vane/OpenCode and can hold an embedding endpoint/model when the remote bridge needs one. Operator values survive refresh; missing/invalid keys use manifest defaults.
