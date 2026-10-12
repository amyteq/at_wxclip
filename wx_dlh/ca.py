"""The proxy's own CA: create it, find it, say how to make WeChat trust it.

mitmproxy owns the CA material, and we keep its file names (`mitmproxy-ca*`) so it picks
the same files up from `confdir` on the next run - only the directory is ours. The private
key never belongs in the repo: the default location is outside it.

Creating the CA is portable - mitmproxy does that anywhere. *Trusting* it is not: the
stores below are the ones a Linux WeChat reads, and probing for `flatpak` or `certutil`
on another platform answers a question nobody asked. Everything past `ensure_ca` is
therefore behind `platforms`, and on Windows and macOS says so instead of guessing.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from wx_dlh import platforms

#: mitmproxy's naming inside `confdir`. Do not rename: mitmproxy looks for these.
CA_BASENAME = "mitmproxy"
KEY_SIZE = 2048

#: The Flatpak build of WeChat has no access to the host home, so its Chromium runtime
#: reads a NSS database inside the sandbox home instead of `~/.pki/nssdb`.
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


def fingerprint(pem: Path) -> str:
    """SHA-256 fingerprint, so the certificate in the trust store can be identified."""
    if not shutil.which("openssl"):
        return ""
    result = subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-noout", "-fingerprint", "-sha256"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip().partition("=")[2]


@dataclass(frozen=True)
class TrustTarget:
    """One place the certificate has to be installed, and whether it is reachable."""

    name: str
    detail: str
    available: bool


def flatpak_nssdb(app_id: str = FLATPAK_APP_ID) -> Path:
    return Path.home() / ".var" / "app" / app_id / ".pki" / "nssdb"


def trust_targets(app_id: str = FLATPAK_APP_ID, platform: str | None = None) -> list[TrustTarget]:
    """Which trust stores this machine actually has. Detection only - installing is
    `scripts/trust-ca.sh`, because the system store needs sudo.

    Empty off Linux: these three stores are what a *Linux* WeChat reads, and reporting
    them as merely unavailable elsewhere would read as "install these and you are done".
    """
    if platforms.current(platform) != platforms.LINUX:
        return []
    flatpak = shutil.which("flatpak")
    installed = False
    if flatpak:
        result = subprocess.run(
            [flatpak, "info", app_id], capture_output=True, text=True, check=False
        )
        installed = result.returncode == 0
    return [
        TrustTarget(
            "flatpak-nss",
            f"NSS db in the {app_id} sandbox: {flatpak_nssdb(app_id)}",
            installed and bool(shutil.which("certutil")),
        ),
        TrustTarget(
            "user-nss",
            f"user NSS db (non-Flatpak Chromium builds): {Path.home() / '.pki' / 'nssdb'}",
            bool(shutil.which("certutil")),
        ),
        TrustTarget(
            "system",
            "system trust anchors: /etc/ca-certificates/trust-source/anchors (needs sudo)",
            bool(shutil.which("update-ca-trust")),
        ),
    ]


def trust_hint(ca_dir: Path, app_id: str = FLATPAK_APP_ID, platform: str | None = None) -> str:
    """Human-facing next steps; the walkthrough lives in `docs/M0-runbook.md`.

    The certificate itself is the same file on every platform, so those lines are always
    printed. What changes is whether we can say where to put it: off Linux we cannot, and
    say that rather than print Linux's stores with minus signs next to them.
    """
    pem = cert_path(ca_dir)
    lines = [
        f"CA certificate: {pem}",
        f"CA private key: {key_path(ca_dir)}  (keep it here, never commit it)",
    ]
    fp = fingerprint(pem)
    if fp:
        lines.append(f"SHA-256:        {fp}")
    lines.append("")

    host = platforms.current(platform)
    if host != platforms.LINUX:
        lines.append(platforms.unsupported_note(host))
        return "\n".join(lines)

    lines.append("Trust it where WeChat will look:")
    for target in trust_targets(app_id, platform=platform):
        mark = "+" if target.available else "-"
        lines.append(f"  [{mark}] {target.name}: {target.detail}")
    lines.append("")
    lines.append(f"  scripts/trust-ca.sh --cert {pem}          # detected stores")
    lines.append("  scripts/trust-ca.sh --system              # adds the system store (sudo)")
    lines.append("  scripts/trust-ca.sh --remove              # undo")
    return "\n".join(lines)
