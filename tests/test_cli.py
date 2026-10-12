"""CLI surface: what is wired up, what is still a placeholder."""

import json
from pathlib import Path

import pytest

from wx_dlh import __version__, platforms
from wx_dlh.__main__ import PLANNED, _warn_unverified_platform, build_parser, main
from wx_dlh.keystream import HEAD_BYTES, keystream


def test_version_is_set():
    assert __version__


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "wx-dlh" in capsys.readouterr().out


def test_nothing_is_a_placeholder_any_more():
    """M2 emptied PLANNED - `serve` was the last entry in it. A later milestone's
    commands go back in there, and the help marks them `(planned)` again."""
    assert PLANNED == {}


@pytest.mark.parametrize("command", ["proxy", "ca", "download", "decrypt", "capture", "serve"])
def test_every_command_is_wired_up(command, capsys):
    assert command not in PLANNED
    assert main([]) == 0
    assert command in capsys.readouterr().out


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["nope"])


def test_an_unverified_platform_is_announced_before_the_proxy_listens(monkeypatch, capsys):
    """On Windows or macOS the proxy starts fine and captures nothing, which looks exactly
    like "the video has not been played yet". Say which one it is, on stderr, up front."""
    monkeypatch.setattr(platforms, "is_verified", lambda *a, **k: False)
    monkeypatch.setattr(platforms, "current", lambda *a, **k: platforms.MACOS)
    _warn_unverified_platform()
    captured = capsys.readouterr()
    assert "NOT verified" in captured.err
    assert platforms.PLAYBOOK in captured.err
    assert captured.out == ""  # never mixed into the capture log's stream


def test_a_verified_platform_says_nothing(monkeypatch, capsys):
    monkeypatch.setattr(platforms, "is_verified", lambda *a, **k: True)
    _warn_unverified_platform()
    assert capsys.readouterr().err == ""


def test_ca_creates_the_ca_and_explains_the_trust_stores(tmp_path, capsys):
    pytest.importorskip("mitmproxy")
    assert main(["ca", "--ca-dir", str(tmp_path / "ca"), "--app-id", "com.example.App"]) == 0
    out = capsys.readouterr().out
    assert "mitmproxy-ca-cert.pem" in out
    assert "com.example.App" in out
    assert (tmp_path / "ca" / "mitmproxy-ca-cert.pem").is_file()


def test_ca_keeps_using_the_ca_made_before_the_rename(tmp_path, capsys):
    pytest.importorskip("mitmproxy")
    from wx_dlh import ca

    before = ca.ensure_ca(tmp_path / "wxclip" / "ca").read_bytes()
    assert main(["ca", "--ca-dir", str(tmp_path / "wx-dlh" / "ca")]) == 0
    assert "moved the existing CA" in capsys.readouterr().err
    assert ca.cert_path(tmp_path / "wx-dlh" / "ca").read_bytes() == before


def test_proxy_dry_run_reports_the_plan_without_listening(tmp_path, capsys):
    pytest.importorskip("mitmproxy")
    code = main(
        [
            "proxy",
            "--dry-run",
            "--port",
            "18899",
            "--ca-dir",
            str(tmp_path / "ca"),
            "--capture-dir",
            str(tmp_path / "captures"),
            "--page-host",
            "channels.weixin.qq.com",
        ]
    )
    assert code == 0
    plan = json.loads(capsys.readouterr().out.split("\nCA certificate:")[0])
    assert plan["proxy"].endswith(":18899")
    assert plan["page_hosts"] == ["channels.weixin.qq.com"]
    assert plan["endpoints"]["capture"] == "/__wx-dlh/capture"
    assert plan["script_bytes"] > 1000
    assert plan["mitmproxy_options"]["connection_strategy"] == "lazy"


# -- download / decrypt / capture --------------------------------------------------------

KEY = "1234567890"
MP4_HEAD = b"\x00\x00\x00\x20ftypisom"


def encrypted(plain: bytes) -> bytes:
    stream = keystream(KEY)
    return bytes(b ^ (stream[i] if i < HEAD_BYTES else 0) for i, b in enumerate(plain))


def test_decrypt_writes_a_playable_copy_next_to_the_input(tmp_path, capsys):
    src = tmp_path / "clip.bin"
    src.write_bytes(encrypted(MP4_HEAD + b"rest of it"))
    assert main(["decrypt", str(src), "--key", KEY]) == 0
    out = tmp_path / "clip-decrypted.bin"
    assert out.read_bytes().startswith(MP4_HEAD)
    assert "mp4" in capsys.readouterr().out


def test_decrypt_in_place_rewrites_the_file_itself(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(encrypted(MP4_HEAD + b"rest of it"))
    assert main(["decrypt", str(src), "--key", KEY, "--in-place"]) == 0
    assert src.read_bytes().startswith(MP4_HEAD)


def test_decrypt_exits_nonzero_when_the_head_is_not_an_mp4(tmp_path, capsys):
    src = tmp_path / "clip.bin"
    src.write_bytes(encrypted(MP4_HEAD + b"rest of it"))
    assert main(["decrypt", str(src), "--key", "999"]) == 1
    assert "wrong decode_key" in capsys.readouterr().out


def test_decrypt_reports_a_key_that_went_through_a_float(tmp_path, capsys):
    src = tmp_path / "clip.bin"
    src.write_bytes(b"whatever")
    assert main(["decrypt", str(src), "--key", "1.38e+19"]) == 1
    assert "decimal digits" in capsys.readouterr().err


def test_decrypt_refuses_two_conflicting_destinations(tmp_path, capsys):
    src = tmp_path / "clip.bin"
    src.write_bytes(b"whatever")
    assert main(["decrypt", str(src), "-k", KEY, "--in-place", "-o", str(tmp_path / "x")]) == 1
    assert "different things" in capsys.readouterr().err


def test_decrypt_says_so_when_the_file_is_missing(tmp_path, capsys):
    assert main(["decrypt", str(tmp_path / "nope.mp4"), "-k", KEY]) == 1
    assert "not a file" in capsys.readouterr().err


def write_capture_log(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "captures-20260911-120000.jsonl"
    log.write_text(
        json.dumps(
            {
                "kind": "capture",
                "media": [
                    {
                        "url": "https://finder.video.qq.com/251/clip0",
                        "url_token": "?token=0",
                        "decode_key": KEY,
                        "file_size": 4_000_000,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return log


def test_download_list_shows_what_was_captured(tmp_path, capsys):
    captures = tmp_path / "captures"
    write_capture_log(captures)
    assert main(["download", "--list", "--capture-dir", str(captures)]) == 0
    out = capsys.readouterr().out
    assert "[0]" in out and KEY in out and "3.8 MiB" in out


def test_download_needs_something_to_download(capsys):
    assert main(["download"]) == 1
    assert "nothing to download" in capsys.readouterr().err


def test_download_will_not_take_a_url_and_a_capture_at_once(tmp_path, capsys):
    assert main(["download", "https://x/y", "--from-capture"]) == 1
    assert "not both" in capsys.readouterr().err


def test_download_without_a_key_refuses_to_produce_an_unplayable_file(capsys):
    assert main(["download", "https://finder.video.qq.com/251/clip0"]) == 1
    assert "--no-decrypt" in capsys.readouterr().err


def test_download_says_where_it_looked_for_captures(tmp_path, capsys):
    assert main(["download", "--from-capture", "--capture-dir", str(tmp_path / "none")]) == 1
    assert "no captures-*.jsonl" in capsys.readouterr().err


def test_download_rejects_an_index_the_log_does_not_have(tmp_path, capsys):
    captures = tmp_path / "captures"
    write_capture_log(captures)
    assert main(["download", "--from-capture", "--index", "7", "--capture-dir", str(captures)]) == 1
    assert "holds 1 entries" in capsys.readouterr().err


def test_capture_dry_run_reports_the_plan_without_listening(tmp_path, capsys):
    pytest.importorskip("mitmproxy")
    code = main(
        [
            "capture",
            "--dry-run",
            "--port",
            "18898",
            "--ca-dir",
            str(tmp_path / "ca"),
            "--capture-dir",
            str(tmp_path / "captures"),
            "--dir",
            str(tmp_path / "downloads"),
            "--max-size",
            "200m",
        ]
    )
    assert code == 0
    plan = json.loads(capsys.readouterr().out.split("\nCA certificate:")[0])
    assert plan["proxy"].endswith(":18898")
    assert plan["endpoints"]["capture"] == "/__wx-dlh/capture"


def test_capture_rejects_a_max_size_that_is_not_a_size(tmp_path, capsys):
    assert main(["capture", "--max-size", "loads", "--ca-dir", str(tmp_path / "ca")]) == 1
    assert "not a size" in capsys.readouterr().err


# -- serve -------------------------------------------------------------------------------


def test_serve_keeps_the_panel_and_the_proxy_on_separate_ports():
    """The panel only reports the proxy's status; they are two processes."""
    args = build_parser().parse_args(["serve", "--port", "18080", "--proxy-port", "18899"])
    assert (args.port, args.proxy_port) == (18080, 18899)


def test_serve_reports_a_port_it_cannot_have(capsys):
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        assert main(["serve", "--host", "127.0.0.1", "--port", str(port)]) == 1
    assert "cannot listen" in capsys.readouterr().err


# -- M3: post-processing ------------------------------------------------------------------


@pytest.mark.parametrize("command", ["shots", "extract", "summarize", "process"])
def test_m3_commands_are_wired_up(command):
    assert command not in PLANNED
    assert build_parser().parse_args([command, "x"]).command == command


def test_extract_check_reports_the_ocr_setup(monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        "wx_dlh.extract.run_tool", lambda *a, **kw: "List of languages (2):\nchi_sim\neng\n"
    )
    assert main(["extract", "--check"]) == 0
    assert "is ready" in capsys.readouterr().out


def test_extract_without_a_target_says_what_it_wants(capsys):
    assert main(["extract"]) == 1
    assert "give a video or a directory" in capsys.readouterr().err


def test_summarize_without_a_target_says_what_it_wants(capsys):
    assert main(["summarize"]) == 1
    assert "give a video or a directory" in capsys.readouterr().err


def test_a_target_that_does_not_exist_is_reported(tmp_path, capsys):
    assert main(["extract", str(tmp_path / "nope.mp4")]) == 1
    assert "neither a file nor a directory" in capsys.readouterr().err


def test_a_directory_without_a_manifest_is_reported(tmp_path, capsys):
    assert main(["extract", str(tmp_path)]) == 1
    assert "no shots.json" in capsys.readouterr().err


def test_summarize_can_take_a_text_file_on_its_own(tmp_path, monkeypatch, capsys):
    """For working on the prompt without a video anywhere near it."""
    transcript = tmp_path / "text.txt"
    transcript.write_text("价值 15999 元视觉效果\n", encoding="utf-8")
    monkeypatch.setattr(
        "wx_dlh.summarize.post_messages",
        lambda body, **kw: {
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": "## 一句话概要\n\n讲了一个开源项目。"}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
    )
    assert main(["summarize", "--text", str(transcript)]) == 0
    assert (tmp_path / "summary.md").is_file()
    assert "claude-opus-5" in capsys.readouterr().out


def test_summarize_dry_run_prints_the_request_and_calls_nothing(tmp_path, monkeypatch, capsys):
    transcript = tmp_path / "text.txt"
    transcript.write_text("价值 15999 元视觉效果\n", encoding="utf-8")

    def forbidden(*a, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("--dry-run must not call the API")

    monkeypatch.setattr("wx_dlh.summarize.post_messages", forbidden)
    assert main(["summarize", "--text", str(transcript), "--dry-run"]) == 0

    body = json.loads(capsys.readouterr().out)
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["effort"] == "medium"
    assert "价值 15999 元视觉效果" in body["messages"][0]["content"][0]["text"]
    assert not (tmp_path / "summary.md").exists()


def test_summarize_without_credentials_says_what_to_set(tmp_path, monkeypatch, capsys):
    transcript = tmp_path / "text.txt"
    transcript.write_text("有文字\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # so a real .env cannot leak in
    assert main(["summarize", "--text", str(transcript)]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_the_dry_run_redacts_the_image_blobs():
    """Four base64 jpegs would bury the prompt that --dry-run exists to show."""
    from wx_dlh.__main__ import _redact

    body = {
        "messages": [
            {
                "content": [
                    {"type": "text", "text": "keep me"},
                    {"type": "image", "source": {"type": "base64", "data": "A" * 5000}},
                ]
            }
        ]
    }
    shown = _redact(body)
    assert shown["messages"][0]["content"][0]["text"] == "keep me"
    assert shown["messages"][0]["content"][1]["source"]["data"] == "<5000 base64 chars>"
    assert body["messages"][0]["content"][1]["source"]["data"] == "A" * 5000  # not mutated


def test_shot_width_zero_means_native_size():
    from wx_dlh.__main__ import _shot_kwargs

    args = build_parser().parse_args(["shots", "v.mp4", "--shot-width", "0"])
    assert _shot_kwargs(args)["width"] is None


def test_summarize_writes_where_out_says(tmp_path, monkeypatch):
    """Without --out the summary lands beside the text file, which would drop output into
    the read-only fixtures in evals/summarize/."""
    transcript = tmp_path / "text.txt"
    transcript.write_text("价值 15999 元视觉效果\n", encoding="utf-8")
    monkeypatch.setattr(
        "wx_dlh.summarize.post_messages",
        lambda body, **kw: {
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": "## 一句话概要\n\n讲了一个开源项目。"}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
    )
    out = tmp_path / "runs"
    assert main(["summarize", "--text", str(transcript), "--out", str(out)]) == 0
    assert (out / "summary.md").is_file()
    assert not (tmp_path / "summary.md").exists()
