# StegoSplit Key Pair

Bundled first-party plugin wrapping the uploaded StegoSplit `0.2.0a1` prototype. It carries a 256-bit key across two visually similar lossless PNG shares and authenticates recovery with the password + map key.

Native tools: `create_key_pair`, `recover_key`, `reconstruct_key_cover`, `rotate_key_pair`, and `key_pair_stats`.

The uploaded prototype CLI was deliberately not bundled because it did not pass the current required `map_key` argument. This plugin calls the current library API directly.

Security/format limits from the prototype still apply: lossless image handling is required; this code has not been independently cryptographically audited; Python cannot guarantee secure in-memory wiping.
