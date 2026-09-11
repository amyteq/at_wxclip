"""The proxy's own CA: create it, find it, say how to make WeChat trust it.

mitmproxy owns the CA material, and we keep its file names (`mitmproxy-ca*`) so it picks
the same files up from `confdir` on the next run - only the directory is ours. The private
key never belongs in the repo: the default location is outside it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def trust_targets(app_id: str = FLATPAK_APP_ID) -> list[TrustTarget]:
    """Which trust stores this machine actually has. Detection only - installing is
    `scripts/trust-ca.sh`, because the system store needs sudo."""
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


def trust_hint(ca_dir: Path, app_id: str = FLATPAK_APP_ID) -> str:
    """Human-facing next steps; the walkthrough lives in `docs/M0-操作步骤.md`."""
    pem = cert_path(ca_dir)
    lines = [
        f"CA certificate: {pem}",
        f"CA private key: {key_path(ca_dir)}  (keep it here, never commit it)",
    ]
    fp = fingerprint(pem)
    if fp:
        lines.append(f"SHA-256:        {fp}")
    lines.append("")
    lines.append("Trust it where WeChat will look:")
    for target in trust_targets(app_id):
        mark = "+" if target.available else "-"
        lines.append(f"  [{mark}] {target.name}: {target.detail}")
    lines.append("")
    lines.append(f"  scripts/trust-ca.sh --cert {pem}          # detected stores")
    lines.append("  scripts/trust-ca.sh --system              # adds the system store (sudo)")
    lines.append("  scripts/trust-ca.sh --remove              # undo")
    return "\n".join(lines)
