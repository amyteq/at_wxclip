"""wxclip: capture, download and decrypt WeChat Channels videos.

The media URL comes straight from the WeChat JSAPI response; nothing resolves it.
What needs decrypting is the downloaded file: its first 128 KiB are XORed with an
ISAAC64 keystream seeded by the decode_key that accompanies the URL.

Modules (see docs/开发计划.md):
    proxy       - MITM proxy that injects the hook script and receives the capture
    capture     - what the hook script posts back, and the decode_key precision guard
    keystream   - ISAAC64 keystream for a decode_key (a port; no Node, no wasm blob)
    decrypt     - XOR of the encrypted head, streaming and offset-aware
    downloader  - direct download (never through the proxy) plus head decryption
    pipeline    - capture event -> chosen media -> downloaded, decrypted file
    ca, config, inject - the CA, settings, and what gets injected where

Planned:
    server      - light web server backing the admin / download dashboard (M2)
"""

__version__ = "0.0.1"

__all__ = ["__version__"]
