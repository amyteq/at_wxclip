"""Light web server behind the admin and download dashboard.

The panel is deliberately thin. Every question it answers and every byte it fetches comes
from the modules the CLI already uses - `pipeline` to find what was captured, `downloader`
to fetch and decrypt it - so there is one implementation of each rather than two. What the
server adds is the part a terminal is bad at: showing a whole captured feed at once and
letting the operator pick out of it, which matters because one capture event carries the
entire visible feed (eight entries on the machine this was measured on).

Three decisions worth keeping:

* **A download request names a capture, never a URL.** The page posts back the bare `url`
  of an entry it was shown, and anything not in the capture logs is refused. The
  alternative - taking a URL from the client - would turn a page open in a browser into a
  way to make this process fetch an arbitrary address and write it under the download
  directory.
* **`decode_key` goes out as a JSON string, and never comes back.** It is a 64-bit
  integer: `JSON.parse` on a bare number silently rounds it, which is the whole reason the
  hook script lifts it out of the raw response text on the way in (see `wxclip.capture`).
  The page only ever displays it.
* **One download at a time**, for the reason `pipeline` gives: two 585 MB transfers racing
  each other help nobody.

Stdlib only, like the rest of the runtime - `http.server` is enough for a panel this thin
and keeps `wxclip` importable with no third-party packages at all.
"""

from __future__ import annotations

import json
import mimetypes
import re
import socket
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from wxclip import __version__, downloader, inject, pipeline
from wxclip.capture import Media
from wxclip.config import Settings
from wxclip.downloader import Progress, human_size

#: Files from `web/` the panel is allowed to ask for, by request path. Everything else is
#: a 404: this serves one page and its script, it is not a file server for `web/`.
ASSETS: dict[str, str] = {
    "/": "dashboard.html",
    "/dashboard.js": "dashboard.js",
}

#: Where a finished file is served from, so the browser can play or save it.
FILES_PREFIX = "/files/"

#: Long enough for a loopback connect to succeed, short enough that a poll of the state
#: endpoint does not visibly wait on it.
PROXY_PROBE_TIMEOUT = 0.25

#: A posted body larger than this is not a download request, whatever it is.
MAX_BODY = 64 << 10

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_SKIPPED = "skipped"

#: One range, no multipart - enough of RFC 9110 for a `<video>` element to seek.
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def echo_line(line: str) -> None:
    print(line, flush=True)


def port_open(host: str, port: int, timeout: float = PROXY_PROBE_TIMEOUT) -> bool:
    """Whether something accepts connections there.

    Not whether it is our proxy - but on a loopback port the operator chose themselves,
    that is the question worth answering for the price of one connect.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def parse_byte_range(header: str, size: int) -> tuple[int, int] | None:
    """`bytes=start-end` resolved against a known size, or None if unsatisfiable."""
    match = _RANGE_RE.match(header.strip().lower())
    if match is None or size == 0:
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not first:  # a suffix range: the final N bytes
        length = int(last)
        if length == 0:
            return None
        start, end = max(0, size - length), size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if start > end or start >= size:
        return None
    return start, end


@dataclass
class Job:
    """One download the panel asked for, and how far it has got."""

    id: int
    url: str
    name: str
    state: str = JOB_QUEUED
    file_size: int | None = None
    downloaded: int = 0
    total: int | None = None
    resumed_from: int = 0
    error: str = ""
    path: str = ""
    looks_like_mp4: bool | None = None
    started_at: str = ""
    finished_at: str = ""

    @property
    def fraction(self) -> float | None:
        total = self.total or self.file_size
        if not total:
            return None
        return min(1.0, self.downloaded / total)

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "name": self.name,
            "state": self.state,
            "file_size": self.file_size,
            "downloaded": self.downloaded,
            "total": self.total,
            "fraction": self.fraction,
            "human_downloaded": human_size(self.downloaded),
            "resumed_from": self.resumed_from,
            "error": self.error,
            "path": self.path,
            "looks_like_mp4": self.looks_like_mp4,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class Dashboard:
    """What the panel shows and what it can set going.

    Holds no captured data of its own: the capture logs on disk are the record, and they
    are re-read for each request. A panel left open therefore picks up a `wxclip proxy`
    running in another terminal without being told about it.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        download: Callable[..., Any] = downloader.download,
        echo: Callable[[str], None] = echo_line,
    ) -> None:
        self.settings = settings
        self._download = download
        self.echo = echo
        self._jobs: dict[int, Job] = {}
        self._job_for_url: dict[str, int] = {}
        self._next_id = 1
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wxclip-serve")

    # -- what was captured ---------------------------------------------------------------

    def media(self) -> list[Media]:
        """Every captured clip, newest capture first, one entry per clip.

        Identity is the bare `url`, as in `pipeline`: `url_token` is reissued on every
        capture of the same clip, so the copy worth keeping is the freshest one - an older
        entry's token may well have expired. Hence newest log first and newest capture
        event first within a log.

        Inside one event the order is left alone, because that is the feed order the
        operator saw in WeChat and the numbering `wxclip download --index` uses. Reversing
        there would leave row 0 of the panel meaning `--index 7` on the command line.
        """
        out: list[Media] = []
        seen: set[str] = set()
        for log in pipeline.capture_logs(self.settings.capture_dir):
            for record in reversed(pipeline.read_capture_log(log)):
                for media in pipeline.media_from_log([record], only_valid=False):
                    if not media.url or media.url in seen:
                        continue
                    seen.add(media.url)
                    out.append(media)
        return out

    def find(self, url: str) -> Media | None:
        """The captured entry for a bare `url`, which is all the panel is allowed to ask
        for - see the module docstring."""
        return next((m for m in self.media() if m.url == url), None)

    def destination(self, media: Media) -> Path:
        """Where a clip lands. Same name the CLI would give it, so the two see each
        other's files instead of downloading the same clip twice."""
        return Path(self.settings.download_dir) / downloader.suggest_filename(
            media.url, description=media.description, nickname=media.nickname
        )

    def media_json(self, media: Media) -> dict[str, Any]:
        dest = self.destination(media)
        return {
            "url": media.url,
            # A string on the wire, always: see the module docstring.
            "decode_key": media.decode_key,
            "decode_key_ok": media.decode_key_ok,
            "decode_key_json_type": media.decode_key_json_type,
            "file_size": media.file_size,
            "human_size": human_size(media.file_size) if media.file_size else "",
            "description": media.description,
            "nickname": media.nickname,
            "name": dest.name,
            "downloaded": dest.is_file(),
        }

    # -- what is on disk -----------------------------------------------------------------

    def downloads(self) -> list[dict[str, Any]]:
        directory = Path(self.settings.download_dir)
        if not directory.is_dir():
            return []
        out = []
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            if not path.is_file():
                continue
            stat = path.stat()
            out.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "human_size": human_size(stat.st_size),
                    "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(
                        timespec="seconds"
                    ),
                    # A `.part` is a download that was interrupted; the bytes in it are
                    # already decrypted, and the next attempt resumes from there.
                    "partial": path.name.endswith(downloader.PART_SUFFIX),
                }
            )
        return out

    def file_path(self, name: str) -> Path | None:
        """Resolve a name under the download directory, refusing anything that climbs
        out of it. Downloads are flat, so a resolved parent that is not the directory
        itself is reason enough to say no."""
        directory = Path(self.settings.download_dir).resolve()
        candidate = (directory / name).resolve()
        if candidate.parent != directory or not candidate.is_file():
            return None
        return candidate

    # -- the proxy, which is someone else's process --------------------------------------

    def proxy_status(self) -> dict[str, Any]:
        host, port = self.settings.proxy_host, self.settings.proxy_port
        return {
            "host": host,
            "port": port,
            "listening": port_open(host, port),
            "command": f"wxclip proxy --host {host} --port {port}",
        }

    # -- state and jobs ------------------------------------------------------------------

    def jobs(self) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in sorted(self._jobs, reverse=True)]

    def state(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "now": _now(),
            "proxy": self.proxy_status(),
            "capture_dir": str(self.settings.capture_dir),
            "download_dir": str(self.settings.download_dir),
            "logs": [str(p) for p in pipeline.capture_logs(self.settings.capture_dir)],
            "media": [self.media_json(m) for m in self.media()],
            "downloads": self.downloads(),
            "jobs": [job.as_json() for job in self.jobs()],
        }

    def start_download(self, url: str, *, overwrite: bool = False) -> Job:
        """Queue a captured clip. Raises LookupError for a url that was never captured
        and ValueError for one whose key cannot work."""
        media = self.find(url)
        if media is None:
            raise LookupError("that clip is not in the capture logs")
        if not media.decode_key_ok:
            raise ValueError(
                f"decode_key {media.decode_key!r} is not a plain digit string - "
                "something parsed it as a number and the keystream would be wrong"
            )

        dest = self.destination(media)
        with self._lock:
            running = self._jobs.get(self._job_for_url.get(media.url, -1))
            if running is not None and running.state in (JOB_QUEUED, JOB_RUNNING):
                return running  # a second click while the first is still going
            job = Job(
                id=self._next_id,
                url=media.url,
                name=dest.name,
                file_size=media.file_size,
                total=media.file_size,
            )
            self._next_id += 1
            self._jobs[job.id] = job
            self._job_for_url[media.url] = job.id

        if dest.is_file() and not overwrite:
            job.state = JOB_SKIPPED
            job.path = str(dest)
            job.downloaded = dest.stat().st_size
            job.finished_at = _now()
            return job

        self._pool.submit(self._fetch, job, media, dest, overwrite)
        return job

    def _fetch(self, job: Job, media: Media, dest: Path, overwrite: bool) -> None:
        job.state = JOB_RUNNING
        job.started_at = _now()
        size = f" ({human_size(media.file_size)})" if media.file_size else ""
        self.echo(f"-> downloading{size} to {dest}")

        def on_progress(progress: Progress) -> None:
            job.downloaded = progress.downloaded
            job.total = progress.total or job.total
            job.resumed_from = progress.resumed_from

        try:
            result = self._download(
                media.download_url,
                dest,
                decode_key=media.decode_key,
                expected_size=media.file_size,
                overwrite=overwrite,
                on_progress=on_progress,
            )
        except Exception as exc:  # one failed clip must not take the panel down
            job.state = JOB_FAILED
            job.error = str(exc)
            job.finished_at = _now()
            self.echo(f"!! download failed: {exc}")
            return

        job.state = JOB_DONE
        job.path = str(result.path)
        job.name = Path(result.path).name
        job.downloaded = result.size
        job.total = result.size
        job.looks_like_mp4 = result.looks_like_mp4
        job.finished_at = _now()
        self.echo(f"<- {result.summary()}")
        if not result.looks_like_mp4:
            self.echo("!! the decrypted head is not an mp4 box - the decode_key looks wrong")

    def close(self) -> None:
        """Let a download in flight finish; the file on disk is worth the wait."""
        self._pool.shutdown(wait=True)


class DashboardHandler(BaseHTTPRequestHandler):
    """Routes. Small enough to read in one go, which is the point of a panel this thin."""

    server_version = f"wxclip/{__version__}"
    protocol_version = "HTTP/1.1"  # so a `<video>` can keep a connection and seek

    @property
    def dashboard(self) -> Dashboard:
        return self.server.dashboard  # type: ignore[attr-defined]

    # -- routing -------------------------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ASSETS:
            self._send_asset(ASSETS[path])
        elif path == "/api/state":
            self._send_json(200, self.dashboard.state())
        elif path.startswith(FILES_PREFIX):
            self._send_file(unquote(path[len(FILES_PREFIX) :]))
        else:
            self._send_json(404, {"error": f"no such path: {path}"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/download":
            self._send_json(404, {"error": f"no such path: {path}"})
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        url = str(payload.get("url") or "")
        if not url:
            self._send_json(400, {"error": "which clip? post the url of a captured entry"})
            return
        try:
            job = self.dashboard.start_download(url, overwrite=bool(payload.get("overwrite")))
        except LookupError as exc:
            self._send_json(404, {"error": str(exc)})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        else:
            self._send_json(202, job.as_json())

    # -- responses -----------------------------------------------------------------------

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError as exc:
            raise ValueError("content-length is not a number") from exc
        if length > MAX_BODY:
            raise ValueError(f"body of {length} bytes is too large")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"body is not json: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"expected a json object, got {type(payload).__name__}")
        return payload

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send_asset(self, name: str) -> None:
        try:
            path = inject.asset_source(name)
        except FileNotFoundError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Both assets carry Chinese text, so the charset is not optional. `.js` maps to
        # application/javascript on 3.11 and text/javascript on 3.12, hence both checks.
        textual = guessed.startswith("text/") or "javascript" in guessed
        self._send(200, path.read_bytes(), guessed + ("; charset=utf-8" if textual else ""))

    def _send_file(self, name: str) -> None:
        """Serve a finished download, so the browser can play it or save it.

        Range matters here: without it a `<video>` element plays from the start but
        cannot seek, and a 585 MB clip is exactly the case where that is noticed.
        """
        path = self.dashboard.file_path(name)
        if path is None:
            self._send_json(404, {"error": f"no such download: {name}"})
            return
        size = path.stat().st_size
        start, end, status = 0, max(0, size - 1), 200
        header = self.headers.get("range")
        if header:
            resolved = parse_byte_range(header, size)
            if resolved is None:
                self._send(
                    416, b"", "text/plain; charset=utf-8", {"content-range": f"bytes */{size}"}
                )
                return
            start, end = resolved
            status = 206

        length = end - start + 1 if size else 0
        guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("content-type", guessed)
        self.send_header("content-length", str(length))
        self.send_header("accept-ranges", "bytes")
        if status == 206:
            self.send_header("content-range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(downloader.CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # -- logging -------------------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """Silence. The page polls the state endpoint, so a line per request is a flood;
        anything that actually went wrong still arrives through `log_error`."""

    def log_error(self, format: str, *args: Any) -> None:
        self.dashboard.echo(f"!! {format % args}")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], dashboard: Dashboard) -> None:
        super().__init__(address, DashboardHandler)
        self.dashboard = dashboard


def make_server(
    settings: Settings,
    *,
    download: Callable[..., Any] = downloader.download,
    echo: Callable[[str], None] = echo_line,
) -> DashboardServer:
    """A server bound and ready to serve. Port 0 picks a free one, which is how the tests
    avoid caring what is already listening."""
    dashboard = Dashboard(settings, download=download, echo=echo)
    return DashboardServer((settings.server_host, settings.server_port), dashboard)


def serve(
    settings: Settings,
    *,
    download: Callable[..., Any] = downloader.download,
    echo: Callable[[str], None] = echo_line,
) -> int:
    """Run the panel until interrupted. Returns a process exit code."""
    server = make_server(settings, download=download, echo=echo)
    host, port = server.server_address[0], server.server_address[1]
    echo(f"wxclip dashboard on http://{host}:{port}\npress ctrl-c to stop")
    proxy = server.dashboard.proxy_status()
    if not proxy["listening"]:
        echo(f"-- nothing on {proxy['host']}:{proxy['port']} yet; run: {proxy['command']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        echo("")
    finally:
        server.shutdown()
        server.server_close()
        server.dashboard.close()
    return 0
