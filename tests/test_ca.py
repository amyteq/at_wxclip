"""CA file naming and trust-store detection."""

import pytest

from wxclip import ca


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
