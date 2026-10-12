"""wx-dlh: capture, download and decrypt WeChat Channels videos.

The media URL comes straight from the WeChat JSAPI response; nothing resolves it.
What needs decrypting is the downloaded file: its first 128 KiB are XORed with an
ISAAC64 keystream seeded by the decode_key that accompanies the URL.

Modules (see docs/development-plan.md):
    proxy       - MITM proxy that injects the hook script and receives the capture
    capture     - what the hook script posts back, and the decode_key precision guard
    keystream   - ISAAC64 keystream for a decode_key (a port; no Node, no wasm blob)
    decrypt     - XOR of the encrypted head, streaming and offset-aware
    downloader  - direct download (never through the proxy) plus head decryption
    pipeline    - capture event -> chosen media -> downloaded, decrypted file
    server      - light web server backing the admin / download dashboard
    ca, config, inject - the CA, settings, and what gets injected where

Post-processing, on a file that is already playable (M3):
    media       - finding ffmpeg / ffprobe, and what ffprobe says about a file
    frames      - keyframe screenshots, chosen by scene change
    extract     - the text in those stills, by OCR
    summarize   - transcript + stills -> a written summary, via the Messages API
    postprocess - the three of them in sequence, one directory per clip
"""

__version__ = "0.0.1"

__all__ = ["__version__"]
