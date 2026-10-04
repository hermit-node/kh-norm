# Security

## Never commit

Do not commit `.env` files, local imprints, passwords, API/OAuth/Tailscale tokens, SSH private keys, private CA keys, PostgreSQL dumps containing user/runtime state, logs, or full-state backup archives.

## Public/private boundary

The public tree contains topology-neutral source/configuration only. Deployment chronology and machine-specific engineering notes are intentionally excluded.

N1's validation cache is an execution-control mechanism, not a secret store. Tool results can contain sensitive data at runtime and therefore must remain in local Redis/PostgreSQL/runtime state rather than release artifacts.
