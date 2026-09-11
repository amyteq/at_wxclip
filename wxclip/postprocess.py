"""Screenshots, then text, then a summary - and the directory that holds all three.

`pipeline.py` does this job for the capture half (event -> chosen media -> file); this is
its counterpart for the post-processing half (file -> stills -> text -> summary). Keeping
it out of `__main__.py` means `wxclip shots`, `wxclip extract`, `wxclip summarize` and
`wxclip process` are four thin wrappers over one set of functions rather than four
slightly different implementations of the same sequence.

Everything a clip produces lands in one directory named after the clip:

    processed/video-1f3c9a2b41/
        shots.json                  what was chosen, and when
        shot-001-t0.00s.jpg  ...    the stills
        text.txt                    the transcript, de-duplicated
        text.json                   per-still detail, including the raw OCR
        summary.md                  the model's answer, plus how it was produced

The steps reuse what is already in there unless told to redo it. That is not just a speed
trick: iterating on the summarize prompt means running the third step twenty times over
the same stills and the same transcript, and re-OCRing five images each round to get the
same text is a waste of everyone's time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wxclip import extract as extract_module
from wxclip import frames as frames_module
from wxclip import summarize as summarize_module
from wxclip.extract import Extraction
from wxclip.frames import ShotSet
from wxclip.media import MediaInfo, Runner, probe, run_default
from wxclip.summarize import Summary


def echo_line(line: str) -> None:
    print(line, flush=True)


def bundle_dir(root: Path, video: Path) -> Path:
    """`processed/<clip name>/`.

    Named after the video file, which `downloader.suggest_filename` already made unique
    and stable per clip - so processing the same download twice finds its own earlier
    output instead of piling up directories.
    """
    return Path(root) / Path(video).stem


def find_video(directory: Path) -> Path | None:
    """The video a bundle directory was built from, according to its manifest."""
    manifest = Path(directory) / frames_module.MANIFEST_NAME
    if not manifest.is_file():
        return None
    recorded = frames_module.read_manifest(manifest).video
    return recorded if str(recorded) else None


@dataclass
class Bundle:
    """One clip's post-processing output, and which parts of it exist so far."""

    video: Path
    directory: Path
    info: MediaInfo | None = None
    shots: ShotSet | None = None
    extraction: Extraction | None = None
    model_summary: Summary | None = None

    @property
    def paths(self) -> list[Path]:
        found = []
        for name in (
            frames_module.MANIFEST_NAME,
            extract_module.TEXT_NAME,
            extract_module.DETAIL_NAME,
            summarize_module.SUMMARY_NAME,
        ):
            path = self.directory / name
            if path.is_file():
                found.append(path)
        return found

    def report(self) -> str:
        lines = [f"{self.directory}"]
        if self.info is not None:
            lines.append(f"  {self.info.summary()}")
        if self.shots is not None:
            lines.append(f"  {len(self.shots)} still(s), chosen by {self.shots.strategy}")
        if self.extraction is not None:
            lines.append(f"  {self.extraction.summary()}")
        if self.model_summary is not None:
            lines.append(f"  {self.model_summary.summary()}")
        lines += [f"  -> {path.name}" for path in self.paths]
        return "\n".join(lines)


def ensure_shots(
    video: Path,
    directory: Path,
    *,
    info: MediaInfo | None = None,
    redo: bool = False,
    echo: Callable[[str], None] = echo_line,
    run: Runner = run_default,
    **options,
) -> ShotSet:
    """The stills for `video`, taken now or read back from an earlier run."""
    manifest = Path(directory) / frames_module.MANIFEST_NAME
    if manifest.is_file() and not redo:
        existing = frames_module.read_manifest(manifest)
        if existing.shots and all(shot.path.is_file() for shot in existing.shots):
            echo(f"-- reusing {len(existing.shots)} still(s) from {manifest.name}")
            return existing
    shots = frames_module.take_shots(video, directory, info=info, run=run, **options)
    frames_module.write_manifest(shots)
    echo(shots.summary())
    return shots


def ensure_text(
    shots: ShotSet,
    directory: Path,
    *,
    redo: bool = False,
    echo: Callable[[str], None] = echo_line,
    run: Runner = run_default,
    **options,
) -> Extraction:
    """The transcript for a set of stills, read from `text.json` when it is already there."""
    detail = Path(directory) / extract_module.DETAIL_NAME
    if detail.is_file() and not redo:
        existing = read_extraction(detail)
        if existing is not None:
            echo(f"-- reusing {len(existing.lines)} line(s) from {detail.name}")
            return existing
    extraction = extract_module.extract_text(shots, run=run, **options)
    extract_module.write_text(extraction, directory)
    echo(extraction.summary())
    return extraction


def read_extraction(path: Path) -> Extraction | None:
    """Load a `text.json` back. None when it is missing or not readable as one."""
    import json

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "lines" not in data:
        return None
    return Extraction(
        video=Path(str(data.get("video") or "")),
        shots=tuple(
            extract_module.ShotText(
                index=int(entry.get("index") or 0),
                time=float(entry.get("time") or 0.0),
                file=str(entry.get("file") or ""),
                lines=tuple(str(line) for line in entry.get("lines") or []),
            )
            for entry in data.get("shots") or []
        ),
        lines=tuple(str(line) for line in data.get("lines") or []),
        source=str(data.get("source") or extract_module.SOURCE_OCR),
        lang=str(data.get("lang") or extract_module.DEFAULT_LANG),
    )


def process(
    video: Path,
    root: Path,
    *,
    info: MediaInfo | None = None,
    shot_options: dict | None = None,
    extract_options: dict | None = None,
    summarize_options: dict | None = None,
    with_text: bool = True,
    with_summary: bool = True,
    redo: bool = False,
    description: str = "",
    nickname: str = "",
    echo: Callable[[str], None] = echo_line,
    run: Runner = run_default,
) -> Bundle:
    """Take a playable file all the way to a summary, writing each step as it lands.

    Each artefact is written the moment it exists, so a run that dies in the model call
    still leaves the stills and the transcript behind - which are the expensive parts.
    """
    video = Path(video)
    directory = bundle_dir(root, video)
    directory.mkdir(parents=True, exist_ok=True)
    info = info or probe(video, run=run)
    echo(info.summary())
    if info.subtitle_streams:
        # Worth saying out loud: no Channels download has had one, and if one does, OCR
        # is doing by hand what a stream copy would do exactly.
        echo(
            f"!! this file has {len(info.subtitle_streams)} subtitle stream(s); OCR ignores "
            f"them (ffmpeg -map 0:s -c copy would read them directly)"
        )

    bundle = Bundle(video=video, directory=directory, info=info)
    bundle.shots = ensure_shots(
        video, directory, info=info, redo=redo, echo=echo, run=run, **(shot_options or {})
    )
    if not with_text:
        return bundle

    bundle.extraction = ensure_text(
        bundle.shots, directory, redo=redo, echo=echo, run=run, **(extract_options or {})
    )
    if not with_summary:
        return bundle

    bundle.model_summary = summarize_module.summarize(
        bundle.extraction.text,
        bundle.shots,
        info=info,
        description=description,
        nickname=nickname,
        run=run,
        **(summarize_options or {}),
    )
    summarize_module.write_summary(
        bundle.model_summary, directory, video=video, source=bundle.extraction.summary()
    )
    echo(bundle.model_summary.summary())
    return bundle
