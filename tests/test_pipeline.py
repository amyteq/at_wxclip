"""Choosing what to download out of a capture, and doing it off the proxy's event loop."""

import json
from pathlib import Path

import pytest

from wxclip.capture import Media, parse_event
from wxclip.downloader import DownloadResult
from wxclip.pipeline import (
    CaptureRunner,
    media_from_log,
    newest_capture_log,
    read_capture_log,
)

KEY = "1234567890"


def media_payload(n: int, *, key: str = KEY, size: int = 4_000_000) -> dict:
    return {
        "url": f"https://finder.video.qq.com/251/clip{n}",
        "url_token": f"?token={n}",
        "decode_key": key,
        "decode_key_json_type": "string",
        "file_size": size,
    }


def capture_event(count: int = 8, **kw):
    """A capture the way Linux sends it: the whole visible feed, not one clip."""
    return parse_event(
        json.dumps(
            {
                "kind": "capture",
                "api": "finderH5ExtTransfer",
                "err_msg": "H5ExtTransfer:ok",
                "media": [media_payload(n, **kw) for n in range(count)],
            }
        )
    )


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
        Path(dest).write_bytes(b"\x00\x00\x00\x20ftypisom")
        return DownloadResult(
            Path(dest), 8, 0, 8, b"\x00\x00\x00\x20ftypisom", 0.1, kw.get("expected_size")
        )


def run_once(tmp_path, event, **kw) -> tuple[CaptureRunner, Recorder]:
    recorder = Recorder(fail=kw.pop("fail", False))
    runner = CaptureRunner(tmp_path / "dl", echo=lambda line: None, download=recorder, **kw)
    runner.on_event(event)
    runner.close()
    return runner, recorder


def test_takes_the_first_media_entry_by_default(tmp_path):
    runner, recorder = run_once(tmp_path, capture_event())
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["url"].endswith("clip0?token=0")
    assert recorder.calls[0]["decode_key"] == KEY
    assert recorder.calls[0]["expected_size"] == 4_000_000
    assert len(runner.results) == 1


def test_index_picks_a_different_entry_of_the_same_capture(tmp_path):
    _, recorder = run_once(tmp_path, capture_event(), index=5)
    assert recorder.calls[0]["url"].endswith("clip5?token=5")


def test_an_index_past_the_end_downloads_nothing_and_says_so(tmp_path):
    said = []
    runner = CaptureRunner(tmp_path, index=99, echo=said.append, download=Recorder())
    runner.on_event(capture_event(count=3))
    runner.close()
    assert runner.results == []
    assert any("--index 99" in line for line in said)


def test_all_takes_every_entry(tmp_path):
    _, recorder = run_once(tmp_path, capture_event(count=8), all_media=True, stop_after=0)
    assert len(recorder.calls) == 8


def test_the_same_clip_is_not_downloaded_twice_across_captures(tmp_path):
    """The feed re-reports the same media as the operator scrolls, with a fresh
    `url_token` each time; deduplication has to be on the bare url."""
    recorder = Recorder()
    runner = CaptureRunner(tmp_path, echo=lambda line: None, download=recorder, stop_after=0)
    runner.on_event(capture_event())
    runner.on_event(capture_event())
    runner.close()
    assert len(recorder.calls) == 1


def test_a_clip_over_max_size_is_skipped(tmp_path):
    """file_size ran to 585 MB in one capture; --max-size is what keeps that off a laptop."""
    _, recorder = run_once(tmp_path, capture_event(size=600_000_000), max_size=200_000_000)
    assert recorder.calls == []


def test_a_key_that_lost_precision_is_refused_before_anything_downloads(tmp_path):
    said = []
    runner = CaptureRunner(tmp_path, echo=said.append, download=Recorder())
    runner.on_event(capture_event(key="1.23e+19"))
    runner.close()
    assert runner.results == []
    assert any("decode_key" in line for line in said)


def test_stop_after_fires_the_finished_hook_once_it_has_enough(tmp_path):
    stopped = []
    recorder = Recorder()
    runner = CaptureRunner(
        tmp_path,
        all_media=True,
        stop_after=2,
        echo=lambda line: None,
        download=recorder,
        on_finished=lambda: stopped.append(True),
    )
    runner.on_event(capture_event(count=8))
    runner.close()
    assert runner.wait(0)
    assert stopped


def test_stop_after_zero_keeps_going(tmp_path):
    runner, recorder = run_once(tmp_path, capture_event(), all_media=True, stop_after=0)
    assert len(recorder.calls) == 8
    assert not runner.wait(0)


def test_a_failed_download_is_recorded_and_does_not_stop_the_proxy(tmp_path):
    runner, _ = run_once(tmp_path, capture_event(), fail=True)
    assert runner.results == []
    assert len(runner.failures) == 1
    assert "the CDN said no" in runner.summary()


def test_a_file_already_on_disk_is_not_fetched_again(tmp_path):
    runner = CaptureRunner(tmp_path / "dl", echo=lambda x: None)
    media = Media.from_payload(media_payload(0))
    dest = runner.destination(media)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"already here")
    recorder = Recorder()
    runner.download = recorder
    runner.on_event(capture_event(count=1))
    runner.close()
    assert recorder.calls == []


def test_events_without_usable_media_are_ignored(tmp_path):
    runner, recorder = run_once(tmp_path, parse_event('{"kind": "probe", "has_bridge": true}'))
    assert recorder.calls == []


def test_a_clip_gets_the_same_name_every_time_it_is_captured(tmp_path):
    """Names come from the clip, not the clock - otherwise the "already downloaded" skip
    above can never fire and a second capture re-fetches the whole file."""
    runner = CaptureRunner(tmp_path, echo=lambda x: None)
    first = runner.destination(Media.from_payload(media_payload(0))).name
    again = runner.destination(Media.from_payload(media_payload(0))).name
    other = runner.destination(Media.from_payload(media_payload(1))).name
    assert first == again != other
    assert first.endswith(".mp4")


def test_newest_capture_log_wins(tmp_path):
    older = tmp_path / "captures-20260101-000000.jsonl"
    newer = tmp_path / "captures-20260911-120000.jsonl"
    older.write_text("{}\n", encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")
    import os

    os.utime(older, (1, 1))
    assert newest_capture_log(tmp_path) == newer
    assert newest_capture_log(tmp_path / "empty") is None


def test_a_half_written_log_line_is_skipped_not_fatal(tmp_path):
    log = tmp_path / "captures-1.jsonl"
    log.write_text(
        json.dumps({"media": [media_payload(0)]}) + "\n{ truncated by a crash\n\n",
        encoding="utf-8",
    )
    assert len(read_capture_log(log)) == 1


def test_media_from_log_keeps_only_entries_that_can_be_downloaded(tmp_path):
    records = [
        {"media": [media_payload(0), media_payload(1, key="1.2e+19")]},
        {"media": [{"url": "", "decode_key": KEY}, "not a dict"]},
        {"kind": "probe"},
    ]
    entries = media_from_log(records)
    assert [e.url for e in entries] == ["https://finder.video.qq.com/251/clip0"]
    assert len(media_from_log(records, only_valid=False)) == 3


@pytest.mark.parametrize("count", [1, 8])
def test_the_download_url_is_url_plus_token(tmp_path, count):
    _, recorder = run_once(tmp_path, capture_event(count=count))
    assert recorder.calls[0]["url"] == "https://finder.video.qq.com/251/clip0?token=0"
