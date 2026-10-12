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


def _one_asn1_value(der: bytes) -> bool:
    """True when `der` is a single ASN.1 value with nothing after it.

    Only the outer tag's length is read; whether what it holds is a certificate is
    `_parse_x509`'s question, and it is asked first.
    """
    if len(der) < 2:
        return False
    length = der[1]
    if length & 0x80:  # long form: the low seven bits count the length's own bytes
        count = length & 0x7F
        return len(der) == 2 + count + int.from_bytes(der[2 : 2 + count], "big")
    return len(der) == 2 + length


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
    if not _one_asn1_value(der):
        raise ValueError("more than one certificate between the PEM delimiters; pass one")


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
