# OpenCode remote adapter

Delegates substantial coding work to an operator-configured OpenCode HTTP server over Tailscale. Norm can check health, create/continue sessions, retrieve messages/diffs, and abort work. HTTP Basic Auth password is stored only as `NORM_OPENCODE_PASSWORD` in Norm's external secrets file.

## Configuration

This plugin declares its non-secret defaults in `plugin.json`. `PluginManager.refresh()` reconciles them into `config/plugins.ini` on startup and hot-rescan. `[opencode] enabled = false` is the default. Provider/model routing uses the shared `[model_provider]` section declared consistently by Jan/Vane/OpenCode. Operator values survive refresh; missing/invalid keys use manifest defaults.
