"""Direct download of a captured media URL - deliberately not through the proxy.

The proxy exists to get the decode_key out of the page; it has no business carrying
hundreds of megabytes of video afterwards. So this module talks to the CDN itself, and
three of the observed facts shape it (see `evals/jsapi-response-linux-20260911.md`):

* `file_size` ran from 3.8 MB to 585 MB in one capture, so the body is streamed to disk
  in chunks and never held in memory - the M0 one-shot tool read it whole and must not be
  copied here;
* the response is one whole file, not a playlist, so a byte range resumes it cleanly;
* only the first 128 KiB are encrypted, and they are decrypted on the way past, so what
  lands on disk is already playable and a resume just has to say how far in it is.

Downloads land on a `.part` file and are renamed into place once complete, so an
interrupted run leaves something resumable and never something that looks finished.
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wx_dlh.decrypt import HEAD_SAMPLE, HeadDecryptor, looks_like_mp4
from wx_dlh.keystream import HEAD_BYTES

#: The reference implementation asks as desktop Chrome. Keep it: the CDN is not obliged
#: to serve a default urllib user agent, and this is the combination known to work.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
)

#: Read size. Small enough that progress moves, large enough to stay out of the way.
CHUNK_SIZE = 1 << 16

#: Suffix of the file being written. Renamed away only once the byte count checks out.
PART_SUFFIX = ".part"

DEFAULT_TIMEOUT = 30.0

_UNSAFE_NAME = re.compile(r"[^\w.\- ]+", re.UNICODE)
_RUNS_OF_SPACE = re.compile(r"\s+")


class DownloadError(RuntimeError):
    """The download could not be completed. A `.part` file may be left to resume from."""


def slugify(text: str, *, limit: int = 60) -> str:
    """A file-name fragment: keeps letters (Chinese included), drops everything else."""
    cleaned = _RUNS_OF_SPACE.sub(" ", _UNSAFE_NAME.sub(" ", text)).strip()
    return cleaned.replace(" ", "-")[:limit].strip("-.")


def suggest_filename(
    url: str, *, description: str = "", nickname: str = "", suffix: str = ".mp4"
) -> str:
    """Name a clip by what it is, and end every name with a digest of its URL.

    The digest is not decoration. It makes the name unique (two clips can share a
    description) and stable (the same clip captured again gets the same name), which is
    what lets a second `wx-dlh capture` say "already downloaded" instead of fetching 585 MB
    again. Both `description` and `nickname` came back empty on Linux, so `video-<digest>`
    is the common case rather than the exception.

    Pass the media's bare `url`, never `download_url`: the token is reissued on every
    capture of the same video, and hashing it would give one clip a new name each time.
    """
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    for candidate in (description, nickname):
        slug = slugify(candidate)
        if slug:
            return f"{slug}-{digest}{suffix}"
    return f"video-{digest}{suffix}"


@dataclass(frozen=True)
class Progress:
    """Where a running download has got to."""

    downloaded: int
    total: int | None
    resumed_from: int
    elapsed: float

    @property
    def fraction(self) -> float | None:
        if not self.total:
            return None
        return min(1.0, self.downloaded / self.total)

    @property
    def bytes_per_second(self) -> float:
        fresh = self.downloaded - self.resumed_from
        return fresh / self.elapsed if self.elapsed > 0 else 0.0

    def line(self) -> str:
        """One terminal line, meant to be reprinted over itself."""
        done = human_size(self.downloaded)
        rate = f"{human_size(int(self.bytes_per_second))}/s"
        if self.total:
            return f"{self.fraction:6.1%}  {done} / {human_size(self.total)}  {rate}"
        return f"        {done}  {rate}"


#: Suffixes `--max-size` understands. Both `100M` and `100MiB` mean 1024-based megabytes;
#: nothing here is measured precisely enough for the decimal reading to be worth the
#: confusion.
_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1 << 10,
    "kb": 1 << 10,
    "kib": 1 << 10,
    "m": 1 << 20,
    "mb": 1 << 20,
    "mib": 1 << 20,
    "g": 1 << 30,
    "gb": 1 << 30,
    "gib": 1 << 30,
}


def parse_size(text: str) -> int:
    """`"600m"` -> 629145600. Raises ValueError on anything else."""
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)\s*", text)
    unit = _SIZE_UNITS.get(match.group(2).lower()) if match else None
    if unit is None:
        raise ValueError(f"{text!r} is not a size; try 600m, 2g or a plain byte count")
    return int(float(match.group(1)) * unit)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"  # pragma: no cover - the loop above always returns


@dataclass(frozen=True)
class DownloadResult:
    """A finished file, and enough detail to tell whether it is the right one."""

    path: Path
    size: int
    resumed_from: int
    decrypted: int
    head: bytes
    elapsed: float
    expected_size: int | None = None

    @property
    def looks_like_mp4(self) -> bool:
        """False either because nothing was decrypted or because the key was wrong."""
        return looks_like_mp4(self.head)

    @property
    def size_matches(self) -> bool | None:
        """None when the capture did not say how large the file should be."""
        if self.expected_size is None:
            return None
        return self.size == self.expected_size

    def summary(self) -> str:
        parts = [f"{self.path}: {human_size(self.size)} ({self.size} bytes)"]
        if self.resumed_from:
            parts.append(f"resumed from {human_size(self.resumed_from)}")
        if self.decrypted:
            verdict = "mp4" if self.looks_like_mp4 else "NOT an mp4 header - wrong decode_key?"
            parts.append(f"decrypted {self.decrypted} bytes, head {self.head.hex()} ({verdict})")
        if self.size_matches is False:
            parts.append(f"!! file_size said {self.expected_size}")
        return "\n  ".join(parts)


def _content_length(headers: Any) -> int | None:
    raw = headers.get("content-length")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def download(
    url: str,
    dest: Path,
    *,
    decode_key: str | None = None,
    expected_size: int | None = None,
    resume: bool = True,
    overwrite: bool = False,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = DEFAULT_TIMEOUT,
    chunk_size: int = CHUNK_SIZE,
    head_bytes: int = HEAD_BYTES,
    on_progress: Callable[[Progress], None] | None = None,
    progress_interval: float = 0.25,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> DownloadResult:
    """Fetch `url` into `dest`, decrypting the head with `decode_key` on the way.

    Returns once the file is complete and renamed into place. Anything short of that
    raises `DownloadError` and leaves the `.part` file for the next attempt.
    """
    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise DownloadError(f"{dest} already exists (pass overwrite to replace it)")
    part = dest.with_name(dest.name + PART_SUFFIX)
    dest.parent.mkdir(parents=True, exist_ok=True)

    start_at = part.stat().st_size if resume and part.is_file() else 0
    if not resume:
        part.unlink(missing_ok=True)

    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    if start_at:
        request.add_header("Range", f"bytes={start_at}-")

    started = time.monotonic()
    try:
        response = urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # 416 means the range is past the end - the file is already whole, or the .part
        # is longer than the resource and has to go.
        if exc.code == 416 and start_at:
            if expected_size is not None and start_at == expected_size:
                part.replace(dest)
                with dest.open("rb") as fh:
                    head = fh.read(HEAD_SAMPLE)
                elapsed = time.monotonic() - started
                return DownloadResult(dest, start_at, start_at, 0, head, elapsed, expected_size)
            raise DownloadError(
                f"the server rejected a resume from {start_at} bytes; "
                f"delete {part} and download again"
            ) from exc
        raise DownloadError(f"HTTP {exc.code} {exc.reason} for {url}") from exc
    except OSError as exc:
        raise DownloadError(f"could not reach {url}: {exc}") from exc

    with response:
        status = getattr(response, "status", None) or response.getcode()
        if start_at and status != 206:
            # The range header was ignored: the body starts at zero, so must we.
            start_at = 0
            part.unlink(missing_ok=True)
        length = _content_length(response.headers)
        total = start_at + length if length is not None else None

        decryptor = (
            HeadDecryptor(decode_key, offset=start_at, head_bytes=head_bytes)
            if decode_key
            else None
        )
        head = b""
        downloaded = start_at
        last_report = 0.0

        with part.open("ab" if start_at else "wb") as fh:
            while chunk := response.read(chunk_size):
                if decryptor is not None:
                    chunk = decryptor.update(chunk)
                if not head and downloaded == 0:
                    head = chunk[:HEAD_SAMPLE]
                fh.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if on_progress is not None and now - last_report >= progress_interval:
                    last_report = now
                    on_progress(Progress(downloaded, total, start_at, now - started))

    elapsed = time.monotonic() - started
    if on_progress is not None:
        on_progress(Progress(downloaded, total, start_at, elapsed))

    if total is not None and downloaded != total:
        raise DownloadError(
            f"download ended at {downloaded} of {total} bytes; {part} can be resumed"
        )

    part.replace(dest)
    if not head:  # a pure resume never saw byte zero
        with dest.open("rb") as fh:
            head = fh.read(HEAD_SAMPLE)
    decrypted = min(downloaded, head_bytes) if decryptor is not None else 0
    return DownloadResult(dest, downloaded, start_at, decrypted, head, elapsed, expected_size)
