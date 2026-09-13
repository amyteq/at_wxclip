"""Text extraction, by OCR over the stills.

Three roads were open here - the embedded subtitle track, OCR of the picture, and speech
transcription - and this module takes the second one. The reasoning, so it is not
relitigated as a bug later:

* **There is no subtitle track.** Every Channels download inspected so far carries one
  video and one audio stream and nothing else; the captions are burned into the picture.
* **The picture is where the content is.** The M0 sample is a news card: a headline
  across the top, a small inner video, and a full paragraph of body text below it. The
  clip *is* text. OCR does not approximate the content of a video like that, it reads it.
* **It costs nothing extra.** The stills already exist - `wxclip shots` took them - so
  extraction is one `tesseract` call per still, with no second decode of the video, no
  model download, and no Python dependency.

Speech transcription remains the obvious next road, for clips that talk instead of
writing; `Extraction.source` records which one produced a given transcript so a mixed
pipeline stays legible later.

The interesting work is not the OCR call, it is the cleanup. Consecutive stills of the
same card yield the same caption, so the same sentence arrives three times; and OCR run
over a photographic region invents characters that were never there. Both are handled
below, and both are what `tests/test_extract.py` guards.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from wxclip.frames import Shot, ShotSet
from wxclip.media import MediaError, Runner, find_tool, have_tool, run_default, run_tool

TESSERACT = "tesseract"

SOURCE_OCR = "ocr"

#: Simplified Chinese first, English second - Channels cards mix the two, and the M0
#: sample has Latin brand names inside Chinese sentences.
DEFAULT_LANG = "chi_sim+eng"

#: tesseract's own default page segmentation: find the text blocks automatically. These
#: cards have several (headline, body, watermark), so the automatic mode is right and
#: `--psm 6` - "one uniform block" - visibly merges them.
DEFAULT_PSM = 3

#: Above this, two lines are the same caption read twice. Chosen loose enough to absorb
#: one or two mis-read characters, tight enough to keep two different sentences apart.
DEFAULT_SIMILARITY = 0.85

#: How far back to look when deciding a line is a repeat; 0 means the whole transcript.
#:
#: A window was the first instinct and it was wrong. One still of a Channels card yields
#: about ten lines, so any window short enough to look cheap is shorter than a single
#: still's output, and the headline returns on every card: the M0 sample dropped 12 of 37
#: lines at a window of 12 and still repeated its own title three times. Comparing
#: against the whole transcript is quadratic, but on a few hundred short lines that costs
#: nothing, and a caption that genuinely recurs is still noise to a summary.
LOOKBACK = 0

TEXT_NAME = "text.txt"
DETAIL_NAME = "text.json"

MISSING_TESSERACT = (
    "tesseract not found on PATH - install it plus the language data:\n"
    "  Arch:          pacman -S tesseract tesseract-data-chi_sim tesseract-data-eng\n"
    "  Debian/Ubuntu: apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng"
)

_WHITESPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")
#: Characters that carry meaning: CJK, letters, digits. Everything else is punctuation,
#: box-drawing noise, or OCR debris.
_MEANINGFUL = re.compile(r"[\w㐀-䶿一-鿿぀-ヿ]", re.UNICODE)


def language_hint(lang: str) -> str:
    """What to install when tesseract has no data for `lang`.

    Named per-language because `chi_sim` is its own package everywhere, and the failure
    is otherwise a wall of "Please make sure TESSDATA_PREFIX is set" that suggests the
    wrong fix.
    """
    names = [part for part in lang.split("+") if part]
    arch = " ".join(f"tesseract-data-{name}" for name in names)
    debian = " ".join(f"tesseract-ocr-{name.replace('_', '-')}" for name in names)
    return (
        f"tesseract has no data for {lang!r}. Install it:\n"
        f"  Arch:          pacman -S {arch}\n"
        f"  Debian/Ubuntu: apt install {debian}\n"
        f"Or drop the .traineddata files in a directory of your own and point\n"
        f"--tessdata-dir (or TESSDATA_PREFIX) at it - no root needed:\n"
        f"  https://github.com/tesseract-ocr/tessdata_fast"
    )


def ocr_image(
    image: Path,
    *,
    lang: str = DEFAULT_LANG,
    psm: int = DEFAULT_PSM,
    tessdata_dir: Path | None = None,
    run: Runner = run_default,
    timeout: float | None = 120.0,
) -> str:
    """Everything tesseract reads in `image`, as it comes off stdout."""
    args = [str(image), "stdout", "-l", lang, "--psm", str(psm)]
    if tessdata_dir:
        args += ["--tessdata-dir", str(tessdata_dir)]
    try:
        return run_tool(TESSERACT, args, run=run, timeout=timeout)
    except MediaError as exc:
        if "Failed loading language" in str(exc) or "Error opening data file" in str(exc):
            raise MediaError(language_hint(lang)) from exc
        raise


def clean_lines(raw: str) -> list[str]:
    """OCR output to lines worth keeping.

    Two filters, both earning their place on real output: a line needs at least two
    meaningful characters (single stray glyphs are what OCR produces from a photograph),
    and at least half of it has to be meaningful (a line that is mostly punctuation is
    debris from a border or a watermark, not a sentence).
    """
    kept = []
    for line in raw.splitlines():
        line = _WHITESPACE.sub(" ", line).strip()
        if not line:
            continue
        meaningful = len(_MEANINGFUL.findall(line))
        if meaningful < 2 or meaningful * 2 < len(line.replace(" ", "")):
            continue
        kept.append(line)
    return kept


def numbers_in(line: str) -> list[str]:
    """The digit runs of a line, in order. See `is_repeat` for why they get a veto."""
    return _DIGITS.findall(line)


def is_repeat(line: str, seen: Sequence[str], threshold: float = DEFAULT_SIMILARITY) -> bool:
    """Whether `line` is a line already in `seen`, read again.

    Exact match first because that is the common case and it is free; the fuzzy pass then
    catches the same caption read with a character or two different, which is what
    actually happens between two stills of one card.

    **Differing digits veto the fuzzy pass.** Similarity alone is too blunt on short
    lines: "填充第 0 行的正文内容" and "填充第 1 行的正文内容" are 92% alike and are not
    the same line at all, and neither are two prices or two list items. Numbers are also
    the part of a caption most worth keeping - the prompt asks the model not to alter
    them - so losing one to de-duplication is the expensive mistake here. The trade is
    that a mis-read digit (`15999` as `1S999`) leaves a near-duplicate in the transcript,
    which the model can reconcile; a deleted number it cannot.
    """
    if line in seen:
        return True
    digits = numbers_in(line)
    for earlier in seen:
        if numbers_in(earlier) != digits:
            continue
        if difflib.SequenceMatcher(None, line, earlier).ratio() >= threshold:
            return True
    return False


def dedupe(
    per_shot: Iterable[Sequence[str]],
    *,
    threshold: float = DEFAULT_SIMILARITY,
    lookback: int = LOOKBACK,
) -> list[str]:
    """One transcript out of per-still line lists, with the repeats dropped.

    A card that stays on screen for three stills contributes its text once. Order is the
    order it appeared in, which is the only order that means anything for a summary.
    """
    transcript: list[str] = []
    for lines in per_shot:
        for line in lines:
            seen = transcript[-lookback:] if lookback > 0 else transcript
            if not is_repeat(line, seen, threshold):
                transcript.append(line)
    return transcript


@dataclass(frozen=True)
class ShotText:
    """What one still said."""

    index: int
    time: float
    file: str
    lines: tuple[str, ...] = ()
    #: Kept so a bad read can be looked at instead of guessed at.
    raw: str = ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class Extraction:
    """The text of a whole clip, and where each piece of it came from."""

    video: Path
    shots: tuple[ShotText, ...] = ()
    lines: tuple[str, ...] = ()
    source: str = SOURCE_OCR
    lang: str = DEFAULT_LANG

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def is_empty(self) -> bool:
        return not self.lines

    def manifest(self) -> dict:
        return {
            "video": str(self.video),
            "source": self.source,
            "lang": self.lang,
            "lines": list(self.lines),
            "shots": [
                {
                    "index": shot.index,
                    "time": round(shot.time, 3),
                    "file": shot.file,
                    "lines": list(shot.lines),
                }
                for shot in self.shots
            ],
        }

    def summary(self) -> str:
        read = sum(len(shot.lines) for shot in self.shots)
        dropped = read - len(self.lines)
        head = (
            f"{self.source} ({self.lang}): {len(self.lines)} line(s) from "
            f"{len(self.shots)} still(s)"
        )
        if dropped > 0:
            head += f", {dropped} repeated line(s) dropped"
        if self.is_empty:
            head += "\n  nothing was read - check the language data and the stills"
        return head


def extract_text(
    shots: ShotSet | Sequence[Shot],
    *,
    video: Path | None = None,
    lang: str = DEFAULT_LANG,
    psm: int = DEFAULT_PSM,
    tessdata_dir: Path | None = None,
    threshold: float = DEFAULT_SIMILARITY,
    run: Runner = run_default,
    timeout: float | None = 120.0,
) -> Extraction:
    """OCR every still of a set and fold the results into one transcript."""
    if isinstance(shots, ShotSet):
        entries, source_video = list(shots.shots), video or shots.video
    else:
        entries, source_video = list(shots), video or Path("")
    if not have_tool(TESSERACT):
        raise MediaError(MISSING_TESSERACT)

    read: list[ShotText] = []
    for shot in entries:
        raw = ocr_image(
            shot.path, lang=lang, psm=psm, tessdata_dir=tessdata_dir, run=run, timeout=timeout
        )
        read.append(
            ShotText(
                index=shot.index,
                time=shot.time,
                file=shot.path.name,
                lines=tuple(clean_lines(raw)),
                raw=raw,
            )
        )
    return Extraction(
        video=Path(source_video),
        shots=tuple(read),
        lines=tuple(dedupe((shot.lines for shot in read), threshold=threshold)),
        source=SOURCE_OCR,
        lang=lang,
    )


def write_text(extraction: Extraction, out_dir: Path) -> tuple[Path, Path]:
    """`text.txt` for reading and for the summarizer, `text.json` for the per-still detail."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    text_path = out_dir / TEXT_NAME
    detail_path = out_dir / DETAIL_NAME
    text_path.write_text(extraction.text + ("\n" if extraction.lines else ""), encoding="utf-8")
    detail_path.write_text(
        json.dumps(extraction.manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return text_path, detail_path


def check_tesseract(lang: str = DEFAULT_LANG) -> str:
    """Report whether OCR can run at all, for `wxclip extract --check`."""
    if not have_tool(TESSERACT):
        return MISSING_TESSERACT
    binary = find_tool(TESSERACT)
    langs = run_tool(TESSERACT, ["--list-langs"], run=run_default)
    available = {line.strip() for line in langs.splitlines()[1:] if line.strip()}
    wanted = [part for part in lang.split("+") if part]
    missing = [part for part in wanted if part not in available]
    lines = [f"{binary}", f"  installed: {', '.join(sorted(available)) or '(none)'}"]
    if missing:
        lines.append(f"  missing:   {', '.join(missing)}")
        lines.append(language_hint("+".join(missing)))
    else:
        lines.append(f"  {lang} is ready")
    return "\n".join(lines)
