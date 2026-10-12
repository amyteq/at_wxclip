"""The proxy's own CA: create it, find it, fingerprint it.

mitmproxy owns the CA material, and we keep its file names (`mitmproxy-ca*`) so it picks
the same files up from `confdir` on the next run - only the directory is ours. The private
key never belongs in the repo: the default location is outside it. Putting the certificate
into the trust stores WeChat reads is `wx_dlh.trust`.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import ssl
from pathlib import Path

#: mitmproxy's naming inside `confdir`. Do not rename: mitmproxy looks for these.
CA_BASENAME = "mitmproxy"
KEY_SIZE = 2048

#: The Flatpak build of WeChat has no access to the host home, so its Chromium runtime
#: reads a NSS database inside the sandbox home instead of `~/.pki/nssdb` (see `trust`).
FLATPAK_APP_ID = "com.tencent.WeChat"


def cert_path(ca_dir: Path) -> Path:
    """The PEM to hand to a trust store (certificate only, no key)."""
    return Path(ca_dir) / f"{CA_BASENAME}-ca-cert.pem"


def key_path(ca_dir: Path) -> Path:
    """Certificate *and* private key - never leaves this machine."""
    return Path(ca_dir) / f"{CA_BASENAME}-ca.pem"


#: The directory name the CA lived under before the project was renamed to wx-dlh (#11).
LEGACY_DIR_NAME = "wxclip"


def adopt_legacy_ca(ca_dir: Path) -> Path | None:
    """Move a CA made before the rename into `ca_dir`; return where it came from.

    A CA in use is already trusted, and on Linux its SPKI is pinned in the webview's
    flatpak override. Generating a fresh one under the new name would break
    interception without a word, so the old pair moves over instead - same files, same
    key. Only `<config>/wx-dlh/ca` adopts its `<config>/wxclip/ca` sibling, and only
    while it holds no certificate of its own.

    The old path is left as a symlink to the new one: a checkout from before the rename
    would otherwise find no CA there and quietly mint a new one, which the pinned SPKI
    no longer matches.
    """
    ca_dir = Path(ca_dir).expanduser()
    if ca_dir.parts[-2:] != ("wx-dlh", "ca") or cert_path(ca_dir).exists():
        return None
    legacy = ca_dir.parent.parent / LEGACY_DIR_NAME / "ca"
    if not cert_path(legacy).is_file():
        return None
    if ca_dir.exists():
        try:
            ca_dir.rmdir()  # an empty directory is safe to replace; anything else is not
        except OSError:
            return None
    ca_dir.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(ca_dir)
    with contextlib.suppress(OSError):  # no symlinks (e.g. unprivileged Windows): just moved
        legacy.symlink_to(ca_dir, target_is_directory=True)
    return legacy


def ensure_ca(ca_dir: Path) -> Path:
    """Create the CA if `ca_dir` has none yet; return the public certificate path."""
    ca_dir = Path(ca_dir).expanduser()
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_dir.chmod(0o700)
    from mitmproxy.certs import CertStore  # heavy, and only needed when a CA is wanted

    CertStore.from_store(ca_dir, CA_BASENAME, KEY_SIZE)
    return cert_path(ca_dir)


#: A `BEGIN ... PRIVATE KEY` line of any flavour: PKCS#8, RSA, EC, ENCRYPTED.
PRIVATE_KEY_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


#: Why a length that is not the shortest one possible is refused rather than read: DER
#: allows each length exactly one spelling, OpenSSL reads the others anyway but re-encodes
#: the certificate before it fingerprints it, and `digest` hashes the bytes as they came.
#: The two would print two fingerprints for one certificate, and a store searched by ours
#: would not find what `openssl x509 -fingerprint` says is in it.
NOT_DER = (
    "a length between the PEM delimiters is not written the way DER requires (the "
    "shortest form); openssl re-encodes such a certificate before it fingerprints it, so "
    "the fingerprints would differ. Re-encode it with: openssl x509 -in FILE -out FILE"
)

#: A value whose length reaches past the bytes there are. `_parse_x509` reads the whole
#: certificate first, so by the time the encoding is looked at only the outer value can
#: still say this - the rest has already been walked by OpenSSL.
CUT_SHORT = "the certificate between the PEM delimiters stops in the middle"


def _der_header(
    der: bytes, start: int, end: int, *, minimal: bool = True
) -> tuple[int, int | None]:
    """Where the value at `start` keeps its content, and how many bytes of it follow -
    None when the length is the indefinite form, which counts nothing and runs to an
    end-of-contents marker instead.

    ValueError when the header reaches past `end`, or - unless `minimal` says not to ask -
    when its length is spelled any way but the shortest, the only one DER allows. The
    indefinite form is one of those other ways, and only a constructed value may carry it:
    a primitive one holds no marker to close it, and openssl parses no such length either.
    """
    if start >= end:
        raise ValueError(CUT_SHORT)
    at = start + 1
    if der[start] & 0x1F == 0x1F:  # a tag number of its own: every byte but the last carries
        while at < end and der[at] & 0x80:
            at += 1
        at += 1
    if at >= end:
        raise ValueError(CUT_SHORT)
    size = der[at]
    at += 1
    if not size & 0x80:  # short form: the byte is the length
        return at, size
    count = size & 0x7F  # long form: the low seven bits count the length's own bytes
    if count == 0:  # indefinite: the marker ends it, and only for a constructed value
        if minimal or not der[start] & 0x20:
            raise ValueError(NOT_DER)
        return at, None
    if count == 0x7F or at + count > end:  # reserved, cut short
        raise ValueError(NOT_DER)
    leading_zero = der[at] == 0  # never the shortest form, whatever follows it
    size = int.from_bytes(der[at : at + count], "big")
    if minimal and (leading_zero or size < 0x80):  # ... nor the long form where short fits
        raise ValueError(NOT_DER)
    return at + count, size


def _der_value(der: bytes, start: int, end: int) -> tuple[int, int]:
    """Where the value at `start` keeps its content, and where that content ends.

    ValueError when the header or the content reaches past `end`, or when its length is
    spelled any way but the shortest, the only one DER allows.
    """
    content, size = _der_header(der, start, end)  # the indefinite form is refused in there
    if content + size > end:
        raise ValueError(CUT_SHORT)
    return content, content + size


def _ber_end(der: bytes, start: int, end: int) -> int:
    """Where the value at `start` ends, its lengths read the way BER writes them as well.

    `tbsCertificate` is measured here, not judged: openssl hands those bytes back exactly
    as it read them, so how they spell a length is nothing for us to disagree about - an
    indefinite length closed by an end-of-contents marker among them, which openssl takes
    and keeps like any other. Finding where the value stops is all that is wanted, so that
    the values after it, the ones openssl does rewrite, can be held to DER. What is inside
    is walked only far enough to pass the markers: a marker closes the value it opened, so
    the nesting has to be counted even here. ValueError when a length or a marker runs past
    `end`, because then where the value stops is anyone's guess.
    """
    at, depth = start, 0
    while True:
        if depth and der[at : at + 2] == b"\x00\x00":  # end-of-contents: one level closes
            at, depth = at + 2, depth - 1
            if not depth:
                return at
            continue
        content, size = _der_header(der, at, end, minimal=False)
        if size is None:  # indefinite: what it holds is values again, down to its marker
            at, depth = content, depth + 1
            continue
        at = content + size
        if at > end:
            raise ValueError(CUT_SHORT)
        if not depth:
            return at


def _check_der_lengths(der: bytes, start: int, end: int) -> None:
    """Raise ValueError unless every value in `der[start:end]`, and every value nested in
    a constructed one, spells its length the way DER requires.

    A list rather than recursion: how deep the nesting goes is the file's to choose, and a
    stack this one overflows is not the error to answer with.
    """
    spans = [(start, end)]
    while spans:
        at, stop = spans.pop()
        while at < stop:
            content, finish = _der_value(der, at, stop)
            if der[at] & 0x20:  # constructed: what it holds is values again
                spans.append((content, finish))
            at = finish


def _check_one_der_value(der: bytes) -> None:
    """Raise ValueError unless `der` is one certificate, nothing follows it, and it is
    already what `openssl x509 -outform DER` would write back.

    Whether the value is a certificate at all is `_parse_x509`'s question, and it is asked
    first; the question here is the spelling, because `digest` hashes these bytes while
    openssl fingerprints its own re-encoding. So every length has to be the one openssl
    writes - except `tbsCertificate`, which it does not rewrite at all: OpenSSL keeps the
    encoding it read there (the signature is over those exact bytes) and hands it back
    unchanged, so a padded length in it - or the indefinite form, which is BER and not DER
    at all - is in the file and in openssl's fingerprint alike. The value is therefore only
    measured, never judged: `_ber_end` finds where it stops and nothing in it is refused.
    Checked against OpenSSL 3.0.13 and 3.6.4, which agree, and refusing more than they do
    would turn away a certificate that `openssl x509 -in F -out F` cannot rewrite.
    `scripts/trust-ca.sh` draws the line from the other side - it compares openssl's
    re-encoding with the decoded block byte for byte - and lands on the same one.
    """
    if len(der) < 2:
        raise ValueError("no certificate between the PEM delimiters")
    content, end = _der_value(der, 0, len(der))
    if end != len(der):
        raise ValueError("more than one certificate between the PEM delimiters; pass one")
    after_tbs = _ber_end(der, content, end)  # tbsCertificate, left exactly as it came
    _check_der_lengths(der, after_tbs, end)


def _parse_x509(der: bytes) -> None:
    """Raise ValueError unless `der` is one X.509 certificate and nothing besides.

    The parser is OpenSSL's own, reached through `ssl`: `load_verify_locations` hands DER
    straight to it without a file or a socket, and the context it fills is thrown away.
    Going through the standard library rather than `cryptography` keeps mitmproxy the
    only runtime dependency (AGENTS.md) - that `cryptography` comes in under mitmproxy today
    is its choice to revisit, not ours to depend on.

    OpenSSL reads `cadata` as a *list*, so a second certificate after the first would pass:
    the length is checked too. Those bytes hash to neither CA, and `trust` would file them
    as one anchor and print that fingerprint as the CA's.
    """
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=der)
    except (ssl.SSLError, ValueError) as exc:  # SSLError is an OSError: it must not escape
        raise ValueError(f"no X.509 certificate between the PEM delimiters: {exc}") from None
    _check_one_der_value(der)


def cert_der(pem_text: str) -> bytes:
    """The DER bytes of one PEM certificate; ValueError when the text is not that.

    A text that carries the private key as well is refused rather than filtered down to
    its certificate: `key_path`'s combined file is exactly such a text, and a trust store
    handed it would keep the CA's private key in a file of its own - on Linux a
    world-readable trust anchor. Refusing is the only safe reading of that mistake.

    What the delimiters hold is parsed, not merely base64-decoded: a `BEGIN CERTIFICATE`
    block can wrap any bytes at all, and `trust` files what it is given as a trust anchor.
    """
    if PRIVATE_KEY_PEM.search(pem_text):
        raise ValueError(
            "this holds the CA private key as well as the certificate; "
            f"pass the certificate-only {CA_BASENAME}-ca-cert.pem"
        )
    der = ssl.PEM_cert_to_DER_cert(pem_text.strip())
    _parse_x509(der)
    return der


def load_cert(pem: Path) -> bytes:
    """`cert_der` of a file: OSError when it cannot be read, ValueError when what is in
    it is not one certificate - bytes that are not even text among them (a DER `.cer`,
    say), which `read_text` would raise a `UnicodeDecodeError` over."""
    raw = Path(pem).read_bytes()
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        raise ValueError(
            f"this is not PEM text; pass the {CA_BASENAME}-ca-cert.pem a CA directory holds"
        ) from None
    return cert_der(text)


def digest(der: bytes, algorithm: str = "sha256", *, sep: str = ":") -> str:
    """A certificate's fingerprint in upper-case hex.

    SHA-256 with colons is how `openssl x509 -fingerprint` prints it; SHA-1 without them is
    how keychains and certutil.exe look a certificate up. Computed here rather than by
    openssl, which a Windows machine does not have.
    """
    hexed = hashlib.new(algorithm, der, usedforsecurity=False).hexdigest().upper()
    return sep.join(hexed[i : i + 2] for i in range(0, len(hexed), 2))


def fingerprint(pem: Path, algorithm: str = "sha256", *, sep: str = ":") -> str:
    """`digest` of a certificate file, or "" when there is no certificate to read there."""
    try:
        der = load_cert(pem)
    except (OSError, ValueError):
        return ""
    return digest(der, algorithm, sep=sep)
