"""The dashboard: what it shows, what it refuses, and what it hands back.

These drive a real server on a port the OS picks, because the things worth testing here
are HTTP-shaped: the exact bytes `decode_key` goes out as, a range request, a file name
that tries to climb out of the download directory. The downloader itself is a stand-in -
`tests/test_downloader.py` covers the real one.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wx_dlh.config import Settings
from wx_dlh.downloader import DownloadResult
from wx_dlh.server import Dashboard, make_server, parse_byte_range

KEY = "1234567890"
#: The key from the background notes: 20 digits, and a float rounds it to
#: 13835058055282164000 without complaining.
BIG_KEY = "13835058055282163712"
MP4_HEAD = b"\x00\x00\x00\x20ftypisom"

#: Nothing listens on port 1, which is what the proxy status should report.
CLOSED_PORT = 1


def media_payload(n: int, *, key: str = KEY, size: int = 4_000_000, token: str = "") -> dict:
    return {
        "url": f"https://finder.video.qq.com/251/clip{n}",
        "url_token": token or f"?token={n}",
        "decode_key": key,
        "decode_key_json_type": "string",
        "file_size": size,
    }


def write_log(directory: Path, name: str, media: list[dict], *, mtime: float | None = None) -> Path:
    """One capture event, the way the proxy records it: the whole visible feed at once."""
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / name
    log.write_text(
        json.dumps({"kind": "capture", "api": "finderH5ExtTransfer", "media": media}) + "\n",
        encoding="utf-8",
    )
    if mtime is not None:
        os.utime(log, (mtime, mtime))
    return log


class Recorder:
    """Stands in for downloader.download and remembers what it was asked for."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, url, dest, **kw):
        self.calls.append({"url": url, "dest": Path(dest), **kw})
        if self.fail:
            raise RuntimeError("the CDN said no")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(MP4_HEAD)
        return DownloadResult(
            Path(dest), len(MP4_HEAD), 0, len(MP4_HEAD), MP4_HEAD, 0.1, kw.get("expected_size")
        )


class Panel:
    """A client for the panel under test."""

    def __init__(self, server, recorder: Recorder) -> None:
        self.server = server
        self.recorder = recorder
        self.base = f"http://{server.server_address[0]}:{server.server_address[1]}"

    def get(self, path: str, headers: dict[str, str] | None = None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def post(self, path: str, payload: dict):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def state(self) -> dict:
        status, _, body = self.get("/api/state")
        assert status == 200
        return json.loads(body)

    def raw_state(self) -> str:
        return self.get("/api/state")[2].decode("utf-8")

    def wait_for(self, state: str, timeout: float = 5.0) -> dict:
        """Poll until the newest job reaches `state` - it runs on a worker thread."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            jobs = self.state()["jobs"]
            if jobs and jobs[0]["state"] == state:
                return jobs[0]
            time.sleep(0.02)
        raise AssertionError(f"no job reached {state!r}: {self.state()['jobs']}")


@pytest.fixture
def captures(tmp_path: Path) -> Path:
    return tmp_path / "captures"


@pytest.fixture
def downloads(tmp_path: Path) -> Path:
    return tmp_path / "downloads"


@pytest.fixture
def settings(captures: Path, downloads: Path) -> Settings:
    return Settings(
        proxy_host="127.0.0.1",
        proxy_port=CLOSED_PORT,
        server_host="127.0.0.1",
        server_port=0,  # the OS picks, so the suite never fights for a port
        download_dir=downloads,
        capture_dir=captures,
    )


@pytest.fixture
def panel(settings: Settings):
    """Starts the panel. Capture logs can be written before or after: the dashboard
    re-reads them per request, which is how it notices a proxy in another terminal."""
    started = []

    def start(*, fail: bool = False) -> Panel:
        recorder = Recorder(fail=fail)
        server = make_server(settings, download=recorder, echo=lambda line: None)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return Panel(server, recorder)

    yield start

    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        server.dashboard.close()


# -- what the panel shows ----------------------------------------------------------------


def test_state_lists_the_whole_captured_feed(panel, captures):
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(n) for n in range(8)])
    state = panel().state()

    assert len(state["media"]) == 8  # one capture carries the feed, not one clip
    first = state["media"][0]
    assert first["human_size"] == "3.8 MiB"
    assert first["decode_key_ok"] is True
    assert first["downloaded"] is False
    assert first["name"].endswith(".mp4")
    assert len(state["logs"]) == 1


def test_state_reports_a_proxy_that_is_not_running(panel):
    proxy = panel().state()["proxy"]
    assert proxy["listening"] is False
    assert proxy["port"] == CLOSED_PORT
    assert "wx-dlh proxy" in proxy["command"]


def test_an_empty_capture_directory_is_not_an_error(panel):
    state = panel().state()
    assert state["media"] == []
    assert state["logs"] == []
    assert state["downloads"] == []


def test_decode_key_goes_out_as_a_quoted_string(panel, captures):
    """The one thing this endpoint must never get wrong: a bare number in the JSON would
    be rounded by JSON.parse in the page, and the keystream would be unrelated."""
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0, key=BIG_KEY)])
    live = panel()

    assert f'"decode_key": "{BIG_KEY}"' in live.raw_state()
    value = live.state()["media"][0]["decode_key"]
    assert isinstance(value, str)
    assert value == BIG_KEY  # not 13835058055282164000


def test_the_newest_capture_of_a_clip_is_the_one_offered(panel, captures):
    """`url_token` is reissued every capture, so an older entry's token may be dead.
    Identity is the bare url; the freshest token wins."""
    shared = "https://finder.video.qq.com/251/clip0"
    old = media_payload(0, token="?token=old") | {"url": shared}
    new = media_payload(0, token="?token=new") | {"url": shared}
    write_log(captures, "captures-20260911-100000.jsonl", [old], mtime=1_000_000)
    write_log(captures, "captures-20260911-120000.jsonl", [new], mtime=2_000_000)

    live = panel()
    state = live.state()
    assert len(state["media"]) == 1  # one clip, not two

    status, _ = live.post("/api/download", {"url": shared})
    assert status == 202
    live.wait_for("done")
    assert live.recorder.calls[0]["url"] == shared + "?token=new"


def test_the_page_and_its_script_are_served_with_a_charset(panel):
    live = panel()
    status, headers, body = live.get("/")
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert b"wx-dlh" in body

    status, headers, body = live.get("/dashboard.js")
    assert status == 200
    assert "charset=utf-8" in headers["content-type"]  # the script carries Chinese text
    assert b"/api/state" in body


def test_an_unknown_path_is_a_json_404(panel):
    status, _, body = panel().get("/nope")
    assert status == 404
    assert "no such path" in json.loads(body)["error"]


# -- starting a download -----------------------------------------------------------------


def test_a_download_runs_and_lands_a_file(panel, captures, downloads):
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0)])
    live = panel()
    entry = live.state()["media"][0]

    status, job = live.post("/api/download", {"url": entry["url"]})
    assert status == 202
    assert job["state"] in ("queued", "running")

    done = live.wait_for("done")
    assert done["name"] == entry["name"]
    assert done["looks_like_mp4"] is True
    assert (downloads / entry["name"]).read_bytes() == MP4_HEAD

    call = live.recorder.calls[0]
    assert call["url"] == entry["url"] + "?token=0"  # url + url_token, as captured
    assert call["decode_key"] == KEY
    assert isinstance(call["decode_key"], str)  # a string the whole way down
    assert call["expected_size"] == 4_000_000

    assert live.state()["media"][0]["downloaded"] is True


def test_a_clip_that_was_never_captured_is_refused(panel, captures):
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0)])
    status, payload = panel().post("/api/download", {"url": "https://evil.example/pwn.bin"})
    assert status == 404
    assert "not in the capture logs" in payload["error"]


def test_a_download_with_no_url_says_what_is_missing(panel):
    status, payload = panel().post("/api/download", {})
    assert status == 400
    assert "which clip" in payload["error"]


def test_a_key_that_went_through_a_float_is_refused(panel, captures):
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0, key="1.38e+19")])
    live = panel()
    entry = live.state()["media"][0]
    assert entry["decode_key_ok"] is False

    status, payload = live.post("/api/download", {"url": entry["url"]})
    assert status == 400
    assert "not a plain digit string" in payload["error"]
    assert live.recorder.calls == []


def test_a_failed_download_is_reported_and_the_panel_survives(panel, captures):
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0)])
    live = panel(fail=True)
    entry = live.state()["media"][0]

    live.post("/api/download", {"url": entry["url"]})
    failed = live.wait_for("failed")
    assert "the CDN said no" in failed["error"]
    assert live.state()["media"][0]["downloaded"] is False  # nothing was left behind


def test_asking_again_for_a_file_already_there_skips_it(panel, captures):
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0)])
    live = panel()
    entry = live.state()["media"][0]

    live.post("/api/download", {"url": entry["url"]})
    live.wait_for("done")

    status, job = live.post("/api/download", {"url": entry["url"]})
    assert status == 202
    assert job["state"] == "skipped"
    assert len(live.recorder.calls) == 1  # not fetched twice


def test_overwrite_downloads_it_again(panel, captures):
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0)])
    live = panel()
    entry = live.state()["media"][0]

    live.post("/api/download", {"url": entry["url"]})
    live.wait_for("done")
    live.post("/api/download", {"url": entry["url"], "overwrite": True})
    live.wait_for("done")

    assert len(live.recorder.calls) == 2
    assert live.recorder.calls[1]["overwrite"] is True


def test_a_second_request_while_one_runs_returns_the_same_job(settings, captures):
    """One download at a time, and a double click must not queue a second copy."""
    write_log(captures, "captures-20260911-120000.jsonl", [media_payload(0)])
    gate = threading.Event()

    def blocking(url, dest, **kw):
        assert gate.wait(5)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(MP4_HEAD)
        return DownloadResult(Path(dest), 8, 0, 8, MP4_HEAD, 0.1, None)

    dashboard = Dashboard(settings, download=blocking, echo=lambda line: None)
    try:
        url = dashboard.media()[0].url
        first = dashboard.start_download(url)
        second = dashboard.start_download(url)
        assert second.id == first.id
    finally:
        gate.set()
        dashboard.close()


# -- serving what was downloaded ---------------------------------------------------------


def test_a_finished_file_is_served_whole(panel, downloads):
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "clip.mp4").write_bytes(MP4_HEAD)

    status, headers, body = panel().get("/files/clip.mp4")
    assert status == 200
    assert body == MP4_HEAD
    assert headers["content-type"] == "video/mp4"
    assert headers["accept-ranges"] == "bytes"


def test_a_range_request_is_answered_with_that_range(panel, downloads):
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "clip.mp4").write_bytes(bytes(range(32)))

    status, headers, body = panel().get("/files/clip.mp4", {"Range": "bytes=4-7"})
    assert status == 206
    assert body == bytes(range(4, 8))
    assert headers["content-range"] == "bytes 4-7/32"
    assert headers["content-length"] == "4"


def test_an_unsatisfiable_range_is_a_416(panel, downloads):
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "clip.mp4").write_bytes(MP4_HEAD)

    status, headers, _ = panel().get("/files/clip.mp4", {"Range": "bytes=999-"})
    assert status == 416
    assert headers["content-range"] == f"bytes */{len(MP4_HEAD)}"


def test_a_missing_download_is_a_404(panel):
    status, _, body = panel().get("/files/nope.mp4")
    assert status == 404
    assert "no such download" in json.loads(body)["error"]


def test_a_name_that_climbs_out_of_the_download_directory_is_refused(panel, downloads, tmp_path):
    downloads.mkdir(parents=True, exist_ok=True)
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")

    status, _, _ = panel().get("/files/..%2Fsecret.txt")
    assert status == 404


@pytest.mark.parametrize("name", ["../secret.txt", "sub/clip.mp4", "", "/etc/hostname"])
def test_file_path_only_resolves_inside_the_download_directory(settings, downloads, name):
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads.parent / "secret.txt").write_text("not yours", encoding="utf-8")
    (downloads / "clip.mp4").write_bytes(MP4_HEAD)
    dashboard = Dashboard(settings, echo=lambda line: None)

    assert dashboard.file_path(name) is None
    assert dashboard.file_path("clip.mp4") == (downloads / "clip.mp4").resolve()


def test_the_listing_flags_a_part_file_as_resumable(panel, downloads):
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "clip.mp4").write_bytes(MP4_HEAD)
    (downloads / "other.mp4.part").write_bytes(b"half of it")

    listing = {entry["name"]: entry for entry in panel().state()["downloads"]}
    assert listing["clip.mp4"]["partial"] is False
    assert listing["other.mp4.part"]["partial"] is True
    assert listing["clip.mp4"]["human_size"] == "12 B"


# -- range parsing -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-", 100, (0, 99)),
        ("bytes=10-19", 100, (10, 19)),
        ("bytes=90-200", 100, (90, 99)),  # clamped to what exists
        ("bytes=-10", 100, (90, 99)),  # the last ten bytes
        ("bytes=-500", 100, (0, 99)),
        ("bytes=100-", 100, None),  # first byte past the end
        ("bytes=20-10", 100, None),
        ("bytes=-0", 100, None),
        ("bytes=", 100, None),
        ("items=0-9", 100, None),
        ("bytes=0-", 0, None),  # nothing to serve
    ],
)
def test_parse_byte_range(header, size, expected):
    assert parse_byte_range(header, size) == expected
