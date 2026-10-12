"""Building the model request, and reading the answer.

No test here reaches the network: `summarize` takes a `post` callable and `post_messages`
takes an `urlopen`, so the request that would have gone out is inspected directly. That
covers everything except the socket - the headers, the body shape, the response parsing
and every error path.
"""

import base64
import json
import urllib.error

import pytest

from tests.conftest import FakeRunner
from wx_dlh.frames import Shot, ShotSet
from wx_dlh.media import MediaInfo
from wx_dlh.summarize import (
    API_URL,
    API_VERSION,
    DEFAULT_MODEL,
    OAUTH_BETA,
    SYSTEM_PROMPT,
    SummarizeError,
    Summary,
    build_body,
    build_content,
    credentials,
    describe_clip,
    encode_image,
    pick_images,
    post_messages,
    read_response,
    render,
    summarize,
    write_summary,
)

KEY_ENV = {"ANTHROPIC_API_KEY": "sk-ant-test"}


def shots(tmp_path, count=5):
    entries = []
    for i in range(1, count + 1):
        path = tmp_path / f"shot-00{i}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0jpeg-ish")
        entries.append(Shot(index=i, time=float(i), path=path))
    return ShotSet(
        video=tmp_path / "v.mp4", directory=tmp_path, strategy="scene", shots=tuple(entries)
    )


def answer(text="## 一句话概要\n\n讲了一个开源项目。", **kw):
    """A Messages API response, shaped the way the real one is."""
    return {
        "model": DEFAULT_MODEL,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 8000, "output_tokens": 250},
        **kw,
    }


# -- choosing the stills ------------------------------------------------------------------


def test_every_still_is_sent_when_there_are_few_enough(tmp_path):
    entries = shots(tmp_path, 3).shots
    assert pick_images(entries, 4) == list(entries)


def test_the_stills_are_spread_across_the_clip_not_taken_in_order(tmp_path):
    """The opening stills are the ones most like each other; taking the first four would
    send four pictures of the title card."""
    entries = shots(tmp_path, 9).shots
    picked = pick_images(entries, 4)
    assert len(picked) == 4
    assert picked[0] == entries[0]
    assert picked[-1] == entries[-1]  # the end of the clip is always represented


def test_asking_for_no_images_sends_none(tmp_path):
    assert pick_images(shots(tmp_path).shots, 0) == []
    assert pick_images((), 4) == []


# -- credentials ---------------------------------------------------------------------------


def test_an_api_key_goes_on_x_api_key():
    assert credentials(KEY_ENV) == ("x-api-key", "sk-ant-test")


def test_an_oauth_token_goes_on_authorization():
    """Crossing these two is a 401 that reads like a bad key."""
    header, value = credentials({"ANTHROPIC_AUTH_TOKEN": "oauth-abc"})
    assert header == "authorization"
    assert value == "Bearer oauth-abc"


def test_the_api_key_wins_when_both_are_set():
    assert credentials({**KEY_ENV, "ANTHROPIC_AUTH_TOKEN": "t"})[0] == "x-api-key"


def test_no_credentials_says_what_to_set():
    with pytest.raises(SummarizeError) as exc:
        credentials({})
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert "ANTHROPIC_AUTH_TOKEN" in str(exc.value)


def test_a_blank_key_counts_as_absent():
    with pytest.raises(SummarizeError):
        credentials({"ANTHROPIC_API_KEY": "   "})


# -- the request -----------------------------------------------------------------------------


def test_the_body_carries_effort_inside_output_config():
    body = build_body([{"type": "text", "text": "x"}], effort="high")
    assert body["output_config"] == {"effort": "high"}
    assert body["model"] == DEFAULT_MODEL
    assert body["system"] == SYSTEM_PROMPT
    assert body["messages"][0]["role"] == "user"


def test_the_body_sends_no_thinking_key():
    """Thinking is adaptive by default on this model family; sending a configuration for
    it is what gets rejected."""
    assert "thinking" not in build_body([{"type": "text", "text": "x"}])


def test_the_request_headers_are_what_the_api_wants():
    sent = {}

    class Response:
        def read(self):
            return json.dumps(answer()).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["headers"] = {k.lower(): v for k, v in request.headers.items()}
        sent["body"] = json.loads(request.data)
        sent["method"] = request.get_method()
        return Response()

    post_messages({"model": DEFAULT_MODEL}, env=KEY_ENV, urlopen=urlopen)
    assert sent["url"] == API_URL
    assert sent["method"] == "POST"
    assert sent["headers"]["anthropic-version"] == API_VERSION
    assert sent["headers"]["x-api-key"] == "sk-ant-test"
    assert "anthropic-beta" not in sent["headers"]  # only an OAuth call needs that


def test_an_oauth_call_adds_the_beta_flag():
    sent = {}

    class Response:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):
        sent.update({k.lower(): v for k, v in request.headers.items()})
        return Response()

    post_messages({}, env={"ANTHROPIC_AUTH_TOKEN": "t"}, urlopen=urlopen)
    assert sent["authorization"] == "Bearer t"
    assert sent["anthropic-beta"] == OAUTH_BETA


def test_the_content_puts_metadata_and_text_first_then_the_stills(tmp_path):
    info = MediaInfo(
        path=tmp_path / "v.mp4",
        duration=7.5,
        width=1080,
        height=2344,
        video_codec="h264",
        audio_codec="aac",
    )
    blocks = build_content(
        "价值 15999 元视觉效果", shots(tmp_path, 2).shots, info=info, image_width=None
    )
    assert blocks[0]["type"] == "text"
    assert "7.5 秒" in blocks[0]["text"]
    assert "竖屏" in blocks[0]["text"]
    assert "价值 15999 元视觉效果" in blocks[0]["text"]
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 2
    assert images[0]["source"]["media_type"] == "image/jpeg"
    # Each image is introduced by its timestamp so it can be lined up with the text.
    labels = [b["text"] for b in blocks if b["type"] == "text" and "秒" in b["text"]]
    assert any("1.00 秒" in label for label in labels)


def test_an_empty_transcript_is_said_in_words_not_left_blank(tmp_path):
    blocks = build_content("", (), image_width=None)
    assert "没有识别到文字" in blocks[0]["text"]


def test_describe_clip_includes_the_channels_metadata_when_there_is_any():
    text = describe_clip(None, description="开源项目介绍", nickname="某某")
    assert "开源项目介绍" in text
    assert "某某" in text


def test_a_still_is_base64_encoded_as_is_when_no_width_is_given(tmp_path):
    path = tmp_path / "s.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0raw")
    media_type, data = encode_image(path, width=None)
    assert media_type == "image/jpeg"
    assert base64.standard_b64decode(data) == b"\xff\xd8\xff\xe0raw"


def test_a_wide_still_is_handed_to_ffmpeg_to_shrink(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    path = tmp_path / "s.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0raw")
    runner = FakeRunner(default="")
    encode_image(path, width=768, run=runner)
    assert "scale='min(768,iw)':-2" in " ".join(runner.calls[0])


def test_encoding_a_missing_still_is_an_error(tmp_path):
    with pytest.raises(SummarizeError, match="is not a file"):
        encode_image(tmp_path / "gone.jpg", width=None)


# -- the response -----------------------------------------------------------------------------


def test_the_text_blocks_are_joined_and_the_usage_is_kept():
    summary = read_response(answer(), images=4)
    assert summary.text.startswith("## 一句话概要")
    assert (summary.input_tokens, summary.output_tokens) == (8000, 250)
    assert summary.images == 4
    assert summary.ok
    assert "8000 in / 250 out" in summary.summary()


def test_non_text_blocks_are_ignored():
    payload = answer()
    payload["content"] = [
        {"type": "thinking", "thinking": ""},
        {"type": "text", "text": "总结"},
    ]
    assert read_response(payload).text == "总结"


def test_a_refusal_is_reported_rather_than_read_as_a_summary():
    """A refusal is an ordinary HTTP 200 with no usable text in it."""
    payload = answer(
        text="",
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "nope"},
    )
    summary = read_response(payload)
    assert not summary.ok
    assert "declined" in summary.refusal
    assert "nope" in summary.refusal
    assert "!!" in summary.summary()


def test_a_response_with_no_content_is_not_ok():
    assert not read_response({"content": []}).ok


def test_unparseable_usage_does_not_crash_the_read():
    assert read_response(answer(usage={"input_tokens": None})).input_tokens == 0


# -- errors -------------------------------------------------------------------------------------


def http_error(code, body=b'{"error": {"message": "boom"}}'):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(API_URL, code, "reason", {}, __import__("io").BytesIO(body))

    return urlopen


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, "credentials were rejected"),
        (403, "not allowed to call this model"),
        (404, "no such model"),
        (413, "lower --max-images"),
        (429, "rate limited"),
        (500, "usually transient"),
    ],
)
def test_each_http_failure_says_what_to_do_about_it(code, expected):
    with pytest.raises(SummarizeError) as exc:
        post_messages({}, env=KEY_ENV, urlopen=http_error(code))
    assert expected in str(exc.value)
    assert "boom" in str(exc.value)  # the API's own reason is quoted


def test_a_network_failure_names_the_endpoint():
    def urlopen(request, timeout=None):
        raise OSError("no route to host")

    with pytest.raises(SummarizeError, match="could not reach"):
        post_messages({}, env=KEY_ENV, urlopen=urlopen)


def test_a_non_json_body_is_an_error_not_a_crash():
    class Response:
        def read(self):
            return b"<html>502</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with pytest.raises(SummarizeError, match="not json"):
        post_messages({}, env=KEY_ENV, urlopen=lambda *a, **kw: Response())


# -- end to end (with the transport faked) ------------------------------------------------------


def test_summarize_sends_the_transcript_and_the_stills(tmp_path):
    captured = {}

    def post(body, **kwargs):
        captured["body"] = body
        captured["kwargs"] = kwargs
        return answer()

    summary = summarize(
        "价值 15999 元视觉效果",
        shots(tmp_path, 5),
        model="claude-opus-5",
        max_images=2,
        image_width=None,
        env=KEY_ENV,
        post=post,
    )
    assert summary.ok
    assert summary.images == 2
    content = captured["body"]["messages"][0]["content"]
    assert sum(1 for b in content if b["type"] == "image") == 2
    assert captured["kwargs"]["env"] == KEY_ENV


def test_summarizing_nothing_at_all_is_refused_before_any_call(tmp_path):
    def post(body, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("should not have called the API")

    with pytest.raises(SummarizeError, match="nothing to summarize"):
        summarize("   ", (), post=post)


def test_text_only_is_allowed(tmp_path):
    summary = summarize("有文字没截图", (), env=KEY_ENV, post=lambda body, **kw: answer())
    assert summary.ok
    assert summary.images == 0


# -- writing -------------------------------------------------------------------------------


def test_the_summary_file_carries_the_text_and_how_it_was_made(tmp_path):
    summary = Summary(text="## 一句话概要\n\n讲了一个开源项目。", images=4, model="claude-opus-5")
    path = write_summary(summary, tmp_path, video=tmp_path / "v.mp4", source="ocr (chi_sim+eng)")
    body = path.read_text(encoding="utf-8")
    assert path.name == "summary.md"
    assert "讲了一个开源项目。" in body
    assert "claude-opus-5" in body
    assert "ocr (chi_sim+eng)" in body
    assert "v.mp4" in body


def test_a_refusal_is_written_into_the_file_too():
    rendered = render(Summary(text="", refusal="the model declined to summarize this clip"))
    assert "declined" in rendered
    assert "模型没有返回内容" in rendered
