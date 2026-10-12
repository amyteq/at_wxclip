"""Choosing which moments get a screenshot, and writing them out."""

import json

import pytest

from tests.conftest import SCENE_OUTPUT
from wx_dlh.frames import (
    STRATEGY_EVEN,
    STRATEGY_INTERVAL,
    STRATEGY_SCENE,
    cull,
    detect_scenes,
    even_times,
    grab,
    interval_times,
    parse_scene_output,
    plan,
    read_manifest,
    shot_name,
    take_shots,
    write_manifest,
)
from wx_dlh.media import MediaError, MediaInfo


def info(duration=7.476, **kw):
    return MediaInfo(path=kw.pop("path", "v.mp4"), duration=duration, **kw)


# -- reading what ffmpeg printed -------------------------------------------------------


def test_scene_output_is_parsed_into_times_and_scores():
    pairs = parse_scene_output(SCENE_OUTPUT)
    assert [round(t, 3) for t, _ in pairs] == [0.0, 1.2, 3.567, 4.867, 6.167]
    assert pairs[1][1] == pytest.approx(0.387536)


def test_the_opening_frame_arrives_with_a_zero_score():
    """`eq(n,0)` puts it in the set; the scene filter cannot score the first frame."""
    assert parse_scene_output(SCENE_OUTPUT)[0] == (0.0, 0.0)


def test_a_frame_header_with_no_score_is_dropped():
    truncated = "frame:0    pts:0       pts_time:0\nlavfi.scene_score=0.5\nframe:1 pts:9 pts_time:1"
    assert parse_scene_output(truncated) == [(0.0, 0.5)]


def test_output_without_any_scene_lines_is_empty_not_an_error():
    assert parse_scene_output("") == []
    assert parse_scene_output("Duration: 00:00:07.48\nStream #0:0 Video: h264") == []


def test_detect_scenes_asks_for_the_opening_frame_and_the_threshold(video, ffmpeg):
    detect_scenes(video, threshold=0.35, run=ffmpeg)
    argv = " ".join(ffmpeg.calls_matching("metadata=print")[0])
    assert r"eq(n\,0)" in argv  # or the first frame is never in the set
    assert r"gt(scene\,0.35)" in argv
    assert "-f null" in argv  # the detection pass must not write frames


# -- spacing ---------------------------------------------------------------------------


def test_even_times_uses_midpoints():
    """Not edges: a seek to the final timestamp lands past the last frame."""
    assert even_times(10.0, 2) == [2.5, 7.5]
    assert even_times(8.0, 4) == [1.0, 3.0, 5.0, 7.0]
    assert max(even_times(10.0, 5)) < 10.0


def test_even_times_copes_with_an_unknown_duration():
    assert even_times(0.0, 5) == [0.0]
    assert even_times(10.0, 0) == []


def test_interval_times_starts_at_zero_and_stays_inside_the_file():
    assert interval_times(10.0, 4.0) == [0.0, 4.0, 8.0]
    assert all(t < 7.5 for t in interval_times(7.5, 2.0))


def test_a_non_positive_interval_is_rejected():
    with pytest.raises(ValueError, match="must be positive"):
        interval_times(10.0, 0)


# -- culling ---------------------------------------------------------------------------


def test_cull_keeps_the_opening_frame_and_the_biggest_changes():
    scenes = [(0.0, 0.0), (1.0, 0.9), (2.0, 0.2), (3.0, 0.8), (4.0, 0.3)]
    kept = cull(scenes, 3)
    assert [t for t, _ in kept] == [0.0, 1.0, 3.0]  # in time order, not score order
    assert kept[0][1] is None  # the opening frame is "where", not "what changed"


def test_cull_never_drops_the_opening_frame_for_scoring_zero():
    """It scores 0, so a plain highest-score cull would throw away the one still that
    is always worth having."""
    scenes = [(0.0, 0.0), (1.0, 0.5), (2.0, 0.6)]
    assert [t for t, _ in cull(scenes, 1)] == [0.0]


def test_cull_leaves_a_short_list_alone():
    scenes = [(0.0, 0.0), (1.0, 0.5)]
    assert len(cull(scenes, 12)) == 2
    assert cull(scenes, 0) == []


# -- strategy ---------------------------------------------------------------------------


def test_plan_uses_scene_detection_when_there_is_enough_to_go_on(video, ffmpeg):
    strategy, times = plan(info(path=video), min_shots=3, run=ffmpeg)
    assert strategy == STRATEGY_SCENE
    assert len(times) == 5


def test_plan_falls_back_to_spacing_when_nothing_changes(video, ffmpeg):
    """A single card held for a minute scores nothing anywhere. One screenshot is not
    a screenshot set, so spacing takes over."""
    ffmpeg.rules = [("metadata=print", "frame:0 pts:0 pts_time:0\nlavfi.scene_score=0.0")]
    strategy, times = plan(info(duration=60.0, path=video), min_shots=3, run=ffmpeg)
    assert strategy == STRATEGY_EVEN
    assert len(times) == 12  # one every ~5s, capped at max_shots
    assert all(score is None for _, score in times)


def test_the_spacing_fallback_respects_min_and_max(video, ffmpeg):
    ffmpeg.rules = [("metadata=print", "")]
    _, times = plan(info(duration=2.0, path=video), min_shots=3, max_shots=12, run=ffmpeg)
    assert len(times) == 3  # a 2s clip still gets min_shots
    _, times = plan(info(duration=600.0, path=video), min_shots=3, max_shots=6, run=ffmpeg)
    assert len(times) == 6


def test_every_overrides_detection_entirely(video, ffmpeg):
    strategy, times = plan(info(duration=10.0, path=video), every=4.0, run=ffmpeg)
    assert strategy == STRATEGY_INTERVAL
    assert [t for t, _ in times] == [0.0, 4.0, 8.0]
    assert not ffmpeg.calls_matching("metadata=print")  # no detection pass was run


# -- writing ----------------------------------------------------------------------------


def test_shot_name_sorts_by_index_and_reads_as_a_timestamp():
    assert shot_name(3, 12.4) == "shot-003-t12.40s.jpg"
    assert sorted([shot_name(10, 1.0), shot_name(2, 9.0)])[0] == shot_name(2, 9.0)


def test_grab_seeks_before_the_input(video, tmp_path, ffmpeg):
    dest = tmp_path / "out" / "still.jpg"
    grab(video, 3.5, dest, run=ffmpeg)
    argv = ffmpeg.calls_matching("-frames:v")[0]
    # -ss before -i is input seeking: it does not decode what precedes the timestamp.
    assert argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-ss") + 1] == "3.500"
    assert dest.is_file()


def test_grab_scales_down_only(video, tmp_path, ffmpeg):
    grab(video, 0, tmp_path / "s.jpg", width=640, run=ffmpeg)
    assert "scale='min(640,iw)':-2" in " ".join(ffmpeg.calls_matching("-frames:v")[0])


def test_grab_without_a_width_leaves_the_frame_alone(video, tmp_path, ffmpeg):
    grab(video, 0, tmp_path / "s.jpg", width=None, run=ffmpeg)
    assert "scale" not in " ".join(ffmpeg.calls_matching("-frames:v")[0])


def test_grab_complains_if_ffmpeg_wrote_nothing(video, tmp_path, ffmpeg):
    ffmpeg.rules = [("-frames:v", "")]  # succeeds but writes no file
    with pytest.raises(MediaError, match="wrote no still"):
        grab(video, 0, tmp_path / "missing.jpg", run=ffmpeg)


def test_take_shots_writes_a_still_per_chosen_moment(video, tmp_path, ffmpeg):
    shots = take_shots(video, tmp_path / "out", run=ffmpeg)
    assert shots.strategy == STRATEGY_SCENE
    assert len(shots) == 5
    assert [shot.index for shot in shots.shots] == [1, 2, 3, 4, 5]
    assert all(shot.path.is_file() for shot in shots.shots)
    assert shots.shots[0].score is None
    # Full precision is kept on the Shot; only the manifest rounds it.
    assert shots.shots[1].score == pytest.approx(0.387536)
    assert "5 screenshot(s)" in shots.summary()


def test_the_manifest_round_trips(video, tmp_path, ffmpeg):
    shots = take_shots(video, tmp_path / "out", run=ffmpeg)
    path = write_manifest(shots)
    assert path.name == "shots.json"

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["strategy"] == STRATEGY_SCENE
    assert saved["height"] == 2344
    assert saved["shots"][0]["score"] is None

    loaded = read_manifest(path)
    assert [s.time for s in loaded.shots] == [round(s.time, 3) for s in shots.shots]
    assert [s.path for s in loaded.shots] == [s.path for s in shots.shots]
    assert loaded.video == video


def test_reading_a_broken_manifest_says_so(tmp_path):
    bad = tmp_path / "shots.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(MediaError, match="could not read"):
        read_manifest(bad)
