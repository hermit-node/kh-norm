# StegoSplit (Norm 0.5 integration)

Bare general-purpose paired-image steganography and a seeded polygonal base-image generator.

**Payloads:** `embed_text`, `extract_text`, `embed_base64`, `extract_base64`, `embed_file`, `extract_file`. Also the compiled V3 `create_unlock_pair`, `unlock_image_pair`, `rotate_pair_password`, `pair_info`, `pair_stats`, `rebuild_cover`, `embed_identity_pair`, `unlock_identity_pair` functions remain available. The underlying compiled message engine is unchanged from r8 **V3 0.4.3** and uses its existing 16 V3 mapping choices. It is **not** relabeled as the newer 128-independent-formula credential engine.

**Base image creation:** `generate_base_image(output_png, seed, width=512, height=512, object_type='random')` renders *literal Pillow-filled polygons*, no SVG and no generative AI. The image is RGB PNG, at most 12 colors, with no antialiasing. Object families: mug, kettle, stapler, desk lamp, toaster, plant, notebook, wrench, shoe. Use a stable seed for identical outputs. For imported company logos, pass the supplied PNG directly into the StegoServe issuer. Imported images are not silently recolored.

**Environment:** Windows x64, CPython 3.14, numpy, Pillow, cryptography. Keep image pairs lossless and exactly sized. Row 0 of issued credential images is reserved for metadata by the separate v0.6 engine.

**Compatibility:** The original r8 `stegosplit_message`, `stegosplit_key`, and `stegosplit_auth` plugins are stored under `legacy_stegosplit_plugins_r8/`, outside the active plugin directory. They were not erased. The legacy Auth v0.1 master image stored a sensitive signing seed in metadata; do not use it for new identities.
