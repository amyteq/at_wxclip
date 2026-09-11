"""Talking to ffmpeg and ffprobe.

The post-processing half of wxclip (screenshots, text extraction, summary) leans on
external binaries rather than Python packages, so the runtime dependency list stays empty
and the heavy lifting is done by tools that are already tuned for it. What this module
owns is the unglamorous part: finding those binaries, telling the operator which package
to install when they are missing, and turning `ffprobe` output into something typed.

Every function that shells out takes a `run` callable, defaulting to `subprocess.run`.
That is the seam the tests use - nothing under `tests/` needs ffmpeg installed, exactly
as nothing there needs mitmproxy or a network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

#: How to get the binaries, by name. Printed verbatim when one is missing, so it has to
#: name the package rather than just the command.
INSTALL_HINTS = {
    FFMPEG: "ffmpeg (Arch: pacman -S ffmpeg; Debian/Ubuntu: apt install ffmpeg)",
    FFPROBE: "ffprobe, part of ffmpeg (Arch: pacman -S ffmpeg; Debian/Ubuntu: apt install ffmpeg)",
}

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class MediaError(RuntimeError):
    """A required binary is missing, or it ran and was unhappy."""


def run_default(argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """`subprocess.run` with text output. The one place the module actually forks."""
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, **kwargs)


def find_tool(name: str) -> str:
    """Absolute path of `name`, or a MediaError naming the package that provides it."""
    found = shutil.which(name)
    if found is None:
        raise MediaError(f"{name} not found on PATH - install {INSTALL_HINTS.get(name, name)}")
    return found


def have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def run_tool(
    name: str,
    args: Sequence[str],
    *,
    run: Runner = run_default,
    timeout: float | None = None,
) -> str:
    """Run `name` with `args` and hand back stdout, or raise MediaError with stderr.

    ffmpeg writes its banner and progress to stderr and is chatty even on success, so a
    failure is judged by the exit status and only then is stderr quoted - and only its
    tail, because the interesting line is always the last one.
    """
    argv = [find_tool(name), *args]
    try:
        completed = run(argv, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{name} did not finish within {timeout}s") from exc
    except OSError as exc:
        raise MediaError(f"could not run {name}: {exc}") from exc
    if completed.returncode != 0:
        tail = "\n".join((completed.stderr or "").strip().splitlines()[-5:])
        raise MediaError(f"{name} exited {completed.returncode}\n{tail}")
    return completed.stdout or ""


@dataclass(frozen=True)
class MediaInfo:
    """What ffprobe says about a file. Enough to plan a screenshot pass."""

    path: Path
    duration: float = 0.0
    width: int = 0
    height: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    size: int = 0
    #: Subtitle stream indexes. Empty for every Channels download seen so far - the
    #: captions are burned into the picture, which is why extraction goes through OCR.
    subtitle_streams: tuple[int, ...] = ()

    @property
    def has_video(self) -> bool:
        return bool(self.video_codec)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)

    @property
    def is_portrait(self) -> bool:
        """Channels clips are shot for a phone held upright: 1080x2344 on the M0 sample."""
        return self.height > self.width

    def summary(self) -> str:
        shape = f"{self.width}x{self.height}" if self.has_video else "no video"
        streams = self.video_codec or "-"
        if self.has_audio:
            streams += f" + {self.audio_codec}"
        if self.subtitle_streams:
            streams += f" + {len(self.subtitle_streams)} subtitle stream(s)"
        return f"{self.path}: {shape}, {self.duration:.2f}s, {streams}"


def probe(path: Path, *, run: Runner = run_default, timeout: float | None = 60.0) -> MediaInfo:
    """Read `path`'s shape with ffprobe.

    A still-encrypted download is the common failure here: its first 128 KiB are noise,
    so ffprobe finds no streams at all. That is reported as such instead of coming back
    as a MediaInfo full of zeroes, because the fix - decrypt it first - is specific.
    """
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"{path} is not a file")
    stdout = run_tool(
        FFPROBE,
        [
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        run=run,
        timeout=timeout,
    )
    try:
        parsed = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"could not parse ffprobe output for {path}: {exc}") from exc

    container = parsed.get("format") or {}
    streams = [s for s in (parsed.get("streams") or []) if isinstance(s, dict)]
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    subtitles = tuple(
        int(s["index"])
        for s in streams
        if s.get("codec_type") == "subtitle" and isinstance(s.get("index"), int)
    )

    def number(*candidates: object) -> float:
        for candidate in candidates:
            try:
                return float(candidate)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return 0.0

    info = MediaInfo(
        path=path,
        duration=number(container.get("duration"), video.get("duration"), audio.get("duration")),
        width=int(number(video.get("width"))),
        height=int(number(video.get("height"))),
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=str(audio.get("codec_name") or ""),
        size=int(number(container.get("size"))) or path.stat().st_size,
        subtitle_streams=subtitles,
    )
    if not streams:
        raise MediaError(
            f"{path} has no decodable streams - if this came straight off the CDN it is "
            f"still encrypted; run `wxclip decrypt` on it first"
        )
    return info
