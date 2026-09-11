"""Finding ffmpeg and ffprobe, and reading what ffprobe says."""

import subprocess

import pytest

from tests.conftest import PROBE_OUTPUT, FakeRunner
from wxclip.media import (
    FFMPEG,
    FFPROBE,
    MediaError,
    find_tool,
    have_tool,
    probe,
    run_tool,
)


def test_a_missing_binary_names_the_package_to_install(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(MediaError) as exc:
        find_tool(FFMPEG)
    assert "pacman -S ffmpeg" in str(exc.value)
    assert "apt install ffmpeg" in str(exc.value)
    assert not have_tool(FFMPEG)


def test_run_tool_hands_back_stdout(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffprobe")
    runner = FakeRunner(default="hello")
    assert run_tool(FFPROBE, ["-version"], run=runner) == "hello"
    assert runner.calls == [["/usr/bin/ffprobe", "-version"]]


def test_a_failed_run_quotes_the_tail_of_stderr(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
    # ffmpeg is chatty; only the last lines say what went wrong.
    noise = "\n".join(f"line {n}" for n in range(20))
    runner = FakeRunner(returncode=1, stderr=f"{noise}\nInvalid data found")
    with pytest.raises(MediaError) as exc:
        run_tool(FFMPEG, ["-i", "x"], run=runner)
    assert "exited 1" in str(exc.value)
    assert "Invalid data found" in str(exc.value)
    assert "line 0" not in str(exc.value)


def test_a_timeout_is_reported_as_one(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")

    def slow(argv, timeout=None, **kwargs):
        raise subprocess.TimeoutExpired(argv, timeout or 0)

    with pytest.raises(MediaError, match="did not finish within 5"):
        run_tool(FFMPEG, ["-i", "x"], run=slow, timeout=5)


def test_probe_reads_the_shape_of_the_file(video, ffmpeg):
    info = probe(video, run=ffmpeg)
    assert (info.width, info.height) == (1080, 2344)
    assert info.duration == pytest.approx(7.476009)
    assert (info.video_codec, info.audio_codec) == ("h264", "aac")
    assert info.has_video and info.has_audio
    assert info.is_portrait  # a Channels clip is shot for a phone held upright
    assert info.size == 3873528
    assert info.subtitle_streams == ()
    assert "1080x2344" in info.summary()


def test_a_subtitle_stream_is_recorded_when_there_is_one(video, ffmpeg):
    ffmpeg.rules = [
        (
            "-show_streams",
            PROBE_OUTPUT.replace(
                '{"index": 1, "codec_type": "audio"',
                '{"index": 2, "codec_type": "subtitle", "codec_name": "mov_text"},'
                ' {"index": 1, "codec_type": "audio"',
            ),
        )
    ]
    assert probe(video, run=ffmpeg).subtitle_streams == (2,)


def test_a_file_with_no_streams_is_probably_still_encrypted(video, ffmpeg):
    """The head of a fresh download is 128 KiB of noise, so ffprobe finds nothing.
    Saying "run decrypt first" beats handing back a MediaInfo full of zeroes."""
    ffmpeg.rules = [("-show_streams", '{"streams": [], "format": {}}')]
    with pytest.raises(MediaError, match="still encrypted"):
        probe(video, run=ffmpeg)


def test_probe_refuses_a_path_that_is_not_a_file(tmp_path, ffmpeg):
    with pytest.raises(MediaError, match="is not a file"):
        probe(tmp_path / "nope.mp4", run=ffmpeg)


def test_unparseable_ffprobe_output_is_an_error_not_a_crash(video, ffmpeg):
    ffmpeg.rules = [("-show_streams", "not json at all")]
    with pytest.raises(MediaError, match="could not parse ffprobe output"):
        probe(video, run=ffmpeg)
