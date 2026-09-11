"""CLI surface: what is wired up, what is still a placeholder."""

import json
from pathlib import Path

import pytest

from wxclip import __version__
from wxclip.__main__ import PLANNED, build_parser, main
from wxclip.keystream import HEAD_BYTES, keystream


def test_version_is_set():
    assert __version__


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "wxclip" in capsys.readouterr().out


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


def test_ca_creates_the_ca_and_explains_the_trust_stores(tmp_path, capsys):
    pytest.importorskip("mitmproxy")
    assert main(["ca", "--ca-dir", str(tmp_path / "ca"), "--app-id", "com.example.App"]) == 0
    out = capsys.readouterr().out
    assert "mitmproxy-ca-cert.pem" in out
    assert "com.example.App" in out
    assert (tmp_path / "ca" / "mitmproxy-ca-cert.pem").is_file()


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
    assert plan["endpoints"]["capture"] == "/__wxclip/capture"
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
    assert plan["endpoints"]["capture"] == "/__wxclip/capture"


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
