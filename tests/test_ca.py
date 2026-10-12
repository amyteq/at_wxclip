"""CA file naming, creation, fingerprints, and the move from the pre-rename directory."""

import base64
import shutil
import ssl
import subprocess

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


#: The three values a certificate is - tbsCertificate, signatureAlgorithm, signatureValue -
#: with nothing in any of them. No real certificate is this small, which is why these bytes
#: only ever reach `_check_one_der_value` directly: through `cert_der` the parse comes
#: first, and refuses them as no certificate at all.
SHAPE = b"\x30\x00" + b"\x30\x00" + b"\x03\x00"


def test_the_short_form_is_the_only_one_where_it_fits():
    """The other half of the same rule, and the check is the only place it can be shown."""
    ca._check_one_der_value(b"\x30\x06" + SHAPE)  # the short form, as DER wants it
    for spelling in (b"\x30\x81\x06", b"\x30\x82\x00\x06"):  # long form, padded long form
        with pytest.raises(ValueError, match="DER requires"):
            ca._check_one_der_value(spelling + SHAPE)


@pytest.mark.parametrize(
    ("tbs", "alg", "sig", "refused"),
    [
        (b"\x30\x00", b"\x30\x81\x00", b"\x03\x00", True),
        (b"\x30\x00", b"\x30\x03\x06\x81\x00", b"\x03\x00", True),
        (b"\x30\x00", b"\x30\x00", b"\x03\x81\x00", True),
        (b"\x30\x03\x05\x81\x00", b"\x30\x00", b"\x03\x00", False),
    ],
    ids=["signatureAlgorithm", "inside signatureAlgorithm", "signatureValue", "in tbsCertificate"],
)  # fmt: skip
def test_the_lengths_openssl_rewrites_are_the_ones_refused(tbs, alg, sig, refused):
    """A padded length is refused wherever openssl would rewrite it - however deeply nested
    - and taken where openssl would not. It does not rewrite tbsCertificate: it keeps the
    encoding it read there, signature and all, so those bytes reach the fingerprint exactly
    as the file holds them and nothing here disagrees with openssl about them."""
    body = tbs + alg + sig
    der = b"\x30" + bytes([len(body)]) + body
    if not refused:
        ca._check_one_der_value(der)
        return
    with pytest.raises(ValueError, match="DER requires"):
        ca._check_one_der_value(der)


def _length(size: int, pad: int = 0) -> bytes:
    """How DER writes `size`, with `pad` zero bytes in front of it that nobody needs."""
    if size < 0x80 and not pad:
        return bytes([size])
    body = size.to_bytes(max(1, (size.bit_length() + 7) // 8), "big")
    return bytes([0x80 | (len(body) + pad)]) + bytes(pad) + body


def _seq(body: bytes, pad: int = 0) -> bytes:
    """A SEQUENCE around `body`, its length padded by `pad` bytes nobody needs."""
    return b"\x30" + _length(len(body), pad) + body


def _padded(value: bytes) -> bytes:
    """One DER value with a zero byte added to its length: the same value, spelled the one
    way DER does not allow. Every tag a certificate uses is a single byte."""
    content, finish = ca._der_value(value, 0, len(value))
    return value[:1] + _length(finish - content, 1) + value[content:finish]


def _openssl(der: bytes, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["openssl", "x509", "-inform", "DER", *args], input=der, capture_output=True
    )


@pytest.mark.skipif(shutil.which("openssl") is None, reason="the comparison is with openssl")
@pytest.mark.parametrize(
    "where", ["outer", "tbsCertificate", "signatureAlgorithm", "signatureValue"]
)  # fmt: skip
def test_a_padded_length_is_taken_exactly_where_openssl_keeps_it(tmp_path, where):
    """The rule is openssl's, and this is the comparison that says so: a certificate is
    taken exactly when `openssl x509 -outform DER` writes the same bytes back, and then
    `digest` prints the fingerprint openssl prints. `scripts/trust-ca.sh` makes that very
    comparison, so the port's two halves accept and fingerprint the same files. Which
    position lands on which side is openssl's business, not this test's - it only asks
    that the two agree."""
    pytest.importorskip("mitmproxy")
    der = ssl.PEM_cert_to_DER_cert(ca.ensure_ca(tmp_path / "ca").read_text())
    content, end = ca._der_value(der, 0, len(der))
    parts, at = [], content
    while at < end:  # tbsCertificate, signatureAlgorithm, signatureValue
        _, finish = ca._der_value(der, at, end)
        parts.append(der[at:finish])
        at = finish
    tbs, alg, sig = parts
    if where == "outer":
        variant = _seq(tbs + alg + sig, 1)
    elif where == "tbsCertificate":  # on the first value inside it, under the kept encoding
        inside, stop = ca._der_value(tbs, 0, len(tbs))
        _, first_end = ca._der_value(tbs, inside, stop)
        variant = _seq(_seq(_padded(tbs[inside:first_end]) + tbs[first_end:stop]) + alg + sig)
    elif where == "signatureAlgorithm":
        variant = _seq(tbs + _padded(alg) + sig)
    else:
        variant = _seq(tbs + alg + _padded(sig))

    written = _openssl(variant, "-outform", "DER")
    if written.returncode == 0 and written.stdout == variant:  # openssl keeps these bytes
        printed = _openssl(variant, "-noout", "-fingerprint", "-sha256").stdout.decode()
        assert ca.digest(ca.cert_der(_block(variant))) == printed.strip().partition("=")[2]
    else:  # openssl would fingerprint bytes the file does not hold
        with pytest.raises(ValueError):
            ca.cert_der(_block(variant))


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
