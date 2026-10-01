# Security

## Never commit

Do not commit:

- `norm-imprint.local.json` or other local imprint variants;
- `.env` files;
- PostgreSQL passwords;
- Tailscale auth keys or OAuth secrets;
- SSH private keys;
- private CA keys;
- API tokens;
- backup archives containing runtime state.

The repository `.gitignore` excludes common local secret/key files.

## Imprint boundary

Imprints are intentionally non-secret. The installer rejects secret-like JSON keys such as password, token, secret, authkey, API key, and private key fields.

Deployment topology may itself be sensitive even when it is not a credential, so local imprint files are ignored by Git by default.

## Norm network-map safety

Passive Tailscale peer inventory may include decoy/honeypot nodes. Presence in the passive inventory never authorizes an active connection.

Active probes use an explicit allowlist and re-check the never-probe policy immediately before network I/O. Hostnames are resolved before probing and every resolved address is checked against `never_probe_cidrs`. HTTP probes are pinned to an approved resolved address and never follow redirects.
