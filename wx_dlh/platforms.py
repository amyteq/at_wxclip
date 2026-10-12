"""Which host platform this is, and what has actually been brought up on it.

Everything that moves bytes is already portable: `keystream`, `decrypt`, `downloader`,
`pipeline`, `server` and the proxy itself run anywhere Python does. Two things are not,
and they are the whole of issue #11:

    routing   how WeChat's traffic reaches our proxy
    trust     how WeChat comes to accept our CA

Only Linux has been carried end to end (M0, 2026-09-11), and even there both steps are a
documented human procedure, not code - `docs/M0-runbook.md`. On Windows and macOS the two
halves stand differently. Trust has `scripts/trust-ca.sh` (#23), which installs into the
keychain or the certificate store and reads the store back. Routing has only the
reference implementation's path, which no one has run. Neither has been watched working
against a real WeChat, and this module exists so that is stated out loud instead of
leaking out as a row of dead probes. `ca.trust_targets()` used to shell out to `flatpak`
and `certutil` whichever platform it was on, so a macOS user got three unavailable Linux
stores and Linux-only commands to run - wrong in a way that looks like a working answer.

Nothing here sets a proxy or installs a certificate, and `verified` stays False until a
real client has been driven through the proxy. A store that lists the CA is not yet a
client that accepts it: on Linux `update-ca-trust` reported success at every step while
the client kept rejecting the certificate, and only a real client on a real machine
showed why. The checks that decide it are in `docs/cross-platform-verification.md`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

LINUX = "linux"
MACOS = "macos"
WINDOWS = "windows"
UNKNOWN = "unknown"

#: Where the per-platform steps live, once they exist.
PLAYBOOK = "docs/cross-platform-verification.md"
#: Installs the CA on every platform here and reads the store back; see its header.
TRUST_SCRIPT = "scripts/trust-ca.sh"
ISSUE = "https://github.com/amyteq/at_wxclip/issues/11"


def current(raw: str | None = None) -> str:
    """Normalise `sys.platform` to one of the four names above.

    `raw` is for tests: the whole point of this module is behaviour that cannot be
    exercised on the machine it was written on. It accepts an already-normalised name
    too, so that a `platform=` argument can be handed down through several callers
    without anyone having to track whether it has been through here yet.
    """
    value = sys.platform if raw is None else raw
    if value.startswith("linux") or value == LINUX:
        return LINUX
    if value == "darwin" or value == MACOS:
        return MACOS
    if value in ("win32", "cygwin", "msys") or value == WINDOWS:
        return WINDOWS
    return UNKNOWN


@dataclass(frozen=True)
class HostSupport:
    """What is known about getting WeChat onto the proxy on one platform.

    `verified` is the only field that matters for a decision: it means a person watched
    a real WeChat client on this platform play a video through this proxy. Everything
    else is a description, and a description has never once been the hard part.
    """

    platform: str
    name: str
    verified: bool
    routing: str
    trust: str
    doc: str


SUPPORT: dict[str, HostSupport] = {
    LINUX: HostSupport(
        platform=LINUX,
        name="Linux",
        verified=True,
        # Both halves are needed and they are separate mechanisms - the in-app setting
        # never reaches the webview. See docs/project-background.md.
        routing="in-app proxy settings (main process) + "
        "`flatpak override --env=http_proxy=…` (WeChatAppEx webview)",
        trust="scripts/trust-ca.sh into the sandbox NSS db (main process); the webview "
        "ignores every trust store, so it takes "
        "`--ignore-certificate-errors-spki-list` injected by LD_PRELOAD",
        doc="docs/M0-runbook.md",
    ),
    MACOS: HostSupport(
        platform=MACOS,
        name="macOS",
        verified=False,
        routing="`networksetup -setsecurewebproxy` per active network service "
        "(what the reference implementation does; never run for this project)",
        trust="`scripts/trust-ca.sh`: the login keychain through `security add-trusted-cert`, "
        "or the System keychain with `--system` (the reference implementation's choice); "
        "read back with `security verify-cert`, never watched against a real WeChat",
        doc=PLAYBOOK,
    ),
    WINDOWS: HostSupport(
        platform=WINDOWS,
        name="Windows",
        verified=False,
        routing=r"WinINET registry values under "
        r"`HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` "
        "(what the reference implementation does; never run for this project)",
        trust=r"`scripts/trust-ca.sh` from Git Bash, MSYS2, Cygwin or WSL: `CurrentUser\Root` "
        r"through certutil.exe, or `LocalMachine\Root` with `--system`; read back from the "
        "store, never watched against a real WeChat",
        doc=PLAYBOOK,
    ),
}


def support(platform: str | None = None) -> HostSupport | None:
    """What is known about `platform`; `None` for anything we have never considered.

    Normalises first, so `support("darwin")` and `support("macos")` are the same
    question - the raw name is what every caller actually has in hand.
    """
    return SUPPORT.get(current(platform))


def is_verified(platform: str | None = None) -> bool:
    """Has a real WeChat client on this platform been driven through the proxy."""
    known = support(platform)
    return known is not None and known.verified


def status_line(platform: str | None = None) -> str:
    """One line for the top of `wx-dlh proxy --dry-run` and friends."""
    name = current(platform)
    known = support(name)
    if known is None:
        return f"platform: {sys.platform} - not considered; issue #11 covers Windows and macOS"
    if known.verified:
        return f"platform: {known.name} - verified end to end, steps in {known.doc}"
    return (
        f"platform: {known.name} - NOT verified; routing is unimplemented and CA trust "
        f"({TRUST_SCRIPT}) is untested against a real WeChat (issue #11)"
    )


def unsupported_note(platform: str | None = None) -> str:
    """Why this platform is unverified, and where the work to verify it lives.

    It names the mechanism but stops short of being a procedure to paste: the ordered
    steps live in the playbook, next to the checks that decide whether they did anything.
    On Linux every one of those commands succeeded long before any of them worked, so a
    bare command list is the one shape this advice must not take. Trust is different only
    in having a script to name: `scripts/trust-ca.sh` reads back the store it wrote to,
    which is necessary and still not the same thing as WeChat accepting the CA.
    """
    known = support(platform)
    # An unrecognised platform has no name worth printing - `current()` has already
    # flattened it to "unknown" - so address it rather than label it.
    label = f"on {known.name}" if known else "on this platform"
    # Off the considered platforms trust-ca.sh refuses to run, so it is not named there.
    state = "is not verified" if known else "is not implemented"
    lines = [
        f"Pointing WeChat at this proxy {state} {label}.",
        "",
        "  what it would take, routing: " + (known.routing if known else "unknown"),
        "  what it would take, trust:   " + (known.trust if known else "unknown"),
        "",
    ]
    if known:
        lines += [
            "Routing has no code here yet; trust has a script. Neither has been watched "
            "working against a real WeChat:",
            f"  {TRUST_SCRIPT}   installs the CA and reads the store back",
        ]
    else:
        lines.append("Neither has been run against a real client, so neither is in the code yet:")
    lines += [
        f"  {PLAYBOOK}   the checks to run, in order, with what counts as a pass",
        f"  {ISSUE}   the issue this lands under",
    ]
    if known is None:
        lines.append("")
        lines.append(f"Nothing is known about {sys.platform!r} at all - start from the playbook.")
    return "\n".join(lines)
