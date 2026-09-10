"""wxclip: capture, decode and download WeChat videos.

Planned modules (see docs/开发计划.md):
    proxy       - local proxy that observes media requests from WeChat for Linux
    decoder     - bridge to the JavaScript decode logic in web/
    downloader  - direct download of the resolved URL (never through the proxy)
    server      - light web server backing the admin / download dashboard
"""

__version__ = "0.0.1"

__all__ = ["__version__"]
