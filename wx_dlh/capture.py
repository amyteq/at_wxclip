"""Records posted back by the hook script.

`decode_key` is a 64-bit integer and it stays a decimal string from here to the XOR:
the hook script lifts it out of the raw JSON text precisely so it never passes through a
float. This module refuses to normalise it into anything else and flags a value that does
not look like an untouched integer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: One to twenty decimal digits - `18446744073709551615` is the uint64 ceiling.
DECODE_KEY_RE = re.compile(r"\A[0-9]{1,20}\Z")

KIND_PROBE = "probe"
KIND_HOOK = "hook"
KIND_INVOKE = "invoke"
KIND_CAPTURE = "capture"


class CaptureError(ValueError):
    """The posted body was not something the hook script could have sent."""


def is_valid_decode_key(value: object) -> bool:
    """A usable key is a digit string. A float, or a key that has been through one,
    is not: `13835058055282163712` comes back as `13835058055282164000`."""
    return isinstance(value, str) and bool(DECODE_KEY_RE.match(value))


@dataclass(frozen=True)
class Media:
    """One entry of `object.object_desc.media` from the JSAPI response."""

    url: str = ""
    url_token: str = ""
    decode_key: str = ""
    #: Whether `decode_key` was quoted in the JSAPI response - an M0 unknown, and worth
    #: recording: if it arrives unquoted, every JSON parser downstream is a hazard.
    decode_key_json_type: str = "unknown"
    file_size: int | None = None
    description: str = ""
    nickname: str = ""

    @property
    def download_url(self) -> str:
        """What the reference implementation downloads: `url` with `url_token` appended."""
        return self.url + self.url_token

    @property
    def decode_key_ok(self) -> bool:
        return is_valid_decode_key(self.decode_key)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Media:
        size = payload.get("file_size")
        if isinstance(size, str) and size.isdigit():
            size = int(size)
        elif not isinstance(size, int):
            size = None
        return cls(
            url=str(payload.get("url") or ""),
            url_token=str(payload.get("url_token") or ""),
            decode_key=str(payload.get("decode_key") or ""),
            decode_key_json_type=str(payload.get("decode_key_json_type") or "unknown"),
            file_size=size,
            description=str(payload.get("description") or ""),
            nickname=str(payload.get("nickname") or ""),
        )


@dataclass(frozen=True)
class Event:
    """One post from the hook script: a page probe, an invoke we saw, or a capture."""

    kind: str = KIND_INVOKE
    received_at: str = ""
    page_url: str = ""
    user_agent: str = ""
    api: str = ""
    err_msg: str = ""
    media: tuple[Media, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def captures(self) -> tuple[Media, ...]:
        return tuple(m for m in self.media if m.url and m.decode_key)


def parse_event(raw: bytes | str, *, received_at: str | None = None) -> Event:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CaptureError("body is not utf-8") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaptureError(f"body is not json: {exc}") from exc
    if not isinstance(payload, dict):
        raise CaptureError(f"expected a json object, got {type(payload).__name__}")

    media_payload = payload.get("media") or []
    if not isinstance(media_payload, list):
        raise CaptureError("media must be a list")

    return Event(
        kind=str(payload.get("kind") or KIND_INVOKE),
        received_at=received_at or datetime.now(UTC).isoformat(timespec="seconds"),
        page_url=str(payload.get("page_url") or ""),
        user_agent=str(payload.get("user_agent") or ""),
        api=str(payload.get("api") or ""),
        err_msg=str(payload.get("err_msg") or ""),
        media=tuple(Media.from_payload(m) for m in media_payload if isinstance(m, dict)),
        payload=payload,
    )


def format_event(event: Event) -> str:
    """One terminal line. Keeps the key visible - it is the whole point of the exercise -
    but not the description or the author."""
    head = f"[{event.kind}]"
    if event.api or event.err_msg:
        head += f" {event.api or '?'} err_msg={event.err_msg or '-'}"
    if event.kind == KIND_PROBE:
        bridge = event.payload.get("has_bridge")
        head += f" bridge={'yes' if bridge else 'no'} page={event.page_url or '-'}"
    for media in event.media:
        flag = "" if media.decode_key_ok else " !! not a digit string"
        head += (
            f"\n    decode_key={media.decode_key or '-'}"
            f" ({media.decode_key_json_type}){flag}"
            f" file_size={media.file_size if media.file_size is not None else '-'}"
            f"\n    url={media.download_url[:160] or '-'}"
        )
    return head


class CaptureLog:
    """Append-only JSONL under `captures/` (git-ignored - it is the user's own feed)."""

    def __init__(self, directory: Path, name: str = "captures") -> None:
        self.directory = Path(directory)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.path = self.directory / f"{name}-{stamp}.jsonl"

    def append(self, record: Mapping[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return self.path

    def append_event(self, event: Event) -> Path:
        record = {
            "received_at": event.received_at,
            "kind": event.kind,
            "api": event.api,
            "err_msg": event.err_msg,
            "page_url": event.page_url,
            "user_agent": event.user_agent,
            "media": [asdict(m) | {"download_url": m.download_url} for m in event.media],
            "payload": event.payload,
        }
        return self.append(record)
