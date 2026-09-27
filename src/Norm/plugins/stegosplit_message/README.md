# StegoSplit Message Codec

Self-contained first-party Norm plugin built from the uploaded `StegoSplit-MessageCodec 0.2.0a1` source. Unlike the older 0.51.x wrapper, it does **not** depend on an editable checkout under `D:\LOCAL_Share`; rebuilding `.venv` therefore does not remove the codec.

Native tools: `embed_message`, `extract_message`, `rebuild_cover`, plus `run(payload)` for legacy broker compatibility.

The codec compresses the carrier RGB range to 1..254, uses a differential row-0 header, distributes payload bytes between the two shares, and CRC32-checks extraction. PNG/lossless handling is required.

This is a message carrier, **not encryption**: anyone with both shares can decode the message. CRC32 is corruption detection, not cryptographic authentication.
