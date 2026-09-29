# Rotor5 Cipher — Norm plugin

Independent reversible five-machine Enigma-style byte-rotor encoder/decoder. It is intentionally separate from `stegosplit_message`: Rotor5 transforms the message; StegoSplit carries arbitrary bytes across two PNG shares.

## Pipeline when used together

`message -> Rotor5 -> Base64 envelope -> StegoSplit embed_base64 -> share A + share B`

Recovery is the reverse: `StegoSplit extract_base64 -> Rotor5 decode`.

## Rotor design

- Five independently derived 256-symbol rotor machines.
- Each machine has password/message-derived wiring, fixed-point-free reflector, start position, odd step, and a slower wobble/carry term.
- A fresh 16-byte nonce changes all five machines for every encoding.
- Compression happens before the rotor transform so StegoSplit capacity is not wasted on otherwise-compressible text/files.
- A truncated HMAC-SHA256 authenticates the envelope and detects a wrong Rotor5 password, wrong live secret, or damaged envelope.
- Rotor5 is an additional transform, not a replacement for modern encryption. When layered with StegoSplit, StegoSplit still applies ChaCha20-Poly1305 to the Rotor5 envelope.

## Optional live seed/secret

Put either of these in Norm's configured `%APPDATA%\Norm\.env`:

`NORM_ROTOR5_SECRET=<current live secret>`

`NORM_ROTOR5_PREVIOUS_SECRETS=<older secret 1>;<older secret 2>`

Norm 0.53.2 exports only these Rotor5 secret values from its already-loaded/redacted secrets dictionary into the process environment for the plugin. New envelopes use the current secret when present. Previous values permit decoding after rotation. The live secret itself is never placed in the Rotor5 envelope; only an 8-byte selector fingerprint is stored.

If no live secret is configured, Rotor5 remains password-only. Adding a live secret later does not break older password-only envelopes.

## Native tools

- `encode_text(message, password)` -> `encoded_base64`
- `decode_text(encoded_base64, password)`
- `encode_base64(message_base64, password)`
- `decode_base64(encoded_base64, password)`
- `encode_file(input_file, output_file, password)`
- `decode_file(input_file, output_file, password)`
- `envelope_info(encoded_base64)`
- `run(payload)` legacy action wrapper

Envelope: `R5E2`, plugin version `0.1.0`.
