"""Settings come from the environment, with `.env` as a fallback."""

from pathlib import Path

import pytest

from wxclip.config import Settings, read_dotenv


def test_defaults_match_the_documented_ones():
    settings = Settings.from_env(env={})
    assert (settings.proxy_host, settings.proxy_port) == ("127.0.0.1", 8899)
    assert settings.capture_dir == Path("captures")
    assert settings.ca_dir.parts[-2:] == ("wxclip", "ca")


def test_environment_overrides_everything():
    settings = Settings.from_env(
        env={
            "WXCLIP_PROXY_HOST": "0.0.0.0",
            "WXCLIP_PROXY_PORT": "9999",
            "WXCLIP_CAPTURE_DIR": "/tmp/caps",
            "WXCLIP_CA_DIR": "/tmp/ca",
        }
    )
    assert (settings.proxy_host, settings.proxy_port) == ("0.0.0.0", 9999)
    assert settings.capture_dir == Path("/tmp/caps")
    assert settings.ca_dir == Path("/tmp/ca")


def test_dotenv_fills_gaps_but_the_environment_wins(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("WXCLIP_PROXY_PORT=1234\nWXCLIP_SERVER_PORT=4321\n", encoding="utf-8")
    settings = Settings.from_env(env={"WXCLIP_PROXY_PORT": "5555"}, dotenv=dotenv)
    assert settings.proxy_port == 5555
    assert settings.server_port == 4321


def test_a_port_typo_fails_loudly():
    with pytest.raises(ValueError, match="WXCLIP_PROXY_PORT"):
        Settings.from_env(env={"WXCLIP_PROXY_PORT": "88ninetynine"})


def test_surrounding_whitespace_in_a_port_is_tolerated():
    assert Settings.from_env(env={"WXCLIP_PROXY_PORT": " 8899 "}).proxy_port == 8899


def test_read_dotenv_skips_comments_and_strips_quotes(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text('# comment\n\nA="x"\nB=y\nnonsense\n', encoding="utf-8")
    assert read_dotenv(dotenv) == {"A": "x", "B": "y"}
    assert read_dotenv(tmp_path / "missing") == {}
