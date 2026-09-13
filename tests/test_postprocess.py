"""The three steps in sequence, and the reuse that makes rerunning them cheap."""

import json

import pytest

from wxclip import postprocess
from wxclip.extract import DETAIL_NAME, TEXT_NAME
from wxclip.frames import MANIFEST_NAME, ShotSet
from wxclip.summarize import SUMMARY_NAME, Summary

OCR_TEXT = "价值 15999 元视觉效果\n开发者直接开源了\n"


@pytest.fixture
def ocr(monkeypatch, ffmpeg):
    """Faked tesseract on top of the faked ffmpeg, so a whole run needs no binaries."""
    monkeypatch.setattr("wxclip.extract.have_tool", lambda name: True)
    monkeypatch.setattr("wxclip.extract.ocr_image", lambda image, **kw: OCR_TEXT)
    return ffmpeg


@pytest.fixture
def model(monkeypatch):
    """A stand-in for the API call that records what it was asked."""
    calls = []

    def fake(text, shots=(), **kw):
        calls.append({"text": text, "shots": shots, **kw})
        # Unwrapped the way the real summarize() does - a ShotSet has a length but is
        # not itself iterable.
        entries = shots.shots if isinstance(shots, ShotSet) else tuple(shots)
        return Summary(text="## 一句话概要\n\n讲了一个开源项目。", images=len(entries))

    monkeypatch.setattr("wxclip.summarize.summarize", fake)
    return calls


def test_a_clip_gets_its_own_directory_named_after_it(tmp_path):
    video = tmp_path / "video-1f3c9a2b41.mp4"
    assert postprocess.bundle_dir(tmp_path / "processed", video).name == "video-1f3c9a2b41"


def test_process_writes_all_three_artefacts(video, tmp_path, ocr, model):
    bundle = postprocess.process(video, tmp_path / "processed", echo=lambda line: None, run=ocr)
    names = {path.name for path in bundle.paths}
    assert names == {MANIFEST_NAME, TEXT_NAME, DETAIL_NAME, SUMMARY_NAME}
    assert len(bundle.shots) == 5
    assert bundle.extraction is not None and not bundle.extraction.is_empty
    assert bundle.model_summary is not None and bundle.model_summary.ok
    assert bundle.directory.name == video.stem
    assert "5 still(s)" in bundle.report()


def test_the_summary_step_is_given_the_transcript_and_the_stills(video, tmp_path, ocr, model):
    postprocess.process(video, tmp_path / "p", echo=lambda line: None, run=ocr)
    assert len(model) == 1
    assert "价值 15999 元视觉效果" in model[0]["text"]
    assert len(model[0]["shots"].shots) == 5
    assert model[0]["info"] is not None  # the metadata block needs the probe


def test_no_summary_stops_after_the_text(video, tmp_path, ocr, model):
    """So the first two steps work on a machine with no credentials at all."""
    bundle = postprocess.process(
        video, tmp_path / "p", with_summary=False, echo=lambda line: None, run=ocr
    )
    assert bundle.model_summary is None
    assert model == []
    assert SUMMARY_NAME not in {path.name for path in bundle.paths}
    assert TEXT_NAME in {path.name for path in bundle.paths}


def test_with_text_false_stops_after_the_stills(video, tmp_path, ocr, model):
    bundle = postprocess.process(
        video, tmp_path / "p", with_text=False, echo=lambda line: None, run=ocr
    )
    assert bundle.extraction is None
    assert {path.name for path in bundle.paths} == {MANIFEST_NAME}


# -- reuse ---------------------------------------------------------------------------------


def test_a_second_run_reuses_the_stills_instead_of_taking_them_again(video, tmp_path, ocr):
    directory = tmp_path / "p" / video.stem
    first = postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    grabs = len(ocr.calls_matching("-frames:v"))

    second = postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    assert len(ocr.calls_matching("-frames:v")) == grabs  # no new ffmpeg work
    assert [s.time for s in second.shots] == [round(s.time, 3) for s in first.shots]


def test_redo_takes_the_stills_again(video, tmp_path, ocr):
    directory = tmp_path / "p" / video.stem
    postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    grabs = len(ocr.calls_matching("-frames:v"))
    postprocess.ensure_shots(video, directory, redo=True, echo=lambda line: None, run=ocr)
    assert len(ocr.calls_matching("-frames:v")) > grabs


def test_stills_recorded_but_deleted_are_taken_again(video, tmp_path, ocr):
    """A manifest pointing at files that are gone is not something to reuse."""
    directory = tmp_path / "p" / video.stem
    shots = postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    for shot in shots.shots:
        shot.path.unlink()
    grabs = len(ocr.calls_matching("-frames:v"))
    postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    assert len(ocr.calls_matching("-frames:v")) > grabs


def test_the_transcript_is_reused_so_iterating_on_the_prompt_is_cheap(video, tmp_path, ocr):
    """Twenty rounds of prompt work should not mean twenty rounds of OCR."""
    directory = tmp_path / "p" / video.stem
    shots = postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    first = postprocess.ensure_text(shots, directory, echo=lambda line: None, run=ocr)

    read = []
    postprocess.ensure_text(shots, directory, echo=read.append, run=ocr)
    assert any("reusing" in line for line in read)
    assert postprocess.read_extraction(directory / DETAIL_NAME).lines == first.lines


def test_a_corrupt_text_json_is_ignored_rather_than_trusted(video, tmp_path, ocr):
    directory = tmp_path / "p" / video.stem
    directory.mkdir(parents=True)
    (directory / DETAIL_NAME).write_text("{ not json", encoding="utf-8")
    shots = postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    extraction = postprocess.ensure_text(shots, directory, echo=lambda line: None, run=ocr)
    assert not extraction.is_empty  # it re-ran OCR instead of failing


# -- finding things again --------------------------------------------------------------------


def test_find_video_reads_the_path_back_out_of_the_manifest(video, tmp_path, ocr):
    directory = tmp_path / "p" / video.stem
    postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    assert postprocess.find_video(directory) == video


def test_find_video_is_none_when_there_is_no_manifest(tmp_path):
    assert postprocess.find_video(tmp_path) is None


def test_a_subtitle_stream_is_pointed_out_rather_than_silently_ignored(
    video, tmp_path, ocr, model, monkeypatch
):
    """No Channels download has had one; if one does, OCR is doing by hand what a stream
    copy would do exactly, and the operator should hear about it."""
    from wxclip.media import MediaInfo

    monkeypatch.setattr(
        "wxclip.postprocess.probe",
        lambda path, **kw: MediaInfo(
            path=path,
            duration=7.5,
            width=1080,
            height=2344,
            video_codec="h264",
            subtitle_streams=(2,),
        ),
    )
    said = []
    postprocess.process(video, tmp_path / "p", echo=said.append, run=ocr)
    assert any("subtitle stream" in line for line in said)


def test_the_manifest_records_the_strategy_that_was_used(video, tmp_path, ocr):
    directory = tmp_path / "p" / video.stem
    postprocess.ensure_shots(video, directory, echo=lambda line: None, run=ocr)
    saved = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert saved["strategy"] == "scene"
