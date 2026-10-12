"""Which host platform this is, and what has actually been brought up on it.

Everything that moves bytes is already portable: `keystream`, `decrypt`, `downloader`,
`pipeline`, `server` and the proxy itself run anywhere Python does. Two things are not,
and they are the whole of issue #11:

    routing   how WeChat's traffic reaches our proxy
    trust     how WeChat comes to accept our CA

Only Linux has been carried end to end (M0, 2026-09-11), and even there both steps are a
documented human procedure, not code - `docs/M0-runbook.md`. Windows and macOS have a
known-looking path in the reference implementation and nothing more: no one has run it.
This module exists so that difference is stated out loud instead of leaking out as a row
of dead probes. `ca.trust_targets()` used to shell out to `flatpak` and `certutil`
whichever platform it was on, so a macOS user got three unavailable Linux stores and a
`.sh` to run - wrong in a way that looks like a working answer.

Nothing here sets a proxy or installs a certificate. Writing that before it can be tested
is exactly what issue #11 argues against: on Linux `update-ca-trust` reported success at
every step while the client kept rejecting the certificate, and only a real client on a
real machine showed why. Until someone runs the checks in `docs/cross-platform-verification.md`, an
adapter would be a guess wearing the costume of an answer.
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
        trust="`sudo security add-trusted-cert -d -r trustRoot -k "
        "/Library/Keychains/System.keychain` (likewise unrun)",
        doc=PLAYBOOK,
    ),
    WINDOWS: HostSupport(
        platform=WINDOWS,
        name="Windows",
        verified=False,
        routing=r"WinINET registry values under "
        r"`HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` "
        "(what the reference implementation does; never run for this project)",
        trust="the user's `root` certificate store, via certmgr or an equivalent (likewise unrun)",
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
        f"platform: {known.name} - NOT verified; routing and CA trust are unimplemented (issue #11)"
    )


def unsupported_note(platform: str | None = None) -> str:
    """Why this platform has no steps yet, and where the work to produce them lives.

    It names the mechanism but stops short of being a procedure to paste: the ordered
    steps live in the playbook, next to the checks that decide whether they did anything.
    On Linux every one of those commands succeeded long before any of them worked, so a
    bare command list is the one shape this advice must not take.
    """
    known = support(platform)
    # An unrecognised platform has no name worth printing - `current()` has already
    # flattened it to "unknown" - so address it rather than label it.
    label = f"on {known.name}" if known else "on this platform"
    lines = [
        f"Pointing WeChat at this proxy is not implemented {label}.",
        "",
        "  what it would take, routing: " + (known.routing if known else "unknown"),
        "  what it would take, trust:   " + (known.trust if known else "unknown"),
        "",
        "Neither has been run against a real client, so neither is in the code yet:",
        f"  {PLAYBOOK}   the checks to run, in order, with what counts as a pass",
        f"  {ISSUE}   the issue this lands under",
    ]
    if known is None:
        lines.append("")
        lines.append(f"Nothing is known about {sys.platform!r} at all - start from the playbook.")
    return "\n".join(lines)
