"""Which host platform this is, and what has actually been brought up on it.

Everything that moves bytes is already portable: `keystream`, `decrypt`, `downloader`,
`pipeline`, `server` and the proxy itself run anywhere Python does. Two things are not,
and they are the whole of issue #11:

    routing   how WeChat's traffic reaches our proxy
    trust     how WeChat comes to accept our CA

Only Linux has been carried end to end (M0, 2026-09-11), and even there both steps are a
documented human procedure, not code - `docs/M0-runbook.md`. On Windows and macOS the two
halves stand differently. Trust has `wx-dlh ca --install` (#28, with `scripts/trust-ca.sh`
from #23 for a host without wx-dlh), which installs into the keychain or the certificate
store and reads the store back. Routing has only the reference implementation's path,
which no one has run. Neither has been watched working against a real WeChat, and this
module exists so that is stated out loud. `wx-dlh ca` once probed `flatpak` and
`certutil` whichever platform it was on, so a macOS user got three unavailable Linux
stores and Linux-only commands to run - wrong in a way that looks like a working answer.
`wx_dlh.trust` now picks the stores by OS; what it cannot say is whether WeChat reads them.
Which machine that OS belongs to is one question, not two, so `current()` asks
`trust.detect_os()` rather than reading `sys.platform` a second way: under WSL both
answer Windows, because WeChat runs on the host and it is the host's routing and the
host's certificate store that have to work.

Nothing here sets a proxy or installs a certificate, and `verified` stays False until a
real client has been driven through the proxy. A store that lists the CA is not yet a
client that accepts it: on Linux `update-ca-trust` reported success at every step while
the client kept rejecting the certificate, and only a real client on a real machine
showed why. The checks that decide it are in `docs/cross-platform-verification.md`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from wx_dlh import trust

LINUX = "linux"
MACOS = "macos"
WINDOWS = "windows"
UNKNOWN = "unknown"

#: Where the per-platform steps live, once they exist.
PLAYBOOK = "docs/cross-platform-verification.md"
#: Installs the CA on every platform here and reads the store back; see `wx_dlh.trust`.
TRUST_COMMAND = "wx-dlh ca --install"
#: The same, for a machine without wx-dlh on it - the Docker host, say.
TRUST_SCRIPT = "scripts/trust-ca.sh"
ISSUE = "https://github.com/amyteq/at_wxclip/issues/11"


def current(raw: str | None = None) -> str:
    """Normalise `sys.platform` to one of the four names above.

    Asked about this machine, this is `trust.detect_os()` - the same answer the trust
    stores are picked by, so the status line and the store list under it cannot describe
    two different machines. It matters for WSL alone: `sys.platform` is linux there, and
    the machine that has to be routed and has to trust the CA is the Windows host. A WSL
    host is therefore an unverified Windows host, which is what it is.

    `raw` is for tests: the whole point of this module is behaviour that cannot be
    exercised on the machine it was written on. An explicit name is a deliberate claim,
    taken at face value and never probed for WSL. It accepts an already-normalised name
    too, so that a `platform=` argument can be handed down through several callers
    without anyone having to track whether it has been through here yet.
    """
    if raw is None:
        try:
            return trust.detect_os()
        except trust.TrustError:  # a platform `detect_os` has no stores for
            return UNKNOWN
    if raw.startswith("linux") or raw == LINUX:
        return LINUX
    if raw == "darwin" or raw == MACOS:
        return MACOS
    if raw in ("win32", "cygwin", "msys") or raw == WINDOWS:
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
        trust="`wx-dlh ca --install` into the sandbox NSS db (main process); the webview "
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
        trust="`wx-dlh ca --install` (or `scripts/trust-ca.sh`): the login keychain through "
        "`security add-trusted-cert`, or the System keychain with `--system` (the reference "
        "implementation's choice); read back with `security verify-cert`, never watched "
        "against a real WeChat",
        doc=PLAYBOOK,
    ),
    WINDOWS: HostSupport(
        platform=WINDOWS,
        name="Windows",
        verified=False,
        routing=r"WinINET registry values under "
        r"`HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` "
        "(what the reference implementation does; never run for this project)",
        trust=r"`wx-dlh ca --install` natively or from WSL, or `scripts/trust-ca.sh` from Git "
        r"Bash, MSYS2, Cygwin or WSL: `CurrentUser\Root` through certutil.exe, or "
        r"`LocalMachine\Root` with `--system`; read back from the store, never watched "
        "against a real WeChat",
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


def _asked_from_wsl(raw: str | None, platform: str) -> bool:
    """Did asking about this machine answer Windows from a Linux shell - i.e. is this WSL?

    Only when nobody named a platform: `status_line("win32")` is already a claim about
    Windows, and the suite that makes it is not running under WSL.
    """
    return raw is None and platform == WINDOWS and trust.is_wsl()


def _raw_name(raw: str | None) -> str:
    """What to call a platform that has no `HostSupport` entry.

    `current()` flattens everything it does not recognise to "unknown", which is this
    module's bucket and not a name to show anyone. The name worth printing is the one the
    caller asked about: the argument they passed, or - nobody having passed one, so the
    question was about this machine - `sys.platform`.
    """
    return sys.platform if raw is None else raw


def status_line(platform: str | None = None) -> str:
    """One line: which platform this is, and whether WeChat has been watched working on it.

    `wx-dlh ca` prints it first, above the trust stores `wx_dlh.trust` picked for the same
    machine. `wx-dlh proxy` and `wx-dlh capture` print it on stderr before they listen, and
    only when the platform is unverified. Not on `--dry-run`: that output is parsed, so
    both dry-run branches return before anything from here reaches the stream.
    """
    name = current(platform)
    known = support(name)
    if known is None:
        asked = _raw_name(platform)
        return f"platform: {asked} - not considered; issue #11 covers Windows and macOS"
    # From inside WSL a bare "Windows" reads as a misdetection, so say which machine is
    # meant: the host, because that is where WeChat and its certificate store are.
    label = known.name + (" host (from WSL)" if _asked_from_wsl(platform, name) else "")
    if known.verified:
        return f"platform: {label} - verified end to end, steps in {known.doc}"
    return (
        f"platform: {label} - NOT verified; routing is unimplemented and CA trust "
        f"({TRUST_COMMAND}) is untested against a real WeChat (issue #11)"
    )


def unsupported_note(platform: str | None = None) -> str:
    """Why this platform is unverified, and where the work to verify it lives.

    It names the mechanism but stops short of being a procedure to paste: the ordered
    steps live in the playbook, next to the checks that decide whether they did anything.
    On Linux every one of those commands succeeded long before any of them worked, so a
    bare command list is the one shape this advice must not take. Trust is different only
    in having a command to name: `wx-dlh ca --install` reads back the store it wrote to,
    which is necessary and still not the same thing as WeChat accepting the CA.
    """
    known = support(platform)
    # An unrecognised platform has no name to carry a sentence - `current()` has already
    # flattened it to "unknown" - so address it rather than label it; the closing line
    # below quotes the raw name, which is all anyone here knows it by.
    label = f"on {known.name}" if known else "on this platform"
    # Off the considered platforms `wx-dlh ca --install` and trust-ca.sh both refuse to
    # run, so neither is named there.
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
            "Routing has no code here yet; trust has a command. Neither has been watched "
            "working against a real WeChat:",
            f"  {TRUST_COMMAND}   installs the CA and reads the store back "
            f"({TRUST_SCRIPT} without wx-dlh)",
        ]
    else:
        lines.append("Neither has been run against a real client, so neither is in the code yet:")
    lines += [
        f"  {PLAYBOOK}   the checks to run, in order, with what counts as a pass",
        f"  {ISSUE}   the issue this lands under",
    ]
    if known is None:
        lines.append("")
        asked = _raw_name(platform)
        lines.append(f"Nothing is known about {asked!r} at all - start from the playbook.")
    return "\n".join(lines)
