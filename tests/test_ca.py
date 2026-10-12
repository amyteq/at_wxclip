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


@pytest.fixture(scope="module")
def der(tmp_path_factory) -> bytes:
    """One real CA, taken apart and put back together differently by the tests below."""
    pytest.importorskip("mitmproxy")
    return ssl.PEM_cert_to_DER_cert(ca.ensure_ca(tmp_path_factory.mktemp("ca")).read_text())


def _length(size: int, pad: int = 0) -> bytes:
    """How DER writes `size`, with `pad` zero bytes in front of it that nobody needs."""
    if size < 0x80 and not pad:
        return bytes([size])
    body = size.to_bytes(max(1, (size.bit_length() + 7) // 8), "big")
    return bytes([0x80 | (len(body) + pad)]) + bytes(pad) + body


def _seq(body: bytes) -> bytes:
    """A SEQUENCE around `body`."""
    return b"\x30" + _length(len(body)) + body


def _header(value: bytes) -> tuple[int, int]:
    """Where `value` keeps its content, and where that content ends. Only the definite
    lengths a certificate openssl wrote is made of: what the tests build out of them is
    another matter, and never read back through here."""
    at = 1
    size = value[at]
    at += 1
    if size & 0x80:
        count = size & 0x7F
        size = int.from_bytes(value[at : at + count], "big")
        at += count
    return at, at + size


def _inside(value: bytes) -> list[bytes]:
    """The values a constructed `value` is made of, each with its own header."""
    at, end = _header(value)
    out = []
    while at < end:
        _, finish = _header(value[at:])
        out.append(value[at : at + finish])
        at += finish
    return out


def _respell(value: bytes, how: str) -> bytes:
    """The same value written a way DER does not allow: a zero byte of padding in front of
    the length; the indefinite form, which counts nothing and is closed by an
    end-of-contents marker instead; or the tag number moved into the high-tag-number form,
    where it is spelled out in continuation octets - with one more of those than it needs,
    for "high tag padded". Every tag a certificate uses fits in the single byte DER wants,
    and every length it uses has one shortest form."""
    content, finish = _header(value)
    body, tag = value[content:finish], value[0]
    if how == "padded":
        return value[:1] + _length(len(body), 1) + body
    if how == "indefinite":  # only a constructed value can be closed by a marker
        return value[:1] + b"\x80" + body + b"\x00\x00"
    lead = bytes([(tag & 0xE0) | 0x1F])  # class and constructed bit kept, tag number moved out
    number = b"\x80" + bytes([tag & 0x1F]) if how == "high tag padded" else bytes([tag & 0x1F])
    return lead + number + _length(len(body)) + body


def _variant(der: bytes, where: str, how: str) -> bytes:
    """The same certificate with the one value at `where` respelled."""
    tbs, alg, sig = _inside(der)
    if where == "outer":
        return _respell(_seq(tbs + alg + sig), how)
    if where == "tbsCertificate":  # the value openssl hands back exactly as it read it
        return _seq(_respell(tbs, how) + alg + sig)
    if where == "inside tbsCertificate":  # its first value, under that same encoding
        first, *rest = _inside(tbs)
        return _seq(_seq(_respell(first, how) + b"".join(rest)) + alg + sig)
    if where == "signatureAlgorithm":
        return _seq(tbs + _respell(alg, how) + sig)
    if where == "the signature algorithm's OID":
        oid, *rest = _inside(alg)
        return _seq(tbs + _seq(_respell(oid, how) + b"".join(rest)) + sig)
    if where == "the signature algorithm's parameters":
        oid, *rest = _inside(alg)
        return _seq(tbs + _seq(oid + b"".join(_respell(p, how) for p in rest)) + sig)
    return _seq(tbs + alg + _respell(sig, how))


def _openssl(der: bytes, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["openssl", "x509", "-inform", "DER", *args], input=der, capture_output=True
    )


needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="the comparison is with openssl"
)


@needs_openssl
@pytest.mark.parametrize("how", ["padded", "indefinite", "high tag", "high tag padded"])
@pytest.mark.parametrize(
    "where",
    [
        "outer",
        "tbsCertificate",
        "inside tbsCertificate",
        "signatureAlgorithm",
        "the signature algorithm's OID",
        "the signature algorithm's parameters",
        "signatureValue",
    ],
)
def test_a_certificate_is_taken_exactly_when_openssl_writes_the_same_bytes_back(der, where, how):
    """The rule is openssl's, and this is the comparison that says so: a certificate is
    taken exactly when `openssl x509 -outform DER` writes the same bytes back, and then
    `digest` prints the fingerprint openssl prints. `scripts/trust-ca.sh` makes that very
    comparison with the CLI and `wx_dlh.ca` makes it through Python's `ssl`, so the port's
    two halves accept and fingerprint the same files. Which position and which spelling land
    on which side is openssl's business, not this test's - it only asks that the two agree,
    and they do even where openssl surprises: it keeps whatever `tbsCertificate` was written
    as, the indefinite form included, and it keeps a respelling buried in a parameter whose
    type it stores whole. That is why neither half holds a rule of its own about DER."""
    variant = _variant(der, where, how)
    written = _openssl(variant, "-outform", "DER")
    if written.returncode == 0 and written.stdout == variant:  # openssl keeps these bytes
        printed = _openssl(variant, "-noout", "-fingerprint", "-sha256").stdout.decode()
        assert ca.digest(ca.cert_der(_block(variant))) == printed.strip().partition("=")[2]
    else:  # openssl would fingerprint bytes the file does not hold
        with pytest.raises(ValueError):
            ca.cert_der(_block(variant))


def test_a_length_not_written_the_way_der_requires_is_refused(tmp_path, der):
    """One of those cases spelled out, and without needing openssl to say so: openssl reads
    a padded outer length happily, but it re-encodes the certificate before it fingerprints
    it, while `digest` hashes the bytes as they came. Taking one in would mean printing a
    fingerprint `openssl x509 -fingerprint` does not print and no store will be found by."""
    padded = _respell(der, "padded")
    with pytest.raises(ValueError, match="openssl writes it back"):
        ca.cert_der(_block(padded))
    rewritten = tmp_path / "padded.pem"
    rewritten.write_text(_block(padded))
    assert ca.fingerprint(rewritten) == ""


def test_what_openssl_keeps_is_kept_here_too(der):
    """The other half of the same rule, and the one that says the check is openssl's
    re-encoding rather than a rule of our own about DER: openssl hands `tbsCertificate` back
    exactly as it read it - the signature is over those very bytes - so a padded length in
    there is in the file, in openssl's fingerprint and in ours alike, and refusing it would
    turn away a certificate `openssl x509 -in F -out F` cannot rewrite."""
    variant = _variant(der, "tbsCertificate", "padded")
    assert ca.cert_der(_block(variant)) == variant
    assert ca.digest(variant) != ca.digest(der)  # a different file, and a different CA to a store


def test_a_certificate_is_taken_where_the_der_reader_takes_nothing(tmp_path, der, monkeypatch):
    """The reading goes in as PEM, and this is why: `cadata` of `bytes` is read as a stream
    of DER values, and CPython 3.11.0 through 3.11.15 ended that stream only on
    ASN1_R_HEADER_TOO_LONG, while OpenSSL's CVE-2026-34180 releases end it with
    ASN1_R_NOT_ENOUGH_DATA (python/cpython#151504) - so on those pairings, which this
    project supports and its own interpreter is one OpenSSL update from, a clean CA came
    back as "no X.509 certificate" and `ca --install/--list/--remove` refused the CA they
    were pointed at. The host's own library says nothing about that, so the test makes any
    build behave like an affected one: bytes `cadata` raises, and the certificate must still
    be taken, under the fingerprint the stores are searched by - and the rule must still
    refuse what openssl would rewrite."""
    der_reader = ssl.SSLContext.load_verify_locations

    def no_der_stream(self, cafile=None, capath=None, cadata=None):
        if isinstance(cadata, bytes | bytearray):
            raise ssl.SSLError(1, "[ASN1: NOT_ENOUGH_DATA] not enough data (_ssl.c:4057)")
        return der_reader(self, cafile, capath, cadata)

    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", no_der_stream)
    assert ca.cert_der(_block(der)) == der
    clean = tmp_path / "mitmproxy-ca-cert.pem"
    clean.write_text(_block(der))
    assert ca.fingerprint(clean) == ca.digest(der)
    with pytest.raises(ValueError, match="openssl writes it back"):
        ca.cert_der(_block(_respell(der, "padded")))


@pytest.fixture(scope="module")
def leaf_pem() -> str:
    """A self-signed certificate that is no CA - `basicConstraints` says CA:FALSE. Built
    with `cryptography` because the test suite may use it; `wx_dlh` may not (AGENTS.md)."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "mitmproxy")])
    now = datetime.datetime.now(datetime.UTC)
    built = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return built.public_bytes(serialization.Encoding.PEM).decode()


def test_a_certificate_that_is_no_ca_is_refused(tmp_path, leaf_pem):
    """Every store here is handed the file as a trust *anchor*, and `--system` files it as
    one for the whole machine, so a certificate that may not sign certificates is refused
    before any store is touched - as unusable there as it is dangerous to trust."""
    with pytest.raises(ValueError, match="not a CA certificate"):
        ca.cert_der(leaf_pem)
    (tmp_path / "leaf.pem").write_text(leaf_pem)
    assert ca.fingerprint(tmp_path / "leaf.pem") == ""


@needs_openssl
def test_whether_it_is_a_ca_is_the_question_the_script_asks_openssl(der, leaf_pem):
    """And the two halves ask it of the same function: `get_ca_certs` hands back only what
    X509_check_ca accepts, and the OCSP helper CA line of `openssl x509 -purpose` - the line
    `scripts/trust-ca.sh` reads - is that function and nothing else."""

    def purpose_says(what: bytes) -> str:
        printed = _openssl(what, "-noout", "-purpose").stdout.decode()
        line = next(ln for ln in printed.splitlines() if ln.startswith("OCSP helper CA"))
        return line.partition(":")[2].strip()

    assert purpose_says(der).startswith("Yes")
    assert ca.cert_der(_block(der)) == der
    assert purpose_says(ssl.PEM_cert_to_DER_cert(leaf_pem)) == "No"
    with pytest.raises(ValueError, match="not a CA certificate"):
        ca.cert_der(leaf_pem)


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
