"""Runtime settings, all overridable through the environment (see `.env.example`).

Kept dependency-free on purpose: the CLI, the proxy and later the web server read the
same values, and importing this must never pull in mitmproxy.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8899
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8080


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a `KEY=value` file. Missing file is not an error; real env wins over it."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _config_home(env: Mapping[str, str]) -> Path:
    xdg = env.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else Path.home() / ".config"


@dataclass(frozen=True)
class Settings:
    """Where the proxy listens, where its CA lives, where output goes."""

    proxy_host: str = DEFAULT_PROXY_HOST
    proxy_port: int = DEFAULT_PROXY_PORT
    server_host: str = DEFAULT_SERVER_HOST
    server_port: int = DEFAULT_SERVER_PORT
    download_dir: Path = Path("downloads")
    capture_dir: Path = Path("captures")
    ca_dir: Path = Path.home() / ".config" / "wxclip" / "ca"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, dotenv: Path | None = None) -> Settings:
        merged: dict[str, str] = {}
        if dotenv is not None:
            merged.update(read_dotenv(dotenv))
        merged.update(os.environ if env is None else env)

        def port(name: str, fallback: int) -> int:
            raw = merged.get(name, "").strip()
            if not raw:
                return fallback
            try:
                return int(raw)
            except ValueError as exc:  # a typo here is worth failing loudly
                raise ValueError(f"{name} must be a port number, got {raw!r}") from exc

        return cls(
            proxy_host=merged.get("WXCLIP_PROXY_HOST") or DEFAULT_PROXY_HOST,
            proxy_port=port("WXCLIP_PROXY_PORT", DEFAULT_PROXY_PORT),
            server_host=merged.get("WXCLIP_SERVER_HOST") or DEFAULT_SERVER_HOST,
            server_port=port("WXCLIP_SERVER_PORT", DEFAULT_SERVER_PORT),
            download_dir=Path(merged.get("WXCLIP_DOWNLOAD_DIR") or "downloads"),
            capture_dir=Path(merged.get("WXCLIP_CAPTURE_DIR") or "captures"),
            ca_dir=Path(merged.get("WXCLIP_CA_DIR") or _config_home(merged) / "wxclip" / "ca"),
        )
