"""The platform boundary: what wx-dlh claims to support where.

Every assertion here is about a platform this suite does not run on, which is the point.
The one thing these tests cannot check is whether the Windows and macOS descriptions are
*true* - only a real client on a real machine can say that, and until it does, `verified`
stays False. See `docs/cross-platform-verification.md` and issue #11.
"""

import pytest

from wx_dlh import platforms


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
def test_trust_points_at_the_script_routing_still_has_none(raw):
    """#23 gave trust-ca.sh the macOS keychain and the Windows certificate store; nothing
    has done the same for routing. Neither changes `verified`."""
    known = platforms.support(raw)
    assert platforms.TRUST_SCRIPT in known.trust
    assert platforms.TRUST_SCRIPT not in known.routing
    assert "never run for this project" in known.routing
    assert known.verified is False
    line = platforms.status_line(raw)
    assert "routing is unimplemented" in line
    assert platforms.TRUST_SCRIPT in line


def test_status_line_for_an_unconsidered_platform_does_not_invent_a_name():
    line = platforms.status_line("freebsd13")
    assert "not considered" in line
    # "unknown" is this module's internal bucket, not something to show a user.
    assert "platform: unknown" not in line


@pytest.mark.parametrize(("raw", "name"), [("darwin", "macOS"), ("win32", "Windows")])
def test_unsupported_note_says_what_it_would_take_and_where_the_work_is(raw, name):
    note = platforms.unsupported_note(raw)
    assert f"not verified on {name}" in note
    assert platforms.TRUST_SCRIPT in note
    assert platforms.PLAYBOOK in note
    assert platforms.ISSUE in note
    assert "routing" in note and "trust" in note


def test_unsupported_note_reads_as_a_sentence_for_an_unconsidered_platform():
    note = platforms.unsupported_note("freebsd13")
    assert "not implemented on this platform" in note
    assert "Nothing is known about" in note
    assert platforms.TRUST_SCRIPT not in note
