"""CLI surface: what is wired up, what is still a placeholder."""

import json

import pytest

from wxclip import __version__
from wxclip.__main__ import PLANNED, build_parser, main


def test_version_is_set():
    assert __version__


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "wxclip" in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(PLANNED))
def test_planned_commands_are_accepted_but_unimplemented(command, capsys):
    assert main([command]) == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_proxy_is_no_longer_planned():
    assert "proxy" not in PLANNED
    assert "ca" not in PLANNED


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
