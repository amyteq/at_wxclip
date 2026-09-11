"""Parsing what the hook script posts back, with the decode_key precision guard."""

import json

import pytest

from wxclip.capture import (
    KIND_CAPTURE,
    CaptureError,
    CaptureLog,
    Media,
    format_event,
    is_valid_decode_key,
    parse_event,
)

CAPTURE_PAYLOAD = {
    "kind": "capture",
    "api": "getVideoInfo",
    "err_msg": "finderH5ExtTransfer:ok",
    "page_url": "https://channels.weixin.qq.com/web/pages/feed",
    "user_agent": "MicroMessenger/7.0.20",
    "media": [
        {
            "url": "https://finder.video.qq.com/251/abc?token=1",
            "url_token": "&extra=2",
            "decode_key": "13835058055282163712",
            "decode_key_json_type": "number",
            "file_size": "4194304",
            "description": "a clip",
            "nickname": "someone",
        }
    ],
}


def test_parses_a_capture():
    event = parse_event(json.dumps(CAPTURE_PAYLOAD))
    assert event.kind == KIND_CAPTURE
    assert event.err_msg == "finderH5ExtTransfer:ok"
    media = event.captures[0]
    assert media.decode_key == "13835058055282163712"
    assert media.decode_key_json_type == "number"
    assert media.file_size == 4194304
    assert media.download_url == "https://finder.video.qq.com/251/abc?token=1&extra=2"


def test_parses_a_probe_without_media():
    event = parse_event('{"kind": "probe", "has_bridge": true}')
    assert event.kind == "probe"
    assert event.media == ()
    assert event.payload["has_bridge"] is True


@pytest.mark.parametrize(
    "raw", [b"not json", b"[1, 2]", b'{"media": {"not": "a list"}}', b"\xff\xfe"]
)
def test_rejects_bodies_the_hook_script_could_not_have_sent(raw):
    with pytest.raises(CaptureError):
        parse_event(raw)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("0", True),
        ("13835058055282163712", True),
        ("18446744073709551615", True),  # uint64 ceiling
        ("184467440737095516150", False),  # 21 digits, not a uint64
        ("1.3835058055282164e+19", False),  # went through a float
        ("13835058055282164000", True),  # lost precision but still a digit string
        (13835058055282163712, False),  # not a string at all
        ("", False),
        ("0x1f", False),
    ],
)
def test_decode_key_validation(value, ok):
    assert is_valid_decode_key(value) is ok


def test_media_flags_a_key_that_has_been_through_a_float():
    assert not Media(decode_key="1.3835058055282164e+19").decode_key_ok
    assert Media(decode_key="13835058055282163712").decode_key_ok


def test_file_size_falls_back_to_none_when_it_is_not_a_number():
    assert Media.from_payload({"file_size": "unknown"}).file_size is None
    assert Media.from_payload({}).file_size is None


def test_format_event_shows_the_key_and_flags_a_broken_one():
    good = format_event(parse_event(json.dumps(CAPTURE_PAYLOAD)))
    assert "13835058055282163712" in good
    assert "!!" not in good

    broken = dict(CAPTURE_PAYLOAD)
    broken["media"] = [dict(CAPTURE_PAYLOAD["media"][0], decode_key="1.38e+19")]
    assert "!!" in format_event(parse_event(json.dumps(broken)))


def test_capture_log_appends_one_json_object_per_line(tmp_path):
    log = CaptureLog(tmp_path / "captures")
    event = parse_event(json.dumps(CAPTURE_PAYLOAD))
    log.append_event(event)
    log.append_event(event)
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["media"][0]["decode_key"] == "13835058055282163712"
    assert record["media"][0]["download_url"].endswith("&extra=2")
    assert record["kind"] == "capture"
