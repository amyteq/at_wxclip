"""From a capture event to a playable file: pick the media, download it, decrypt it.

`wxclip capture` is `wxclip proxy` with this bolted on. The proxy hands every event the
hook script posts to `CaptureRunner.on_event`, which runs in mitmproxy's event loop, so
all it does is choose and enqueue; the downloading happens on one worker thread, one file
at a time, because two 585 MB downloads racing each other help nobody.

Choosing is the interesting part. A single capture event carries the whole visible feed -
eight `media[]` entries on the machine this was measured on, not the one the reference
implementation assumes - and only the operator knows which one they just played. The
default is therefore the same as the reference implementation's, `media[0]`, with
`--index` and `--all` for the times it is not.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from wxclip import downloader
from wxclip.capture import Event, Media
from wxclip.downloader import DownloadResult, Progress, human_size


def echo_line(line: str) -> None:
    print(line, flush=True)


class CaptureRunner:
    """Turns capture events into finished files, and knows when it has enough.

    `stop_after` downloads are all it waits for; once they are in, `on_finished` fires and
    the caller can stop the proxy. Zero means never stop on its own.
    """

    def __init__(
        self,
        dest_dir: Path,
        *,
        index: int = 0,
        all_media: bool = False,
        max_size: int | None = None,
        stop_after: int = 1,
        overwrite: bool = False,
        echo: Callable[[str], None] = echo_line,
        on_progress: Callable[[Media, Progress], None] | None = None,
        download: Callable[..., DownloadResult] = downloader.download,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        self.dest_dir = Path(dest_dir)
        self.index = index
        self.all_media = all_media
        self.max_size = max_size
        self.stop_after = stop_after
        self.overwrite = overwrite
        self.echo = echo
        self.on_progress = on_progress
        self.download = download
        self.on_finished = on_finished

        self.results: list[DownloadResult] = []
        self.failures: list[tuple[Media, Exception]] = []
        self.finished = threading.Event()
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wxclip-download")

    # -- selection -----------------------------------------------------------------------

    def choose(self, event: Event) -> list[Media]:
        """Which of an event's media entries to fetch, ignoring ones already taken.

        Deduplication is by the bare `url`: `url_token` is reissued for every capture of
        the same clip, so the full download URL would look new each time round the feed.
        """
        available = list(event.captures)
        if not available:
            return []
        if self.all_media:
            wanted = available
        elif self.index < len(available):
            wanted = [available[self.index]]
        else:
            self.echo(
                f"!! --index {self.index} but this capture carried "
                f"{len(available)} media entries; skipping"
            )
            return []

        chosen = []
        for media in wanted:
            if not media.decode_key_ok:
                self.echo(f"!! skipping {media.url[:80]}: decode_key {media.decode_key!r} is bad")
                continue
            if self.max_size is not None and (media.file_size or 0) > self.max_size:
                self.echo(
                    f"-- skipping {human_size(media.file_size or 0)} clip "
                    f"(over --max-size {human_size(self.max_size)})"
                )
                continue
            with self._lock:
                if media.url in self._seen:
                    continue
                self._seen.add(media.url)
            chosen.append(media)
        return chosen

    def destination(self, media: Media) -> Path:
        """Where a clip lands. The name is derived from the clip, not from the clock, so
        capturing the same video twice finds the file already there instead of fetching
        it again."""
        return self.dest_dir / downloader.suggest_filename(
            media.url, description=media.description, nickname=media.nickname
        )

    # -- the proxy's entry point ---------------------------------------------------------

    def on_event(self, event: Event) -> None:
        """Called from the proxy's event loop: choose and enqueue, never block."""
        for media in self.choose(event):
            self._pool.submit(self._fetch, media)

    def _fetch(self, media: Media) -> None:
        dest = self.destination(media)
        if dest.exists() and not self.overwrite:
            self.echo(f"-- already downloaded: {dest}")
            return
        size = f" ({human_size(media.file_size)})" if media.file_size else ""
        self.echo(f"-> downloading{size} to {dest}")
        try:
            result = self.download(
                media.download_url,
                dest,
                decode_key=media.decode_key,
                expected_size=media.file_size,
                overwrite=self.overwrite,
                on_progress=(
                    (lambda progress, m=media: self.on_progress(m, progress))
                    if self.on_progress is not None
                    else None
                ),
            )
        except Exception as exc:  # a failed clip must not take the proxy down with it
            self.failures.append((media, exc))
            self.echo(f"!! download failed: {exc}")
            return
        self.results.append(result)
        self.echo(f"<- {result.summary()}")
        if not result.looks_like_mp4:
            self.echo("!! the decrypted head is not an mp4 box - the decode_key looks wrong")
        if self.stop_after and len(self.results) >= self.stop_after:
            self.finished.set()
            if self.on_finished is not None:
                self.on_finished()

    # -- lifecycle -----------------------------------------------------------------------

    def wait(self, timeout: float | None = None) -> bool:
        return self.finished.wait(timeout)

    def close(self) -> None:
        """Let the download in flight finish; the file on disk is worth the wait."""
        self._pool.shutdown(wait=True)

    def summary(self) -> str:
        lines = [f"downloaded {len(self.results)} file(s)"]
        lines += [f"  {result.path}" for result in self.results]
        if self.failures:
            lines.append(f"{len(self.failures)} failed:")
            lines += [f"  {media.url[:80]}: {exc}" for media, exc in self.failures]
        return "\n".join(lines)


def capture_logs(capture_dir: Path, name: str = "captures") -> list[Path]:
    """Every `captures/<name>-*.jsonl`, most recently written first.

    The CLI wants only the newest one; the dashboard lists them all, which is why the
    glob lives here rather than in either caller.
    """
    return sorted(
        Path(capture_dir).glob(f"{name}-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )


def newest_capture_log(capture_dir: Path, name: str = "captures") -> Path | None:
    """The most recently written `captures/captures-*.jsonl`, if there is one."""
    logs = capture_logs(capture_dir, name)
    return logs[0] if logs else None


def read_capture_log(path: Path) -> list[dict]:
    """Parse a capture log, skipping lines a crash left half-written."""
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def media_from_log(records: Sequence[dict], *, only_valid: bool = True) -> list[Media]:
    """Every media entry in a `captures/captures-*.jsonl` feed, in the order recorded.

    This is how `wxclip download --from-capture` finds a URL again without making the
    operator copy a 600-character link out of a log by hand.
    """
    out: list[Media] = []
    for record in records:
        for payload in record.get("media") or []:
            if not isinstance(payload, dict):
                continue
            media = Media.from_payload(payload)
            if only_valid and not (media.url and media.decode_key_ok):
                continue
            out.append(media)
    return out
