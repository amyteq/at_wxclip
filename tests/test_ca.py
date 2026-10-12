"""CA file naming and trust-store detection."""

import pytest

from wx_dlh import ca, platforms


def test_file_names_are_the_ones_mitmproxy_looks_for(tmp_path):
    assert ca.cert_path(tmp_path).name == "mitmproxy-ca-cert.pem"
    assert ca.key_path(tmp_path).name == "mitmproxy-ca.pem"


def test_ensure_ca_creates_a_usable_pair_and_is_idempotent(tmp_path):
    pytest.importorskip("mitmproxy")
    cert = ca.ensure_ca(tmp_path / "ca")
    assert cert.is_file()
    assert ca.key_path(tmp_path / "ca").is_file()
    first = cert.read_bytes()
    assert ca.ensure_ca(tmp_path / "ca").read_bytes() == first
    assert (tmp_path / "ca").stat().st_mode & 0o077 == 0


def test_ensure_ca_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "mitmproxy.certs", None)
    with pytest.raises(ImportError):
        ca.ensure_ca(tmp_path / "ca")


def test_trust_targets_cover_the_three_stores():
    # Pinned to linux rather than left to sys.platform: these three are the stores a
    # *Linux* WeChat reads, and that is what this test is about.
    names = [t.name for t in ca.trust_targets(platform="linux")]
    assert names == ["flatpak-nss", "user-nss", "system"]


@pytest.mark.parametrize("platform", ["darwin", "win32", "freebsd13"])
def test_trust_targets_are_empty_off_linux(platform):
    """Reporting the Linux stores as merely `available=False` elsewhere would read as
    "install these and you are done" - the wrong answer in the shape of a right one."""
    assert ca.trust_targets(platform=platform) == []


def test_trust_hint_points_at_the_cert_and_the_script(tmp_path):
    hint = ca.trust_hint(tmp_path, platform="linux")
    assert str(ca.cert_path(tmp_path)) in hint
    assert "scripts/trust-ca.sh" in hint


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_trust_hint_off_linux_still_names_the_certificate(tmp_path, platform):
    """The CA file is the same everywhere - mitmproxy makes it on any platform - so the
    paths stay useful even where we cannot say what to do with them."""
    hint = ca.trust_hint(tmp_path, platform=platform)
    assert str(ca.cert_path(tmp_path)) in hint
    assert str(ca.key_path(tmp_path)) in hint


@pytest.mark.parametrize("platform", ["darwin", "win32", "freebsd13"])
def test_trust_hint_off_linux_sends_people_to_the_playbook_not_to_a_linux_script(
    tmp_path, platform
):
    hint = ca.trust_hint(tmp_path, platform=platform)
    assert "scripts/trust-ca.sh" not in hint
    assert "flatpak" not in hint.lower()
    assert platforms.PLAYBOOK in hint
    assert platforms.ISSUE in hint


def test_trust_hint_defaults_to_this_machine(tmp_path):
    assert ca.trust_hint(tmp_path) == ca.trust_hint(tmp_path, platform=platforms.current())


def test_flatpak_nssdb_is_inside_the_sandbox_home():
    assert ca.flatpak_nssdb("com.example.App").parts[-5:] == (
        ".var",
        "app",
        "com.example.App",
        ".pki",
        "nssdb",
    )


def _make_ca(ca_dir, body="pem"):
    ca_dir.mkdir(parents=True)
    ca.cert_path(ca_dir).write_text(body)
    ca.key_path(ca_dir).write_text(body + "+key")


def test_a_ca_made_before_the_rename_moves_over_unchanged(tmp_path):
    legacy, new = tmp_path / "wxclip" / "ca", tmp_path / "wx-dlh" / "ca"
    _make_ca(legacy, "old")
    assert ca.adopt_legacy_ca(new) == legacy
    assert ca.cert_path(new).read_text() == "old"
    assert ca.key_path(new).read_text() == "old+key"
    # an older checkout still finds the same pair rather than minting a new one
    assert legacy.is_symlink()
    assert ca.cert_path(legacy).read_text() == "old"
    assert ca.adopt_legacy_ca(new) is None


def test_an_empty_new_directory_does_not_block_the_move(tmp_path):
    _make_ca(tmp_path / "wxclip" / "ca", "old")
    (tmp_path / "wx-dlh" / "ca").mkdir(parents=True)
    assert ca.adopt_legacy_ca(tmp_path / "wx-dlh" / "ca") is not None
    assert ca.cert_path(tmp_path / "wx-dlh" / "ca").read_text() == "old"


def test_a_ca_already_under_the_new_name_wins(tmp_path):
    _make_ca(tmp_path / "wxclip" / "ca", "old")
    _make_ca(tmp_path / "wx-dlh" / "ca", "new")
    assert ca.adopt_legacy_ca(tmp_path / "wx-dlh" / "ca") is None
    assert ca.cert_path(tmp_path / "wx-dlh" / "ca").read_text() == "new"
    assert ca.cert_path(tmp_path / "wxclip" / "ca").read_text() == "old"


def test_only_the_default_layout_adopts_anything(tmp_path):
    _make_ca(tmp_path / "wxclip" / "ca", "old")
    assert ca.adopt_legacy_ca(tmp_path / "custom") is None
    assert ca.adopt_legacy_ca(tmp_path / "wx-dlh" / "ca") is not None


def test_nothing_to_adopt_is_not_an_error(tmp_path):
    assert ca.adopt_legacy_ca(tmp_path / "wx-dlh" / "ca") is None
    assert not (tmp_path / "wx-dlh").exists()


def test_a_non_empty_new_directory_is_left_alone(tmp_path):
    _make_ca(tmp_path / "wxclip" / "ca", "old")
    (tmp_path / "wx-dlh" / "ca").mkdir(parents=True)
    (tmp_path / "wx-dlh" / "ca" / "unrelated").write_text("x")
    assert ca.adopt_legacy_ca(tmp_path / "wx-dlh" / "ca") is None
    assert ca.cert_path(tmp_path / "wxclip" / "ca").is_file()
