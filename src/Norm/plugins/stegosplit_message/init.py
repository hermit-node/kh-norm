NAME = "stegosplit_message"
VERSION = "0.3.0"
ENTRYPOINT = "plugin.py:run"
CAPABILITIES = [
    "steganography",
    "embed hidden message in image pair",
    "extract hidden message from image pair",
    "paired differential image encoding",
    "rotate stegosplit password",
    "reconstruct stegosplit cover",
    "inspect stegosplit pair",
]
DESCRIPTION = "StegoSplit V2 paired-image carrier for arbitrary text/bytes; cipher layers are separate plugins."
