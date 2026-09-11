"""Keyframe screenshots: pick the moments worth a still, then write them out.

Sampling every N seconds is the obvious thing and it is usually the wrong thing - a
Channels clip holds one card on screen for seconds at a time, so a fixed interval gives
you the same picture six times and misses the cut in between. So the default is ffmpeg's
own scene-change score: one detection pass over the video reports every frame that
differs enough from the one before it, and only those timestamps are turned into files.

That pass costs one full decode, which is why the frames are *not* written by it. It
prints timestamps; a second, seek-based pass grabs only the stills that survive the cull.
Seeking with `-ss` before `-i` never decodes the rest of the file, so a 585 MB video costs
one decode plus a handful of seeks rather than two decodes.

Detection can legitimately come back near-empty - a static talking head, a single card
held for a minute - so a clip that yields too few scenes falls back to evenly spaced
stills instead of handing back one screenshot and calling it a set.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from wxclip.media import FFMPEG, MediaError, MediaInfo, Runner, probe, run_default, run_tool

#: Scene score above which a frame counts as a new shot. ffmpeg's score is 0..1; 0.2
#: catches a cut and a card change without firing on camera shake. The M0 sample
#: (7.5 s, one inner video on a text card) yields four scenes at this setting.
DEFAULT_SCENE_THRESHOLD = 0.2

#: Nobody wants 200 stills out of a long clip, and nobody can work with one.
DEFAULT_MAX_SHOTS = 12
DEFAULT_MIN_SHOTS = 3

#: Seconds between stills when there is nothing to detect and we fall back to spacing.
DEFAULT_EVERY = 5.0

#: Width cap. Channels clips are 1080 wide, so this is a no-op for them and a guard for
#: anything larger; OCR wants the pixels, so it is a cap rather than a target.
DEFAULT_WIDTH = 1080

#: `-q:v` for mjpeg: 2 is visually lossless, 3 is a third of the bytes. OCR sees no
#: difference between them on rendered text.
DEFAULT_QUALITY = 3

MANIFEST_NAME = "shots.json"

STRATEGY_SCENE = "scene"
STRATEGY_EVEN = "even"
STRATEGY_INTERVAL = "interval"

#: `frame:1    pts:18432   pts_time:1.2` followed by `lavfi.scene_score=0.387536`.
_FRAME_RE = re.compile(r"^frame:(\d+)\s+pts:(-?\d+)\s+pts_time:(-?[\d.]+)")
_SCORE_RE = re.compile(r"^lavfi\.scene_score=([\d.]+)")


@dataclass(frozen=True)
class Shot:
    """One still, and why it was taken."""

    index: int
    time: float
    path: Path
    #: The scene score that selected it; None for a frame taken because of where it sits
    #: (the opening frame, or an evenly spaced sample) rather than what changed in it.
    score: float | None = None

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class ShotSet:
    """The stills taken from one video, and how they were chosen."""

    video: Path
    directory: Path
    strategy: str
    shots: tuple[Shot, ...]
    info: MediaInfo | None = None

    def __len__(self) -> int:
        return len(self.shots)

    def manifest(self) -> dict:
        """What `shots.json` holds: enough for `extract` and `summarize` to pick up the
        set without re-probing, and for a human to see what was chosen and when."""
        return {
            "video": str(self.video),
            "duration": round(self.info.duration, 3) if self.info else None,
            "width": self.info.width if self.info else None,
            "height": self.info.height if self.info else None,
            "strategy": self.strategy,
            "shots": [
                {
                    "index": shot.index,
                    "time": round(shot.time, 3),
                    "score": round(shot.score, 4) if shot.score is not None else None,
                    "file": shot.name,
                }
                for shot in self.shots
            ],
        }

    def summary(self) -> str:
        how = {
            STRATEGY_SCENE: "scene changes",
            STRATEGY_EVEN: "evenly spaced (too few scene changes to go on)",
            STRATEGY_INTERVAL: "fixed interval",
        }.get(self.strategy, self.strategy)
        lines = [f"{len(self.shots)} screenshot(s) in {self.directory}, chosen by {how}"]
        lines += [
            f"  [{shot.index:03d}] t={shot.time:7.2f}s"
            + (f" score={shot.score:.3f}" if shot.score is not None else "")
            + f"  {shot.name}"
            for shot in self.shots
        ]
        return "\n".join(lines)


def parse_scene_output(text: str) -> list[tuple[float, float]]:
    """`(time, score)` pairs out of `metadata=print`'s stream.

    The two lines always arrive together, frame header then score, so the header is held
    until its score shows up; a trailing header with no score is dropped rather than
    guessed at.
    """
    pairs: list[tuple[float, float]] = []
    pending: float | None = None
    for line in text.splitlines():
        line = line.strip()
        frame = _FRAME_RE.match(line)
        if frame:
            pending = float(frame.group(3))
            continue
        score = _SCORE_RE.match(line)
        if score and pending is not None:
            pairs.append((pending, float(score.group(1))))
            pending = None
    return pairs


def detect_scenes(
    video: Path,
    *,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
    run: Runner = run_default,
    timeout: float | None = None,
) -> list[tuple[float, float]]:
    """Every frame whose scene score clears `threshold`, plus the opening frame.

    `eq(n,0)` is in the selector because the scene filter cannot fire on the first frame -
    it has nothing to compare against - and the opening frame of a clip is exactly the
    still an operator expects to see in the set. It arrives with a score of 0.
    """
    selector = rf"select='eq(n\,0)+gt(scene\,{threshold})',metadata=print:file=-"
    stdout = run_tool(
        FFMPEG,
        [
            "-hide_banner",
            "-nostats",
            "-nostdin",
            "-i",
            str(video),
            "-vf",
            selector,
            "-an",
            "-f",
            "null",
            "-",
        ],
        run=run,
        timeout=timeout,
    )
    return parse_scene_output(stdout)


def even_times(duration: float, count: int) -> list[float]:
    """`count` timestamps spread over `duration`, at the middle of each slice.

    Midpoints, not edges: a seek to the final timestamp of a file lands past the last
    frame and yields nothing, and the first frame of a clip is often a fade from black.
    """
    if count <= 0:
        return []
    if duration <= 0:
        return [0.0]
    return [duration * (i + 0.5) / count for i in range(count)]


def interval_times(duration: float, every: float) -> list[float]:
    """`0, every, 2*every, ...`, staying inside the file."""
    if every <= 0:
        raise ValueError(f"interval must be positive, got {every}")
    if duration <= 0:
        return [0.0]
    count = max(1, math.ceil(duration / every))
    return [t for t in (i * every for i in range(count)) if t < duration]


def cull(scenes: Sequence[tuple[float, float]], max_shots: int) -> list[tuple[float, float | None]]:
    """Trim a scene list to `max_shots`, keeping the biggest changes and the opening frame.

    The opening frame scores 0, so a plain "keep the highest scores" would throw away the
    one still that is always worth having. It is set aside first - and its score becomes
    None, because 0 would read as "nothing changed here" rather than "this is the start".
    """
    if max_shots <= 0:
        return []
    ordered = sorted(scenes, key=lambda pair: pair[0])
    opening: list[tuple[float, float | None]] = []
    rest = list(ordered)
    if rest and rest[0][0] <= 0.0:
        opening = [(rest[0][0], None)]
        rest = rest[1:]
    room = max_shots - len(opening)
    if len(rest) > room:
        rest = sorted(sorted(rest, key=lambda pair: pair[1], reverse=True)[:room])
    return opening + [(time, score) for time, score in rest]


def plan(
    info: MediaInfo,
    *,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
    max_shots: int = DEFAULT_MAX_SHOTS,
    min_shots: int = DEFAULT_MIN_SHOTS,
    every: float | None = None,
    run: Runner = run_default,
    timeout: float | None = None,
) -> tuple[str, list[tuple[float, float | None]]]:
    """Which timestamps to grab, and which strategy produced them."""
    if every is not None:
        return STRATEGY_INTERVAL, [(t, None) for t in interval_times(info.duration, every)]

    scenes = detect_scenes(info.path, threshold=threshold, run=run, timeout=timeout)
    if len(scenes) >= min_shots:
        return STRATEGY_SCENE, cull(scenes, max_shots)

    # One card held for a minute scores nothing anywhere; spacing is all that is left.
    wanted = min(max(min_shots, math.ceil((info.duration or 0) / DEFAULT_EVERY)), max_shots)
    return STRATEGY_EVEN, [(t, None) for t in even_times(info.duration, wanted)]


def shot_name(index: int, time: float, suffix: str = ".jpg") -> str:
    """`shot-003-t12.40s.jpg` - the index sorts the set, the timestamp says where it is."""
    return f"shot-{index:03d}-t{time:.2f}s{suffix}"


def grab(
    video: Path,
    time: float,
    dest: Path,
    *,
    width: int | None = DEFAULT_WIDTH,
    quality: int = DEFAULT_QUALITY,
    run: Runner = run_default,
    timeout: float | None = 120.0,
) -> Path:
    """Write a single still from `time` into `dest`.

    `-ss` goes before `-i` on purpose: input seeking jumps to the nearest keyframe without
    decoding what precedes it. The still can therefore land a fraction of a second off the
    requested timestamp, which for a screenshot is a trade worth making every time.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-hide_banner",
        "-nostats",
        "-nostdin",
        "-y",
        "-ss",
        f"{max(0.0, time):.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        str(quality),
    ]
    if width:
        # scale down only, and to an even height - mjpeg rejects odd dimensions.
        args += ["-vf", f"scale='min({width},iw)':-2"]
    args.append(str(dest))
    run_tool(FFMPEG, args, run=run, timeout=timeout)
    if not dest.is_file():
        raise MediaError(f"ffmpeg wrote no still for t={time:.3f}s of {video}")
    return dest


def take_shots(
    video: Path,
    out_dir: Path,
    *,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
    max_shots: int = DEFAULT_MAX_SHOTS,
    min_shots: int = DEFAULT_MIN_SHOTS,
    every: float | None = None,
    width: int | None = DEFAULT_WIDTH,
    quality: int = DEFAULT_QUALITY,
    info: MediaInfo | None = None,
    run: Runner = run_default,
    timeout: float | None = None,
) -> ShotSet:
    """Probe `video`, choose the moments, write the stills into `out_dir`."""
    video = Path(video)
    out_dir = Path(out_dir)
    info = info or probe(video, run=run)
    strategy, times = plan(
        info,
        threshold=threshold,
        max_shots=max_shots,
        min_shots=min_shots,
        every=every,
        run=run,
        timeout=timeout,
    )
    shots = []
    for index, (time, score) in enumerate(times, start=1):
        dest = out_dir / shot_name(index, time)
        grab(video, time, dest, width=width, quality=quality, run=run)
        shots.append(Shot(index=index, time=time, path=dest, score=score))
    return ShotSet(video=video, directory=out_dir, strategy=strategy, shots=tuple(shots), info=info)


def write_manifest(shots: ShotSet, path: Path | None = None) -> Path:
    """Save the manifest next to the stills so the later steps can find them."""
    path = path or shots.directory / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(shots.manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def read_manifest(path: Path) -> ShotSet:
    """Load a `shots.json` back, so `extract` can run on a set taken earlier."""
    path = Path(path)
    directory = path.parent
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MediaError(f"could not read {path}: {exc}") from exc
    shots = tuple(
        Shot(
            index=int(entry.get("index") or 0),
            time=float(entry.get("time") or 0.0),
            path=directory / str(entry.get("file") or ""),
            score=None if entry.get("score") is None else float(entry["score"]),
        )
        for entry in data.get("shots") or []
    )
    return ShotSet(
        video=Path(str(data.get("video") or "")),
        directory=directory,
        strategy=str(data.get("strategy") or ""),
        shots=shots,
    )
