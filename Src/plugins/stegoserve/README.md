# StegoServe (Norm 0.6 integration)

Company and user provisioning backend **behind** standalone Stego AuthGate. It does **not** intercept network traffic or provide a session listener inside Norm. The standalone `authgate_server.py` gates SSH access and enforces leases/heartbeats.

## Prerequisite: standalone v0.6 engine

Unpack the separately built `Stego-AuthGate-v0.6-Win-x64-Python314-Deploy.zip` somewhere on the Windows machine. Example existing KHzz directory: `D:\LOCAL_Share\Code Projects\StegoSplit\releases\authgate_v0_6_deploy`. StegoServe auto-discovers that path, or use `STEGOSERVE_BACKEND_DIR` environment variable. The backend must contain `authority.py`, `ss_ctl.py`, `paired_auth.py`, `formula_profiles.cp314-win_amd64.pyd`, `formula_catalog128.cp314-win_amd64.pyd`, and `authgate_server.py`.

The engine has **128 separately compiled pre-seeded formulas**, 32 in each of four complexity tiers. Companies receive up to 16 distinct formula assignments, four per tier when using all 16, plus independent company, password-derived, and rotor-round seeds. Body geometry is width × (height−1), excluding row 0. The 0.4 stratified allocation limits clumping of data and decoys.

## Native Norm functions (safe to call through ordinary tools)

`engine_status`, `profile_info(verifier_dir)`, `gateway_preflight(config_file)`, `plan_batch(company, users, output_dir)`, `save_batch_plan(company, users, output_dir, plan_file)`, and `plan_rotation`. None of these expose a CA private key or plaintext password. The `run` dispatcher accepts only these read-only or preparatory actions.

## Administrative issuance (LOCAL OPERATOR COMMAND LINE ONLY)

From `plugins/stegoserve/src` in an interactive Windows terminal:

```powershell
py -3.14 admin_cli.py init-company --company acme --image C:\Stego\logo.png --issuer C:\Stego\issuer --verifier C:\Stego\verifier
py -3.14 admin_cli.py issue-batch --company acme --plan C:\Stego\acme.plan.json --issuer C:\Stego\issuer --verifier C:\Stego\verifier
py -3.14 admin_cli.py rotate-batch --company acme --plan C:\Stego\acme-new-round.plan.json --issuer C:\Stego\issuer --verifier C:\Stego\verifier
py -3.14 admin_cli.py sign-route --company acme --issuer C:\Stego\issuer --verifier C:\Stego\verifier --host 127.0.0.1 --port 22 --out C:\Stego\acme.route.json --allow-user alice
```

Norm can prepare a password-free plan for 16 (or more) users, assigning formula slots 0..15 in sequence. The operator CLI prompts twice for each *user-chosen password*. Passwords are never written into the plan, log, subprocess argument, or returned JSON. Credential registration is signed with the protected issuer key. Rotation reissues new B images before updating the verifier, and then invalidates old B credentials and active sessions. Do not advance an issuer round twice when reissuance has partially failed.

The standalone AuthGate handles actual TLS listener, strict session admission, routing, heartbeat and SSH byte forwarding. The optional blocking listener owns only its own port; a firewall or loopback-only backend must independently prevent bypass of protected SSH port 22. **Neither Norm nor this plugin changes firewalls, sshd, active sessions, or production listeners automatically.**

**Production limitation:** Norm plugin integration and CLI have offline interface tests; the standalone v0.6 backend has its own verified extracted-ZIP tests on KHzz. The combined Norm → Admin CLI → Live AuthGate user workflow has not yet been exercised end-to-end under a running Norm installation.

## Build standalone gateway configuration from Norm

`save_gateway_config(config_file, listener_port, tls_certificate, tls_private_key_file, routes, blocking_port=0)` creates a new JSON configuration containing signed-route file references, listener TLS paths, default 1-hour lease and 360-second heartbeat grace. It does not start a server or change firewall rules. Call `gateway_preflight(config_file)` to validate with the separate installed backend before explicitly starting AuthGate.
