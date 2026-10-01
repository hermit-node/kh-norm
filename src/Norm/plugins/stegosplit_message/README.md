# StegoSplit Message Carrier — Norm plugin

Self-contained StegoSplit V2 paired-image carrier plugin. Cipher/encoder layers are deliberately separate plugins; this plugin accepts arbitrary text or bytes and protects the hidden payload with its own ChaCha20-Poly1305 layer. Norm can use it to hide text or bytes in a pair of lossless PNG shares, recover hidden content when both shares and the password are supplied, rotate the password without the original source image, reconstruct the canonical cover, inspect an authenticated pair, and report differential statistics.

## Norm entry point

`plugin.py:run(payload: dict) -> dict`

The plugin is intentionally self-contained; the `_stegosplit_v2` private engine package is bundled under this plugin folder rather than relying on a separate editable install.

## V2 format invariants

- One source image produces two PNG shares.
- Row 0 is only a tiny public bootstrap. `(0,2)` is the reference pixel and `(0,3)` marks the role: red `+1` means A, red `-1` means B.
- The remaining public bootstrap only carries format V2 and the selected real formula ID `0..15`. It contains no pair ID, UUID, matching checksum, salt, nonce, angle, or payload length.
- The real formula is frozen in V2 code. Seed variables are password-derived.
- Pair-specific control data is hidden in password-derived body positions, not exposed in the public row-0 header.
- The final center split angle is randomized from 45.00° through 89.99°.
- Ciphertext bits are split exactly 50/50: A is read as `A-B`; B is read as `B-A`.
- A bit changes exactly one perceptually selected RGB channel by `-1` for 0 or `+1` for 1.
- Four password-derived decoy formula streams are used; the default decoy ratio is 1:1 against all real body bits.
- ChaCha20-Poly1305 authenticates encrypted payload data and hidden control metadata.
- A password-gated hidden salt gives each pair a fresh encryption/mapping root while keeping row 0 minimal.
- Decompression is bounded by the authenticated original byte length.
- Generated temporary shares are decoded and byte-compared before final output files are promoted.

## Actions

### `embed`
Required: `source_image`, `share_a`, `share_b`, `password`, and one of `message`, `message_file`, or `message_base64`.
Optional: `decoy_ratio` (default `1.0`).

### `extract`
Required: two share paths (`share_a`/`share_b` or `image_1`/`image_2`) and `password`.
The images may be supplied in either order; the row-0 role marker determines A vs B. Text is returned as `message`. Set `as_bytes=true` for Base64 output or provide `output_file` to write raw recovered bytes.

### `rotate`
Required: two input shares, `old_password`, `new_password`, `output_a`, `output_b`. The original source image is not required.

### `rebuild`
Required: two shares, `password`, `output`. Reconstructs the canonical conditioned cover used by V2.

### `info`
Required: two shares and `password`. Returns authenticated formula, split angle, decoy formulas, payload size and other pair information.

### `stats`
Required: two shares. Returns raw differential counts without attempting password authentication.

## Example payload

```json
{
  "action": "embed",
  "source_image": "C:\\path\\cover.png",
  "share_a": "C:\\path\\share-a.png",
  "share_b": "C:\\path\\share-b.png",
  "password": "example password",
  "message": "hidden message"
}
```

The plugin requires Python packages `Pillow` and `cryptography` in the Python environment used by Norm's plugin broker.


## Separation of concerns

`stegosplit_message` does not implement Rotor5 or any other pre-encoding scheme. To layer Rotor5 over StegoSplit, encode with the separate `rotor5_cipher` plugin and pass its returned Base64 envelope to `embed_base64`. On recovery, use `extract_base64` and then decode that envelope with `rotor5_cipher`.

Plugin version: `0.3.0`; bundled StegoSplit engine: `2.0.0a4`; public image format: `V2`.
