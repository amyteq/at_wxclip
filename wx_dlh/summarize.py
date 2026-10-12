"""Ask a model what the clip was about.

The input is deliberately both halves of what the earlier steps produced: the OCR
transcript *and* a few of the stills. Either one alone is worse than the pair. OCR of a
Channels card recovers the words but not what is being shown - a laptop, a screen
recording, a face - and it mangles a character here and there; the stills carry the scene
and let the model repair an obviously mis-read word from context. Sending both costs a
few thousand extra input tokens and is the difference between a summary of the video and
a summary of some noisy text.

**Why this posts JSON itself instead of using the `anthropic` SDK.** The SDK would be the
normal choice, and it was tried first. It cannot be installed beside mitmproxy: it needs
`httpx2` -> `httpcore2` -> `h11>=0.16`, and mitmproxy pins `h11` below that, so the two
have no common solution. mitmproxy is wx-dlh's own dependency (`wx-dlh capture` runs on
it), so taking the SDK would mean `wx-dlh process` could not run in the same virtualenv as
the command that produces the files it processes - two environments for one tool. The
alternative, pinning `anthropic<1`, means writing against a superseded major whose request
surface for `effort` is not the current one.

So this module does what the rest of the project already does twice over - `keystream.py`
ports ISAAC64 rather than bridging to Node, `downloader.py` uses `urllib` rather than
httpx, both to keep mitmproxy the only runtime dependency. One POST to a documented endpoint
is less code than either of those, and it keeps the whole tool installable as one thing.
If mitmproxy ever loosens that pin, this is about thirty lines to replace with the SDK.

Credentials come from the environment the same way the SDK would read them:
`ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN` for an OAuth token. wx-dlh does not store
or log either one.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wx_dlh.frames import Shot, ShotSet
from wx_dlh.media import FFMPEG, MediaInfo, Runner, run_default, run_tool

API_URL = "https://api.anthropic.com/v1/messages"

#: The Messages API version header. Not a model version - it pins the request/response
#: shape, and is required on every call.
API_VERSION = "2023-06-01"

#: An OAuth bearer token needs this beta flag; an `x-api-key` call must not send it.
OAUTH_BETA = "oauth-2025-04-20"

DEFAULT_TIMEOUT = 120.0

#: Anthropic's most capable current model. Summarising a page of mis-read Chinese is
#: exactly the kind of small, judgement-heavy job it is good at, and one clip is one call.
DEFAULT_MODEL = "claude-opus-5"

#: A cap, not a target - the summary itself is a few hundred tokens. Adaptive thinking
#: draws from the same budget, so leaving room here costs nothing and truncating hurts.
DEFAULT_MAX_TOKENS = 16000

#: Reading a page of text and describing five stills is routine work, so it does not need
#: the top of the effort range. `--effort` moves it.
DEFAULT_EFFORT = "medium"

#: Enough stills to establish what is on screen without paying for near-duplicates.
DEFAULT_MAX_IMAGES = 4

#: Width the stills are downscaled to before they are sent. The transcript carries the
#: words, so these only have to show the scene; full 1080-wide frames cost ~3x the tokens
#: for no gain.
DEFAULT_IMAGE_WIDTH = 768

SUMMARY_NAME = "summary.md"

NO_CREDENTIALS = (
    "no Anthropic credentials in the environment. Set one of:\n"
    "  export ANTHROPIC_API_KEY=sk-ant-...      # an API key\n"
    "  export ANTHROPIC_AUTH_TOKEN=...          # an OAuth token\n"
    "`.env` is read too, so wx-dlh's own file can hold it - it is git-ignored."
)

#: The prompt. It lives here rather than in `evals/` so that an installed wheel carries
#: it; `evals/README.md` holds the fixtures it is judged against and points back here.
#: Changing this text means re-running the samples in `evals/summarize/`.
SYSTEM_PROMPT = """\
你是一个视频内容整理助手。用户会给你一个短视频的资料：从画面里 OCR 出来的文字、\
几张关键帧截图，以及视频的基本信息。请据此写出这个视频讲了什么。

要求：

- 用简体中文回答。
- 严格按下面三节输出，不要加别的标题、开场白或结束语。
- OCR 文字可能有错字、缺字、断行，也可能把画面里无关的水印或界面文字混进来。\
结合截图判断哪些是正文、哪些该丢掉；明显是错字的按上下文改过来。
- 只写资料里有的内容。资料不足就在对应小节写「资料不足」，不要推测、不要补充常识。
- 保留原文里的数字、金额、人名、产品名、项目名、协议名和网址，不要换算也不要改写。

## 一句话概要

一句话说清这个视频在讲什么。

## 内容要点

三到六条，每条一行，按视频里出现的顺序写。

## 关键信息

资料里出现的具体数字、名称、链接，每条一行，写成「名称：值」。没有就写「无」。
"""


class SummarizeError(RuntimeError):
    """The summary could not be produced."""


def pick_images(shots: Sequence[Shot], max_images: int = DEFAULT_MAX_IMAGES) -> list[Shot]:
    """Up to `max_images` stills, spread across the clip.

    Spread, not the first N: the opening stills of a clip are the ones most like each
    other, so taking them in order would send four pictures of the title card and none of
    what happened afterwards.
    """
    entries = list(shots)
    if max_images <= 0 or not entries:
        return []
    if len(entries) <= max_images:
        return entries
    step = (len(entries) - 1) / (max_images - 1) if max_images > 1 else 0
    picked = {round(i * step) for i in range(max_images)}
    return [entries[i] for i in sorted(picked)]


def encode_image(
    path: Path,
    *,
    width: int | None = DEFAULT_IMAGE_WIDTH,
    run: Runner = run_default,
) -> tuple[str, str]:
    """`(media_type, base64)` for a still, downscaled if it is wider than `width`.

    ffmpeg does the resizing because it is already a hard requirement of this half of the
    tool; pulling in an imaging library to shrink four jpegs would not pay for itself.
    """
    path = Path(path)
    if not path.is_file():
        raise SummarizeError(f"{path} is not a file")
    data = path.read_bytes()
    if width:
        with tempfile.TemporaryDirectory(prefix="wx-dlh-img-") as tmp:
            smaller = Path(tmp) / "small.jpg"
            run_tool(
                FFMPEG,
                [
                    "-hide_banner",
                    "-nostats",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(path),
                    "-vf",
                    f"scale='min({width},iw)':-2",
                    "-q:v",
                    "4",
                    str(smaller),
                ],
                run=run,
            )
            if smaller.is_file():
                data = smaller.read_bytes()
    return "image/jpeg", base64.standard_b64encode(data).decode("ascii")


def describe_clip(info: MediaInfo | None, *, description: str = "", nickname: str = "") -> str:
    """The metadata block. Short on purpose - it is context, not content."""
    lines = []
    if info is not None:
        lines.append(f"- 时长：{info.duration:.1f} 秒")
        if info.has_video:
            shape = "竖屏" if info.is_portrait else "横屏"
            lines.append(f"- 画面：{info.width}x{info.height}（{shape}）")
        lines.append(f"- 声音：{'有' if info.has_audio else '无'}")
    if description:
        lines.append(f"- 视频号里的描述：{description}")
    if nickname:
        lines.append(f"- 作者：{nickname}")
    return "\n".join(lines) or "- （没有额外信息）"


def build_content(
    text: str,
    stills: Sequence[Shot],
    *,
    info: MediaInfo | None = None,
    description: str = "",
    nickname: str = "",
    image_width: int | None = DEFAULT_IMAGE_WIDTH,
    run: Runner = run_default,
) -> list[dict]:
    """The user message: metadata, the transcript, then the stills in time order.

    Images come last and each is introduced by its timestamp, so the model can line a
    picture up with the part of the transcript it belongs to.
    """
    blocks: list[dict] = [
        {
            "type": "text",
            "text": (
                "## 视频基本信息\n\n"
                f"{describe_clip(info, description=description, nickname=nickname)}\n\n"
                "## 画面 OCR 文字\n\n"
                f"{text.strip() or '（没有识别到文字）'}"
            ),
        }
    ]
    if stills:
        blocks.append({"type": "text", "text": f"## 关键帧截图（{len(stills)} 张）"})
    for shot in stills:
        media_type, data = encode_image(shot.path, width=image_width, run=run)
        blocks.append({"type": "text", "text": f"第 {shot.index} 张，{shot.time:.2f} 秒："})
        blocks.append(
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        )
    return blocks


@dataclass(frozen=True)
class Summary:
    """What came back, and what it cost."""

    text: str
    model: str = DEFAULT_MODEL
    images: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Set when the model declined rather than answered, so the caller can say so.
    refusal: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.refusal

    def summary(self) -> str:
        head = (
            f"{self.model}: {len(self.text)} chars from {self.images} still(s), "
            f"{self.input_tokens} in / {self.output_tokens} out"
        )
        return head if self.ok else f"{head}\n  !! {self.refusal or 'the model returned no text'}"


def credentials(env: dict[str, str] | None = None) -> tuple[str, str]:
    """`(header_name, value)` for whichever credential is set.

    An API key goes on `x-api-key`; an OAuth token goes on `Authorization: Bearer` and
    additionally needs a beta flag. Getting these two crossed is a 401 that reads like a
    bad key, so they are resolved in one place and in the SDK's own order.
    """
    source = os.environ if env is None else env
    key = (source.get("ANTHROPIC_API_KEY") or "").strip()
    if key:
        return "x-api-key", key
    token = (source.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    if token:
        return "authorization", f"Bearer {token}"
    raise SummarizeError(NO_CREDENTIALS)


def post_messages(
    body: dict,
    *,
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict:
    """POST to the Messages API and return the parsed response.

    The one network call this module makes. Failures are translated by `_explain`, which
    is where "what do I do about this" lives; this function only classifies by status.
    """
    header, value = credentials(env)
    headers = {
        "content-type": "application/json",
        "anthropic-version": API_VERSION,
        header: value,
    }
    if header == "authorization":
        headers["anthropic-beta"] = OAUTH_BETA

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise SummarizeError(_explain_status(exc)) from exc
    except OSError as exc:
        raise SummarizeError(f"could not reach {API_URL}: {exc}") from exc

    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise SummarizeError(f"the API returned something that is not json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SummarizeError(f"expected a json object, got {type(parsed).__name__}")
    return parsed


def summarize(
    text: str,
    shots: ShotSet | Sequence[Shot] = (),
    *,
    info: MediaInfo | None = None,
    description: str = "",
    nickname: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str = DEFAULT_EFFORT,
    max_images: int = DEFAULT_MAX_IMAGES,
    image_width: int | None = DEFAULT_IMAGE_WIDTH,
    system: str = SYSTEM_PROMPT,
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    #: Resolved at call time rather than bound as a default, so replacing
    #: `wx_dlh.summarize.post_messages` works - which is how a caller that does not
    #: thread this parameter through, the CLI included, gets tested without a network.
    post: Callable[..., dict] | None = None,
    run: Runner = run_default,
) -> Summary:
    """One model call: transcript plus stills in, a written summary out."""
    post = post or post_messages
    entries = shots.shots if isinstance(shots, ShotSet) else tuple(shots)
    if not text.strip() and not entries:
        raise SummarizeError("nothing to summarize: no text was extracted and no stills exist")

    stills = pick_images(entries, max_images)
    content = build_content(
        text,
        stills,
        info=info,
        description=description,
        nickname=nickname,
        image_width=image_width,
        run=run,
    )
    body = build_body(content, model=model, max_tokens=max_tokens, effort=effort, system=system)
    return read_response(post(body, env=env, timeout=timeout), model=model, images=len(stills))


def build_body(
    content: Sequence[dict],
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str = DEFAULT_EFFORT,
    system: str = SYSTEM_PROMPT,
) -> dict:
    """The request body. Kept separate so `--dry-run` can show it without sending it."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        # `thinking` is deliberately absent: it is adaptive by default on this model
        # family, and effort is the knob that matters for a job this size.
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": list(content)}],
    }


def read_response(payload: dict, *, model: str = DEFAULT_MODEL, images: int = 0) -> Summary:
    """Pull the text, the usage and any refusal out of a Messages API response.

    `stop_reason` is checked before the content is trusted: a refusal comes back as a
    perfectly ordinary HTTP 200 with no usable text in it.
    """
    refusal = ""
    if payload.get("stop_reason") == "refusal":
        details = payload.get("stop_details") or {}
        reason = details.get("explanation") or details.get("category") or ""
        refusal = f"the model declined to summarize this clip{f': {reason}' if reason else ''}"

    blocks = payload.get("content")
    parts = [
        str(block.get("text") or "")
        for block in (blocks if isinstance(blocks, list) else [])
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]
    usage = payload.get("usage") or {}

    def count(name: str) -> int:
        try:
            return int(usage.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    return Summary(
        text="\n".join(parts).strip(),
        model=str(payload.get("model") or model),
        images=images,
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        refusal=refusal,
    )


def _explain_status(exc: urllib.error.HTTPError) -> str:
    """An HTTP failure, translated into what to do about it.

    The API puts a reason in the body; it is quoted because "invalid_request_error: max
    tokens ..." is worth more than the status line alone.
    """
    try:
        detail = (json.loads(exc.read() or b"{}").get("error") or {}).get("message") or ""
    except Exception:  # the body is not always json, and this is the error path
        detail = ""
    hints = {
        400: "the API rejected the request",
        401: f"the credentials were rejected.\n{NO_CREDENTIALS}",
        403: "those credentials are not allowed to call this model",
        404: "no such model - check --model",
        413: "the request is too large; lower --max-images or --image-width",
        429: "rate limited; wait and run it again",
    }
    head = hints.get(exc.code) or (
        f"the API returned {exc.code}; this is usually transient, run it again"
        if exc.code >= 500
        else f"the API returned {exc.code} {exc.reason}"
    )
    return f"{head}\n  HTTP {exc.code}{f': {detail}' if detail else ''}"


def render(summary: Summary, *, video: Path | None = None, source: str = "") -> str:
    """The `summary.md` body: the model's text, plus a footer saying how it was made."""
    lines = [summary.text.strip() or "（模型没有返回内容）", "", "---", ""]
    if video is not None:
        lines.append(f"- 视频：`{video}`")
    if source:
        lines.append(f"- 文字来源：{source}")
    lines.append(f"- 模型：{summary.model}，截图 {summary.images} 张")
    if summary.refusal:
        lines.append(f"- 注意：{summary.refusal}")
    return "\n".join(lines) + "\n"


def write_summary(
    summary: Summary, out_dir: Path, *, video: Path | None = None, source: str = ""
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SUMMARY_NAME
    path.write_text(render(summary, video=video, source=source), encoding="utf-8")
    return path
