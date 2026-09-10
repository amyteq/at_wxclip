"""wxclip: capture, download and decrypt WeChat Channels videos.

The media URL comes straight from the WeChat JSAPI response; nothing resolves it.
What needs decrypting is the downloaded file: its first 128 KiB are XORed with an
ISAAC64 keystream seeded by the decode_key that accompanies the URL.

Planned modules (see docs/开发计划.md):
    proxy       - MITM proxy that injects the hook script and receives the capture
    keystream   - ISAAC64 keystream for a decode_key (bridges web/, or a Python port)
    downloader  - direct download (never through the proxy) plus head decryption
    server      - light web server backing the admin / download dashboard
"""

__version__ = "0.0.1"

__all__ = ["__version__"]
