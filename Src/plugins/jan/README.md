# Jan remote adapter

Norm Schema-2 adapter for a Jan OpenAI-compatible API reachable over an operator-configured Tailscale endpoint. `jan_chat` resolves `primary`/`secondary` through the shared `[model_provider]` plugin settings; credentials stay in Norm's external secrets file (`NORM_JAN_API_KEY`). The adapter never discovers or probes arbitrary hosts.

## Configuration

This plugin declares its non-secret defaults in `plugin.json`. `PluginManager.refresh()` reconciles them into `config/plugins.ini` on startup and hot-rescan. `[jan] enabled = false` is the default, so an unconfigured install is inert. Operator values such as `host`, `port`, and `model` survive refresh; missing/invalid keys fall back to manifest defaults. Secrets never belong in `plugins.ini`.
