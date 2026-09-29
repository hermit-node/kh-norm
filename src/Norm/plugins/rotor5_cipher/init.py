NAME = "rotor5_cipher"
VERSION = "0.1.0"
ENTRYPOINT = "rotor5_cipher.py:run"
CAPABILITIES = [
    "encode message with five rotor cipher",
    "decode five rotor message",
    "enigma style message encoding",
    "rotor cipher",
    "encode bytes before stegosplit",
]
DESCRIPTION = "Independent five-machine Enigma-style byte-rotor message encoder/decoder with per-message nonce and optional live secret from Norm's .env."
