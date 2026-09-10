"""Smoke tests for the CLI skeleton - they keep the package importable and runnable."""

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


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["nope"])
