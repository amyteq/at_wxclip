"""Shared fakes for the post-processing tests.

The point of these is that `make test` needs neither ffmpeg nor tesseract nor a network,
the same way it already needs no WeChat client. Every module that shells out takes a `run`
callable, so a fake that records its calls and answers with canned output covers the real
logic - which is the parsing, the selection and the de-duplication, not the subprocess
plumbing.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

#: Real `ffmpeg -vf select=...,metadata=print` output from the M0 sample, kept verbatim.
#: Any change to the parser gets checked against what the tool actually prints.
SCENE_OUTPUT = """\
frame:0    pts:0       pts_time:0
lavfi.scene_score=0.000000
frame:1    pts:18432   pts_time:1.2
lavfi.scene_score=0.387536
frame:2    pts:54784   pts_time:3.566667
lavfi.scene_score=0.389802
frame:3    pts:74752   pts_time:4.866667
lavfi.scene_score=0.278497
frame:4    pts:94720   pts_time:6.166667
lavfi.scene_score=0.230930
"""

#: `ffprobe -print_format json -show_format -show_streams` on the same file, trimmed to
#: the fields wx-dlh reads.
PROBE_OUTPUT = """\
{
  "streams": [
    {"index": 0, "codec_type": "video", "codec_name": "h264",
     "width": 1080, "height": 2344, "duration": "7.466667"},
    {"index": 1, "codec_type": "audio", "codec_name": "aac",
     "duration": "7.476009", "channels": 2}
  ],
  "format": {"duration": "7.476009", "size": "3873528", "nb_streams": 2}
}
"""


class FakeRunner:
    """Stands in for `subprocess.run`, recording argv and answering from a rule list.

    Rules are `(needle, handler)`: the first needle found anywhere in the joined argv
    wins, and the handler is either a string (used as stdout) or a callable taking the
    argv and returning stdout - which is how a fake ffmpeg "writes" a still.
    """

    def __init__(
        self,
        rules: Sequence[tuple[str, str | Callable[[list[str]], str]]] = (),
        *,
        default: str = "",
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.rules = list(rules)
        self.default = default
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout=None, **kwargs) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        stdout = self.default
        for needle, handler in self.rules:
            if needle in joined:
                stdout = handler(argv) if callable(handler) else handler
                break
        return subprocess.CompletedProcess(argv, self.returncode, stdout, self.stderr)

    def calls_matching(self, needle: str) -> list[list[str]]:
        return [argv for argv in self.calls if needle in " ".join(argv)]


def write_still(argv: list[str]) -> str:
    """A fake ffmpeg still-grab: create the file it was told to write."""
    dest = Path(argv[-1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"\xff\xd8\xff\xe0jpeg-ish")
    return ""


@pytest.fixture
def video(tmp_path: Path) -> Path:
    """A file that exists. Its contents never matter - ffprobe is always faked."""
    path = tmp_path / "video-1f3c9a2b41.mp4"
    path.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64)
    return path


@pytest.fixture
def ffmpeg(monkeypatch) -> FakeRunner:
    """A runner that answers ffprobe, scene detection and still grabs.

    `shutil.which` is patched too, so the modules' "is it installed" checks pass on a
    machine that has neither binary.
    """
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    return FakeRunner(
        [
            ("-show_streams", PROBE_OUTPUT),
            ("metadata=print", SCENE_OUTPUT),
            ("-frames:v", write_still),
        ]
    )
