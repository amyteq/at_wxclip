"""CA file naming and trust-store detection."""

import pytest

from wx_dlh import ca


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
    names = [t.name for t in ca.trust_targets()]
    assert names == ["flatpak-nss", "user-nss", "system"]


def test_trust_hint_points_at_the_cert_and_the_script(tmp_path):
    hint = ca.trust_hint(tmp_path)
    assert str(ca.cert_path(tmp_path)) in hint
    assert "scripts/trust-ca.sh" in hint


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
