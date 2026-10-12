"""CA file naming, creation, fingerprints, and the move from the pre-rename directory."""

import base64
import ssl

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


def test_fingerprints_match_what_openssl_and_the_stores_show(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    pem = ca.ensure_ca(tmp_path / "ca")
    loaded = x509.load_pem_x509_certificate(pem.read_bytes())
    sha256 = loaded.fingerprint(hashes.SHA256()).hex().upper()
    assert ca.fingerprint(pem) == ":".join(sha256[i : i + 2] for i in range(0, 64, 2))
    assert ca.fingerprint(pem, "sha1", sep="") == loaded.fingerprint(hashes.SHA1()).hex().upper()


def test_no_certificate_means_no_fingerprint(tmp_path):
    assert ca.fingerprint(tmp_path / "missing.pem") == ""
    (tmp_path / "junk.pem").write_text("not a certificate")
    assert ca.fingerprint(tmp_path / "junk.pem") == ""


def _block(der: bytes) -> str:
    """One PEM certificate block around exactly these bytes."""
    return (
        "-----BEGIN CERTIFICATE-----\n"
        f"{base64.encodebytes(der).decode()}"
        "-----END CERTIFICATE-----\n"
    )


def test_a_length_not_written_the_way_der_requires_is_refused(tmp_path):
    """OpenSSL reads a padded length happily, but it re-encodes the certificate before it
    fingerprints it, while `digest` hashes the bytes as they came. Taking one in would mean
    printing a fingerprint `openssl x509 -fingerprint` does not print and no store will be
    found by. `scripts/trust-ca.sh` refuses the same file, by comparing openssl's
    re-encoding with the block byte for byte."""
    pytest.importorskip("mitmproxy")
    der = ssl.PEM_cert_to_DER_cert(ca.ensure_ca(tmp_path / "ca").read_text())
    count = der[1] & 0x7F  # a certificate's length is always in the long form
    padded = der[:1] + bytes([0x80 | (count + 1)]) + b"\x00" + der[2:]
    with pytest.raises(ValueError, match="DER requires"):
        ca.cert_der(_block(padded))
    rewritten = tmp_path / "padded.pem"
    rewritten.write_text(_block(padded))
    assert ca.fingerprint(rewritten) == ""


def test_the_short_form_is_the_only_one_where_it_fits():
    """The other half of the same rule, and the check is the only place it can be shown:
    no X.509 certificate is under 128 bytes long, so nothing this short ever reaches it
    through `cert_der` - the parse comes first, and refuses it as no certificate at all."""
    ca._check_one_der_value(b"\x30\x05" + b"\x00" * 5)  # the short form, as DER wants it
    for spelling in (b"\x30\x81\x05", b"\x30\x82\x00\x05"):  # long form, padded long form
        with pytest.raises(ValueError, match="DER requires"):
            ca._check_one_der_value(spelling + b"\x00" * 5)


def test_what_sits_outside_the_block_is_not_part_of_the_certificate(tmp_path):
    """A trust store is handed the file, not the certificate in it, so bytes after the
    block ride along unread - a DER private key among them, which the text search for a
    `BEGIN ... PRIVATE KEY` line cannot see."""
    pytest.importorskip("mitmproxy")
    pem = ca.ensure_ca(tmp_path / "ca").read_text()
    with pytest.raises(ValueError):
        ca.cert_der(pem + "\x30\x82\x01\x00")


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
