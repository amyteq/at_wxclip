"""OCR over the stills, and the cleanup that makes the result usable.

The OCR call itself is one subprocess; what is worth testing is everything around it -
which lines survive, which repeats are collapsed, and whether a missing language file
produces an error anyone can act on.
"""

import json

import pytest

from tests.conftest import FakeRunner
from wxclip.extract import (
    SOURCE_OCR,
    Extraction,
    ShotText,
    check_tesseract,
    clean_lines,
    dedupe,
    extract_text,
    is_repeat,
    language_hint,
    ocr_image,
    write_text,
)
from wxclip.frames import Shot, ShotSet
from wxclip.media import MediaError

#: What tesseract really read off three consecutive stills of the M0 sample. The card
#: holds still, so the headline comes back every time - which is the whole problem.
SHOT_ONE = """\
价值 15999 元视觉效果
普通 Mac 实现折玛屏透视
开发者直接开源了
开发者 Makito 开源项目 Mac-Duo
"""
SHOT_TWO = """\
普通 Mac RT SHSM
价值 15999 元视觉效果
普通 Mac 实现折私屏透视
开发者直接开源了
体验。
"""
SHOT_THREE = """\
价值 15999 元视觉效果
普通 Mac 实现折私屏透视
开发者直接开源了
已上传 GitHub ，采用 Apache2.0
"""


def shot_set(tmp_path, count=3):
    shots = []
    for i in range(1, count + 1):
        path = tmp_path / f"shot-00{i}-t{i}.00s.jpg"
        path.write_bytes(b"jpeg")
        shots.append(Shot(index=i, time=float(i), path=path))
    return ShotSet(
        video=tmp_path / "v.mp4", directory=tmp_path, strategy="scene", shots=tuple(shots)
    )


def ocr_runner(*outputs):
    """A fake tesseract answering each still in turn."""
    remaining = list(outputs)

    def run(argv, timeout=None, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(argv, 0, remaining.pop(0) if remaining else "", "")

    return run


# -- line cleanup -----------------------------------------------------------------------


def test_clean_lines_collapses_whitespace_and_drops_blanks():
    assert clean_lines("  价值   15999 元\n\n  \n开发者直接开源了  ") == [
        "价值 15999 元",
        "开发者直接开源了",
    ]


def test_a_single_stray_glyph_is_dropped():
    """One character is what OCR produces from a photograph, not a caption."""
    assert clean_lines("哦\nA\n开发者直接开源了") == ["开发者直接开源了"]


def test_a_line_that_is_mostly_punctuation_is_dropped():
    assert clean_lines("|||---|||\n。。。，，，\n工具运行在 macOS 菜单栏") == [
        "工具运行在 macOS 菜单栏"
    ]


def test_latin_and_mixed_lines_are_kept():
    """Channels cards put brand names inside Chinese sentences - both must survive."""
    kept = clean_lines("Makito\nApache2.0 开源协议\n将 iPhone Duo 的翻")
    assert kept == ["Makito", "Apache2.0 开源协议", "将 iPhone Duo 的翻"]


# -- repeat detection -------------------------------------------------------------------


def test_an_identical_line_is_a_repeat():
    assert is_repeat("价值 15999 元视觉效果", ["价值 15999 元视觉效果"])


def test_a_line_read_with_one_character_wrong_is_still_a_repeat():
    """Two stills of one card differ by a mis-read character, not by meaning."""
    assert is_repeat("普通 Mac 实现折私屏透视", ["普通 Mac 实现折玛屏透视"])


def test_two_different_sentences_are_not_repeats():
    assert not is_repeat("开发者直接开源了", ["价值 15999 元视觉效果"])
    assert not is_repeat("工具运行在 macOS 菜单栏", ["已上传 GitHub ，采用 Apache2.0"])


def test_nothing_is_a_repeat_of_an_empty_transcript():
    assert not is_repeat("价值 15999 元视觉效果", [])


# -- de-duplication ---------------------------------------------------------------------


def test_a_card_held_across_stills_contributes_its_text_once():
    lines = dedupe([clean_lines(SHOT_ONE), clean_lines(SHOT_TWO), clean_lines(SHOT_THREE)])
    assert lines.count("价值 15999 元视觉效果") == 1
    assert sum(1 for line in lines if line.startswith("普通 Mac 实现折")) == 1
    assert "已上传 GitHub ，采用 Apache2.0" in lines  # new text still gets through


def test_repeats_are_found_however_far_back_they_are():
    """The regression that a lookback window caused: one still of a card yields about
    ten lines, so any short window is shorter than a single still's output and the
    headline comes back on every card."""
    card = clean_lines(SHOT_ONE) + [f"填充第 {n} 行的正文内容" for n in range(10)]
    lines = dedupe([card, card, card])
    assert lines.count("价值 15999 元视觉效果") == 1
    assert len(lines) == len(card)


def test_a_lookback_window_can_still_be_asked_for():
    card = ["标题一直都在"] + [f"正文第 {n} 行" for n in range(6)]
    windowed = dedupe([card, card], lookback=3)
    assert windowed.count("标题一直都在") == 2  # out of the window, so kept


def test_order_is_the_order_it_appeared_in():
    lines = dedupe([["第一句"], ["第二句"], ["第三句"]])
    assert lines == ["第一句", "第二句", "第三句"]


# -- the tesseract call -----------------------------------------------------------------


def test_missing_language_data_names_the_package_and_the_rootless_route(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    image = tmp_path / "s.jpg"
    image.write_bytes(b"jpeg")
    failing = FakeRunner(returncode=1, stderr="Failed loading language 'chi_sim'")
    with pytest.raises(MediaError) as exc:
        ocr_image(image, run=failing)
    message = str(exc.value)
    assert "tesseract-data-chi_sim" in message  # Arch
    assert "tesseract-ocr-chi-sim" in message  # Debian, with the dash
    assert "--tessdata-dir" in message  # and the way that needs no root


def test_language_hint_covers_every_language_asked_for():
    hint = language_hint("chi_sim+eng")
    assert "tesseract-data-chi_sim tesseract-data-eng" in hint
    assert "tesseract-ocr-chi-sim tesseract-ocr-eng" in hint


def test_ocr_image_passes_the_language_psm_and_tessdata_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    image = tmp_path / "s.jpg"
    image.write_bytes(b"jpeg")
    runner = FakeRunner(default="text")
    ocr_image(image, lang="chi_sim", psm=6, tessdata_dir=tmp_path / "td", run=runner)
    argv = " ".join(runner.calls[0])
    assert "-l chi_sim" in argv
    assert "--psm 6" in argv
    assert f"--tessdata-dir {tmp_path / 'td'}" in argv
    assert "stdout" in argv


def test_an_unrelated_tesseract_failure_is_not_dressed_up_as_a_language_problem(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    image = tmp_path / "s.jpg"
    image.write_bytes(b"jpeg")
    failing = FakeRunner(returncode=1, stderr="Image file s.jpg cannot be opened")
    with pytest.raises(MediaError, match="cannot be opened"):
        ocr_image(image, run=failing)


# -- the whole extraction ----------------------------------------------------------------


def test_extract_text_reads_every_still_and_folds_them_into_one_transcript(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    shots = shot_set(tmp_path)
    extraction = extract_text(shots, run=ocr_runner(SHOT_ONE, SHOT_TWO, SHOT_THREE))

    assert extraction.source == SOURCE_OCR
    assert len(extraction.shots) == 3
    assert extraction.shots[0].lines[0] == "价值 15999 元视觉效果"
    assert extraction.shots[0].raw == SHOT_ONE  # the raw read is kept for inspection
    assert extraction.lines.count("价值 15999 元视觉效果") == 1
    assert not extraction.is_empty
    assert "repeated line(s) dropped" in extraction.summary()
    assert extraction.video == shots.video


def test_extract_text_without_tesseract_says_how_to_install_it(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(MediaError, match="tesseract not found"):
        extract_text(shot_set(tmp_path))


def test_stills_that_read_as_nothing_produce_an_empty_extraction(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    extraction = extract_text(shot_set(tmp_path), run=ocr_runner("", "|||", "\n\n"))
    assert extraction.is_empty
    assert "nothing was read" in extraction.summary()


def test_write_text_saves_the_transcript_and_the_per_still_detail(tmp_path):
    extraction = Extraction(
        video=tmp_path / "v.mp4",
        shots=(ShotText(index=1, time=0.0, file="shot-001-t0.00s.jpg", lines=("甲", "乙")),),
        lines=("甲", "乙"),
    )
    text_path, detail_path = write_text(extraction, tmp_path / "out")
    assert text_path.read_text(encoding="utf-8") == "甲\n乙\n"

    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    assert detail["lines"] == ["甲", "乙"]
    assert detail["shots"][0]["file"] == "shot-001-t0.00s.jpg"
    assert detail["source"] == SOURCE_OCR


def test_an_empty_transcript_writes_an_empty_file_not_a_stray_newline(tmp_path):
    text_path, _ = write_text(Extraction(video=tmp_path / "v.mp4"), tmp_path / "out")
    assert text_path.read_text(encoding="utf-8") == ""


def test_check_reports_which_languages_are_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    listing = 'List of available languages in "/usr/share/tessdata" (2):\nafr\nosd\n'
    monkeypatch.setattr("wxclip.extract.run_tool", lambda *a, **kw: listing)
    report = check_tesseract("chi_sim+eng")
    assert "installed: afr, osd" in report
    assert "missing:   chi_sim, eng" in report


def test_check_says_so_when_everything_is_ready(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    listing = "List of available languages (3):\nchi_sim\neng\nosd\n"
    monkeypatch.setattr("wxclip.extract.run_tool", lambda *a, **kw: listing)
    assert "chi_sim+eng is ready" in check_tesseract("chi_sim+eng")


def test_check_without_tesseract_explains_the_install(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert "tesseract not found" in check_tesseract()
