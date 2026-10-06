# OpenCode remote adapter

Delegates substantial coding work to an operator-configured OpenCode HTTP server over Tailscale. Norm can check health, create/continue sessions, retrieve messages/diffs, and abort work. The configured provider ID and primary/secondary model IDs map to the shared OpenAI-compatible model server configured on the installer's Remote agents tab. HTTP Basic Auth password is stored only as `NORM_OPENCODE_PASSWORD`.
