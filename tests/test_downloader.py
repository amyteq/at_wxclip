"""The downloader, against a real HTTP server on localhost.

A stubbed `urlopen` would not catch the parts that actually go wrong - byte ranges, a
server that ignores one, a body that stops early - so the resume tests use a small
handler that speaks 206 the way a CDN does. Faked responses are kept for the failures
that are awkward to provoke for real.
"""

import http.server
import socketserver
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from wx_dlh.downloader import (
    DownloadError,
    Progress,
    download,
    human_size,
    parse_size,
    suggest_filename,
)
from wx_dlh.keystream import HEAD_BYTES, keystream

KEY = "1234567890"
MP4_HEAD = b"\x00\x00\x00\x20ftypisom"


def plaintext(size: int) -> bytes:
    return (MP4_HEAD + bytes(range(256)) * (size // 256 + 1))[:size]


def ciphertext(plain: bytes, key: str = KEY) -> bytes:
    stream = keystream(key)
    return bytes(b ^ (stream[i] if i < HEAD_BYTES else 0) for i, b in enumerate(plain))


PLAIN = plaintext(HEAD_BYTES + 2500)
CIPHER = ciphertext(PLAIN)


class _Handler(http.server.BaseHTTPRequestHandler):
    body = CIPHER
    honour_range = True
    truncate_to: int | None = None

    def log_message(self, *args):  # keep pytest output clean
        pass

    def do_GET(self):
        header = self.headers.get("Range")
        start = 0
        if header and self.honour_range:
            start = int(header.partition("=")[2].partition("-")[0])
            if start >= len(self.body):
                self.send_error(416)
                return
        chunk = self.body[start:]
        self.send_response(206 if (header and self.honour_range) else 200)
        if header and self.honour_range:
            self.send_header(
                "Content-Range", f"bytes {start}-{len(self.body) - 1}/{len(self.body)}"
            )
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk[: self.truncate_to] if self.truncate_to else chunk)


@pytest.fixture
def server():
    """A localhost CDN stand-in. `server.handler` tweaks how it behaves."""
    handler = type("Handler", (_Handler,), {})
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{httpd.server_address[1]}/251/stodownload?token=x",
        handler=handler,
    )
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_downloads_and_decrypts_in_one_pass(tmp_path, server):
    result = download(server.url, tmp_path / "clip.mp4", decode_key=KEY, expected_size=len(CIPHER))
    assert result.path.read_bytes() == PLAIN
    assert result.size == len(CIPHER)
    assert result.decrypted == HEAD_BYTES
    assert result.looks_like_mp4
    assert result.size_matches is True
    assert not list(tmp_path.glob("*.part"))


def test_without_a_key_the_bytes_are_left_as_served(tmp_path, server):
    result = download(server.url, tmp_path / "raw.bin")
    assert result.path.read_bytes() == CIPHER
    assert result.decrypted == 0
    assert not result.looks_like_mp4


@pytest.mark.parametrize("already", [1, 4096, HEAD_BYTES - 1, HEAD_BYTES, HEAD_BYTES + 100])
def test_resumes_from_a_part_file_at_any_offset(tmp_path, server, already):
    """The .part file holds decrypted bytes, so the keystream has to resume at `already`,
    not at zero - the bug this test exists for produces a file that plays as noise."""
    part = tmp_path / "clip.mp4.part"
    part.write_bytes(PLAIN[:already])
    result = download(server.url, tmp_path / "clip.mp4", decode_key=KEY)
    assert result.path.read_bytes() == PLAIN
    assert result.resumed_from == already


def test_no_resume_starts_over(tmp_path, server):
    (tmp_path / "clip.mp4.part").write_bytes(b"stale rubbish")
    result = download(server.url, tmp_path / "clip.mp4", decode_key=KEY, resume=False)
    assert result.resumed_from == 0
    assert result.path.read_bytes() == PLAIN


def test_a_server_that_ignores_the_range_makes_us_start_over(tmp_path, server):
    """Answering 200 to a Range request means the body starts at zero; appending it to
    the .part file would silently produce a file that is too long and unplayable."""
    server.handler.honour_range = False
    (tmp_path / "clip.mp4.part").write_bytes(PLAIN[:5000])
    result = download(server.url, tmp_path / "clip.mp4", decode_key=KEY)
    assert result.resumed_from == 0
    assert result.path.read_bytes() == PLAIN


def test_a_complete_part_file_is_accepted_when_the_server_says_416(tmp_path, server):
    part = tmp_path / "clip.mp4.part"
    part.write_bytes(PLAIN)
    result = download(server.url, tmp_path / "clip.mp4", decode_key=KEY, expected_size=len(CIPHER))
    assert result.path.read_bytes() == PLAIN
    assert not part.exists()


def test_a_part_file_longer_than_the_resource_is_reported_not_guessed(tmp_path, server):
    (tmp_path / "clip.mp4.part").write_bytes(PLAIN + b"extra")
    with pytest.raises(DownloadError, match="resume"):
        download(server.url, tmp_path / "clip.mp4", decode_key=KEY)


def test_a_body_that_stops_early_fails_and_keeps_the_part_file(tmp_path, server):
    server.handler.truncate_to = 40_000
    with pytest.raises(DownloadError, match="ended at"):
        download(server.url, tmp_path / "clip.mp4", decode_key=KEY)
    assert (tmp_path / "clip.mp4.part").stat().st_size == 40_000
    assert not (tmp_path / "clip.mp4").exists()


def test_an_existing_file_is_not_clobbered_unless_asked(tmp_path, server):
    dest = tmp_path / "clip.mp4"
    dest.write_bytes(b"mine")
    with pytest.raises(DownloadError, match="already exists"):
        download(server.url, dest, decode_key=KEY)
    result = download(server.url, dest, decode_key=KEY, overwrite=True)
    assert result.path.read_bytes() == PLAIN


def test_a_size_that_disagrees_with_the_capture_is_flagged_not_hidden(tmp_path, server):
    result = download(server.url, tmp_path / "clip.mp4", decode_key=KEY, expected_size=999)
    assert result.size_matches is False
    assert "file_size said 999" in result.summary()


def test_progress_reaches_the_end(tmp_path, server):
    seen: list[Progress] = []
    download(
        server.url,
        tmp_path / "clip.mp4",
        decode_key=KEY,
        on_progress=seen.append,
        progress_interval=0,
    )
    assert seen and seen[-1].downloaded == len(CIPHER)
    assert seen[-1].fraction == 1.0
    assert "100.0%" in seen[-1].line()


class FakeResponse:
    """Just enough of an http.client.HTTPResponse to be read and closed."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None) -> None:
        self.status = status
        self.headers = headers if headers is not None else {"content-length": str(len(body))}
        self._body = body

    def read(self, size: int) -> bytes:
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_desktop_user_agent_is_the_one_sent(tmp_path):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["ua"] = request.get_header("User-agent")
        sent["range"] = request.get_header("Range")
        return FakeResponse(CIPHER)

    result = download("http://cdn.invalid/x", tmp_path / "clip.mp4", urlopen=fake_urlopen)
    assert result.size == len(CIPHER)
    assert "Chrome" in sent["ua"]
    assert sent["range"] is None


def test_a_resume_asks_for_the_range_it_needs(tmp_path):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["range"] = request.get_header("Range")
        return FakeResponse(CIPHER[5000:], status=206)

    (tmp_path / "clip.mp4.part").write_bytes(PLAIN[:5000])
    download("http://cdn.invalid/x", tmp_path / "clip.mp4", decode_key=KEY, urlopen=fake_urlopen)
    assert sent["range"] == "bytes=5000-"
    assert (tmp_path / "clip.mp4").read_bytes() == PLAIN


def test_a_response_without_a_content_length_is_still_written(tmp_path):
    """No length means no completeness check to make - the file still has to land."""

    def fake_urlopen(request, timeout=None):
        return FakeResponse(CIPHER, headers={})

    result = download("http://cdn.invalid/x", tmp_path / "clip.mp4", urlopen=fake_urlopen)
    assert result.path.read_bytes() == CIPHER


def test_an_http_error_is_reported_with_its_status(tmp_path):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    with pytest.raises(DownloadError, match="HTTP 403"):
        download("http://example.invalid/x", tmp_path / "clip.mp4", urlopen=fake_urlopen)


def test_an_unreachable_host_is_reported_rather_than_raised_raw(tmp_path):
    def fake_urlopen(request, timeout=None):
        raise OSError("no route to host")

    with pytest.raises(DownloadError, match="could not reach"):
        download("http://example.invalid/x", tmp_path / "clip.mp4", urlopen=fake_urlopen)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1024", 1024), ("600m", 629145600), ("2G", 2147483648), ("8 KiB", 8192), ("1.5m", 1572864)],
)
def test_parse_size_understands_the_usual_suffixes(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "lots", "12x", "-5m"])
def test_parse_size_rejects_nonsense(text):
    with pytest.raises(ValueError):
        parse_size(text)


def test_human_size_reads_like_a_size():
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KiB"
    assert human_size(613097472) == "584.7 MiB"


def test_filename_prefers_the_description_and_always_carries_the_digest():
    url = "https://finder.video.qq.com/251/stodownload?encfilekey=abc"
    assert suggest_filename(url, description="猫 in a box!").startswith("猫-in-a-box-")
    assert suggest_filename(url, nickname="someone/else").startswith("someone-else-")
    assert suggest_filename(url).startswith("video-")
    assert all(suggest_filename(url, description=d).endswith(".mp4") for d in ("", "x"))
    # two clips that share a description still get two files
    assert suggest_filename(url, description="same") != suggest_filename(
        url + "2", description="same"
    )


def test_the_digest_name_is_stable_for_a_clip_but_not_shared_between_clips():
    """Names come from the bare `url`: `url_token` is reissued each capture, so hashing
    the full download URL would give one clip a new name every time round the feed."""
    one = "https://finder.video.qq.com/251/a"
    assert suggest_filename(one) == suggest_filename(one)
    assert suggest_filename(one) != suggest_filename("https://finder.video.qq.com/251/b")
