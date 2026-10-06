# StegoSplit Key Pair

Bundled first-party plugin wrapping the uploaded StegoSplit `0.2.0a1` prototype. It carries a 256-bit key across two visually similar lossless PNG shares and authenticates recovery with the password + map key.

Native tools: `create_key_pair`, `recover_key`, `reconstruct_key_cover`, `rotate_key_pair`, and `key_pair_stats`.

The uploaded prototype CLI was deliberately not bundled because it did not pass the current required `map_key` argument. This plugin calls the current library API directly.

Security/format limits from the prototype still apply: lossless image handling is required; this code has not been independently cryptographically audited; Python cannot guarantee secure in-memory wiping.

## Package identity

`plugin.json` schema 2 records this plugin's name, version, release date, `src/main.py` injection point, and one SHA-256 for the complete `src/` tree. Norm recalculates that tree hash before loading the plugin. README changes do not change the code SHA; edits or renames anywhere under `src/` do.
