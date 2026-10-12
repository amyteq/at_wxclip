"""The platform boundary: what wx-dlh claims to support where.

Every assertion here is about a platform this suite does not run on, which is the point.
The one thing these tests cannot check is whether the Windows and macOS descriptions are
*true* - only a real client on a real machine can say that, and until it does, `verified`
stays False. See `docs/cross-platform-verification.md` and issue #11.
"""

import sys

import pytest

from wx_dlh import platforms, trust


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("linux", platforms.LINUX),
        ("linux2", platforms.LINUX),
        ("darwin", platforms.MACOS),
        ("win32", platforms.WINDOWS),
        ("cygwin", platforms.WINDOWS),
        ("msys", platforms.WINDOWS),
        ("freebsd13", platforms.UNKNOWN),
        ("", platforms.UNKNOWN),
    ],
)
def test_current_normalises_sys_platform(raw, expected):
    assert platforms.current(raw) == expected


@pytest.mark.parametrize("name", [platforms.LINUX, platforms.MACOS, platforms.WINDOWS])
def test_current_is_idempotent(name):
    """A `platform=` argument gets handed down through several callers, so passing an
    already-normalised name has to mean the same thing as passing the raw one."""
    assert platforms.current(platforms.current(name)) == name


def test_current_with_no_argument_answers_for_this_machine():
    assert platforms.current() in (
        platforms.LINUX,
        platforms.MACOS,
        platforms.WINDOWS,
        platforms.UNKNOWN,
    )


@pytest.mark.parametrize("raw", ["linux", "darwin", "win32"])
def test_current_names_the_same_os_as_the_trust_stores(raw):
    """`wx-dlh ca` prints this platform's status immediately above the stores `trust`
    picked for it. Two mappings would be two machines in one output."""
    assert platforms.current(raw) == trust.detect_os(raw, wsl=False)


@pytest.fixture
def wsl(monkeypatch):
    """A Linux shell on a Windows host. Both halves have to be faked: this machine is not
    WSL, and `trust.detect_os`'s default platform was bound to `sys.platform` at import,
    so it cannot be moved by patching `sys` either."""
    monkeypatch.setattr(trust, "is_wsl", lambda *a, **k: True)
    detect_os = trust.detect_os
    monkeypatch.setattr(trust, "detect_os", lambda *a, **k: detect_os("linux"))


def test_wsl_is_the_windows_host_it_routes_to(wsl):
    """`sys.platform` says linux and WeChat is not there - it runs on the host, whose
    routing is unimplemented and whose certificate store nothing has watched work. Calling
    that a verified Linux machine suppresses the one warning this module exists to print."""
    assert platforms.current() == platforms.WINDOWS
    assert platforms.is_verified() is False
    line = platforms.status_line()
    assert "NOT verified" in line
    assert "WSL" in line  # "Windows" from a Linux shell, unexplained, looks like a bug
    assert "not verified on Windows" in platforms.unsupported_note()


def test_an_explicit_platform_is_still_taken_at_face_value_under_wsl(wsl):
    """`current(raw)` stays a pure mapping: the suite has to be able to ask about Linux
    from a machine that is not Linux."""
    assert platforms.current("linux") == platforms.LINUX
    assert platforms.is_verified("linux") is True
    assert "WSL" not in platforms.status_line("win32")


@pytest.mark.parametrize(("raw", "name"), [("darwin", "macOS"), ("win32", "Windows")])
def test_support_normalises_its_argument(raw, name):
    assert platforms.support(raw).name == name
    assert platforms.support(platforms.current(raw)).name == name


def test_support_is_none_for_a_platform_nobody_considered():
    assert platforms.support("freebsd13") is None


def test_only_linux_is_verified():
    """The whole boundary rests on this: Linux was carried end to end in M0, and nothing
    else has been. Flipping one of these to True is the last step of issue #11, after a
    real client on that platform has been driven through the proxy - not before."""
    assert platforms.is_verified("linux") is True
    assert platforms.is_verified("darwin") is False
    assert platforms.is_verified("win32") is False
    assert platforms.is_verified("freebsd13") is False


def test_every_known_platform_says_how_routing_and_trust_would_work():
    for name, known in platforms.SUPPORT.items():
        assert known.platform == name
        assert known.routing.strip()
        assert known.trust.strip()
        assert known.doc.strip()


def test_unverified_platforms_point_at_the_playbook_not_at_the_linux_steps():
    for name in (platforms.MACOS, platforms.WINDOWS):
        assert platforms.SUPPORT[name].doc == platforms.PLAYBOOK
    assert platforms.SUPPORT[platforms.LINUX].doc == "docs/M0-runbook.md"


def test_status_line_marks_the_unverified_platforms_and_names_the_issue():
    assert "NOT verified" in platforms.status_line("darwin")
    assert "#11" in platforms.status_line("win32")
    assert "NOT verified" not in platforms.status_line("linux")


@pytest.mark.parametrize("raw", ["darwin", "win32"])
def test_trust_points_at_the_command_routing_still_has_none(raw):
    """#23 gave trust-ca.sh the macOS keychain and the Windows certificate store and #28
    ported it to `wx-dlh ca --install`; nothing has done the same for routing. Neither
    changes `verified`."""
    known = platforms.support(raw)
    assert platforms.TRUST_COMMAND in known.trust
    assert platforms.TRUST_SCRIPT in known.trust
    assert platforms.TRUST_COMMAND not in known.routing
    assert platforms.TRUST_SCRIPT not in known.routing
    assert "never run for this project" in known.routing
    assert known.verified is False
    line = platforms.status_line(raw)
    assert "routing is unimplemented" in line
    assert platforms.TRUST_COMMAND in line


@pytest.fixture
def host_is_linux(monkeypatch):
    """Pin `sys.platform`, so "the host's own name is absent" asserts the same thing
    wherever the suite runs: the messages below are about a platform that is not this one."""
    monkeypatch.setattr(sys, "platform", "linux")


def test_status_line_for_an_unconsidered_platform_names_the_one_it_was_asked_about(host_is_linux):
    line = platforms.status_line("freebsd13")
    assert "platform: freebsd13" in line
    assert "not considered" in line
    # "unknown" is this module's internal bucket, not something to show a user.
    assert "platform: unknown" not in line
    # Nor is the host: the caller asked about somewhere else, and answering for this
    # machine would be a wrong answer that reads like a working one.
    assert "linux" not in line


@pytest.mark.parametrize(("raw", "name"), [("darwin", "macOS"), ("win32", "Windows")])
def test_unsupported_note_says_what_it_would_take_and_where_the_work_is(raw, name):
    note = platforms.unsupported_note(raw)
    assert f"not verified on {name}" in note
    assert platforms.TRUST_COMMAND in note
    assert platforms.TRUST_SCRIPT in note
    assert platforms.PLAYBOOK in note
    assert platforms.ISSUE in note
    assert "routing" in note and "trust" in note


def test_unsupported_note_reads_as_a_sentence_for_an_unconsidered_platform(host_is_linux):
    note = platforms.unsupported_note("freebsd13")
    assert "not implemented on this platform" in note
    assert "Nothing is known about 'freebsd13'" in note
    assert "linux" not in note  # the platform asked about, never the one underfoot
    assert platforms.TRUST_COMMAND not in note
    assert platforms.TRUST_SCRIPT not in note


def test_with_no_argument_an_unconsidered_host_is_named_by_sys_platform(monkeypatch):
    """The other half of naming the platform asked about: nobody named one, so the question
    was about this machine, and once `detect_os` has refused it `sys.platform` is the only
    name it has left."""

    def unsupported(*a, **k):
        raise trust.TrustError("unsupported platform: freebsd13")

    monkeypatch.setattr(sys, "platform", "freebsd13")
    monkeypatch.setattr(trust, "detect_os", unsupported)
    assert platforms.current() == platforms.UNKNOWN
    assert "platform: freebsd13" in platforms.status_line()
    assert "Nothing is known about 'freebsd13'" in platforms.unsupported_note()
