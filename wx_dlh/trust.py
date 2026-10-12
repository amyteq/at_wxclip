"""Put the proxy CA into the trust stores WeChat reads, list it there, take it out again.

This is `wx-dlh ca --install / --list / --remove`, and it is `scripts/trust-ca.sh` ported
to Python: a pip-installed wx-dlh has no `scripts/`, and on Windows the script needs Git
Bash. The script stays for machines that run wx-dlh in Docker and have no Python of their
own. Both take the same store flags, use the same nickname and file names, and are held to
the same cases (`tests/test_trust.py` here, `tests/test_trust_ca.py` there), so a change to
one is a change to both.

Which store counts depends on the OS, and on Linux on how WeChat was installed:

Linux - the Channels page renders in a Chromium-based webview, and Chromium on Linux takes
user-added roots from a NSS database:

    flatpak   the NSS db inside the sandbox home, ~/.var/app/<app-id>/.pki/nssdb. A Flatpak
              home only shows the subpaths its metadata persists, so `.pki` is persisted
              as well - without that the file is there and the app does not see it.
    user      ~/.pki/nssdb, for a distro package (e.g. /opt/wechat)
    system    the distro's trust anchors (sudo); a Flatpak app does not see them

macOS - the keychain, through `security add-trusted-cert`:

    keychain  the login keychain, trusted for this user (a password dialog)
    system    the System keychain, in the admin domain (sudo)

Windows - the certificate store, through certutil. From WSL it is the *host's* store:
WeChat runs on the host, so the distro's own stores are never consulted.

    windows   CurrentUser\\Root (a confirmation dialog)
    system    LocalMachine\\Root (an administrator shell)

Only Linux has been carried end to end (`docs/M0-runbook.md`); nobody has yet watched
WeChat accept the CA on macOS or Windows (#11). So every install is read back from the
store it wrote to - on Linux every command succeeded long before WeChat accepted anything -
and stores are searched by fingerprint, never by name: every mitmproxy CA is called
"mitmproxy", another tool's copy may sit in the same store, and a nickname outlives the
certificate under it - `wx-dlh ca` can mint a new CA while the old one keeps the name.
`--remove` goes by that fingerprint too: an entry under our own nickname or path that
holds some other CA is reported, not deleted - it is a trust anchor nobody asked about.

Every command goes through `run` and every tool lookup through `which`. That is the seam
the tests use, so nothing under `tests/` touches a real trust store.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from wx_dlh.ca import FLATPAK_APP_ID, cert_der, cert_path, digest, fingerprint, key_path, load_cert

NICKNAME = "wx-dlh-ca"
#: The name before the rename to wx-dlh. The same CA moved over, so an install clears it.
LEGACY_NICKNAME = "wxclip-ca"

ACTIONS = ("install", "remove", "list")

#: The stores each OS has. `system` is the only one that needs sudo or an administrator.
STORES: dict[str, tuple[str, ...]] = {
    "linux": ("flatpak", "user", "system"),
    "macos": ("keychain", "system"),
    "windows": ("windows", "system"),
}

#: Where each distro family keeps its trust anchors, and the tool that rebuilds the bundle
#: from them: Arch, Fedora/RHEL, Debian/Ubuntu, openSUSE. Debian's tool only picks up
#: `*.crt`, so that is the file name everywhere.
ANCHOR_DIRS: tuple[tuple[str, str], ...] = (
    ("/etc/ca-certificates/trust-source/anchors", "update-ca-trust"),
    ("/etc/pki/ca-trust/source/anchors", "update-ca-trust"),
    ("/usr/local/share/ca-certificates", "update-ca-certificates"),
    ("/etc/pki/trust/anchors", "update-ca-certificates"),
)

MACOS_SYSTEM_KEYCHAIN = "/Library/Keychains/System.keychain"
#: Where WSL finds certutil.exe when the Windows PATH is not appended to its own.
WSL_CERTUTIL = "/mnt/c/Windows/System32/certutil.exe"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class TrustError(RuntimeError):
    """The request cannot be carried out at all: a store this OS does not have, a run
    under sudo, no certificate. A store that fails is not this - it is reported, and the
    other stores still get their turn."""


def run_default(
    argv: Sequence[str], *, show: bool = False, **kwargs
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run`. A command that changes a store runs with `show`, so its output,
    a sudo prompt or a dialog's aftermath reach the terminal as they would from a shell;
    a query is captured, because its answer is what we read."""
    if show:
        return subprocess.run(list(argv), text=True, check=False, **kwargs)
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, **kwargs)


def is_wsl(
    env: Mapping[str, str] = os.environ,
    osrelease: Path = Path("/proc/sys/kernel/osrelease"),
) -> bool:
    if env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in osrelease.read_text().lower()
    except OSError:
        return False


def detect_os(platform: str = sys.platform, *, wsl: bool | None = None) -> str:
    """linux, macos or windows - the last for WSL too, because WeChat runs on the host."""
    if platform == "darwin":
        return "macos"
    if platform in ("win32", "cygwin", "msys"):
        return "windows"
    if platform.startswith("linux"):
        return "windows" if (is_wsl() if wsl is None else wsl) else "linux"
    raise TrustError(f"unsupported platform: {platform} - pass --os linux|macos|windows")


@dataclass
class Trust:
    """One `wx-dlh ca --install/--remove/--list` against one machine's stores."""

    os: str
    cert: Path
    app_id: str = FLATPAK_APP_ID
    home: Path = field(default_factory=Path.home)
    #: Windows' own Python: paths are Windows paths already and certutil is on PATH.
    #: Otherwise (Cygwin, MSYS2, WSL) both need translating.
    native_windows: bool = sys.platform == "win32"
    #: Already root (a container, say): the system stores need no sudo in front.
    root: bool = False
    anchor_dirs: Sequence[tuple[str, str]] = ANCHOR_DIRS
    run: Runner = run_default
    which: Callable[[str], str | None] = shutil.which
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    #: The certificate itself, read and checked by `apply` before any store is touched.
    #: Everything a store is searched or filed by is derived from it, never from `cert`.
    der: bytes = field(default=b"", init=False, repr=False)

    @classmethod
    def for_this_machine(
        cls,
        cert: Path,
        *,
        os_name: str | None = None,
        app_id: str = FLATPAK_APP_ID,
        env: Mapping[str, str] = os.environ,
        euid: int | None = None,
    ) -> Trust:
        """Detect the OS, and refuse to run under sudo.

        Under sudo, HOME is root's - and on macOS so is the login keychain - so the
        default certificate and the user stores would quietly be the wrong ones. sudo is
        asked for per command instead, and only for `--system`.

        What decides this is `geteuid`, not the OS: Windows has no sudo and no `geteuid`
        either, so it is let through by the `-1` below. WSL reports `windows` - WeChat
        runs on the host - but is POSIX underneath, and sudo there is still sudo.
        """
        os_name = os_name or detect_os()
        if euid is None:
            euid = os.geteuid() if hasattr(os, "geteuid") else -1
        if euid == 0 and env.get("SUDO_USER"):
            raise TrustError(
                "run this without sudo - it asks for sudo itself, and only for --system"
            )
        return cls(os_name, Path(cert).expanduser(), app_id=app_id, root=euid == 0)

    # -- which stores -------------------------------------------------------------------

    def stores(self, wanted: Iterable[str] = ()) -> list[str]:
        """The stores asked for (`all` = every one this OS has), or else the defaults."""
        have = STORES[self.os]
        picked: list[str] = []
        for name in wanted:
            for store in have if name == "all" else (name,):
                if store not in have:
                    flags = " ".join(f"--{s}" for s in have)
                    raise TrustError(
                        f"--{store} is not a store on {self.os} (this OS has: {flags})"
                    )
                if store not in picked:
                    picked.append(store)
        if picked:
            return picked
        defaults = self.default_stores()
        if not defaults:
            raise TrustError(
                "no NSS database found; pass --user to create ~/.pki/nssdb, or --system"
            )
        return defaults

    def default_stores(self) -> list[str]:
        """What `--install` uses without a store flag - never one that needs sudo."""
        if self.os == "macos":
            return ["keychain"]
        if self.os == "windows":
            return ["windows"]
        found = []
        flatpak = self.which("flatpak")
        if flatpak and self._ask([flatpak, "info", self.app_id]).returncode == 0:
            found.append("flatpak")
        if self.user_db.is_dir():
            found.append("user")
        return found

    def describe(self, store: str) -> str:
        if self.os == "linux":
            if store == "flatpak":
                return f"NSS db inside the {self.app_id} Flatpak sandbox: {self.flatpak_db}"
            if store == "user":
                return f"NSS db for non-Flatpak builds (and your browsers): {self.user_db}"
            found = self._anchor_dir()
            where = found[0] if found else "no supported anchor directory here"
            return f"the distro's trust anchors: {where} (sudo; Flatpak apps do not see them)"
        if self.os == "macos":
            if store == "keychain":
                return "login keychain, trusted for you (a password dialog)"
            return "System keychain, trusted for every user (sudo)"
        if store == "windows":
            return "CurrentUser\\Root (a confirmation dialog)"
        return "LocalMachine\\Root (run from an administrator shell)"

    @property
    def flatpak_db(self) -> Path:
        return self.home / ".var" / "app" / self.app_id / ".pki" / "nssdb"

    @property
    def user_db(self) -> Path:
        return self.home / ".pki" / "nssdb"

    # -- doing it -----------------------------------------------------------------------

    def read_cert(self) -> None:
        """Read the certificate into `der`, refusing anything a store must not be given.

        Every action needs it, listing included: a store is searched by fingerprint, so
        without the certificate there is nothing to search *for*. `--cert` takes a path,
        and `mitmproxy-ca.pem` - certificate *and* private key - looks just as plausible
        as `mitmproxy-ca-cert.pem` next to it. `--system` would install that file as a
        world-readable trust anchor, key and all, so it is parsed and refused here,
        before any store is touched. So is anything else that is not one CA certificate
        spelled the way openssl spells one - a PEM block wrapping arbitrary bytes, a
        bundle, a certificate that may not sign certificates: `install` and `certutil -A`
        copy the file as it stands, and a trust store is no place to find out it was junk.
        """
        try:
            self.der = load_cert(self.cert)
        except OSError as exc:
            if not self.cert.is_file():
                raise TrustError(
                    f"certificate not found: {self.cert}\ncreate it first:  wx-dlh ca"
                ) from None
            raise TrustError(f"cannot read {self.cert}: {exc}") from None
        except ValueError as exc:
            raise TrustError(f"{self.cert}: {exc}") from None

    @property
    def sha256(self) -> str:
        """How a certificate is shown to a human, and how two of them are compared here."""
        return digest(self.der)

    @property
    def sha1(self) -> str:
        """How keychains and certutil.exe look a certificate up."""
        return digest(self.der, "sha1", sep="")

    def apply(self, action: str, stores: Sequence[str]) -> bool:
        """Install, remove or list on every store; True when each one of them succeeded."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action}")
        self.read_cert()
        self.say(f"certificate: {self.cert} ({self.os})")
        self.say(f"  SHA-256: {self.sha256}")
        ok = True
        for store in stores:
            ok = self._one(store, action) and ok
        if action == "install":
            self.say("")
            for line in AFTER_INSTALL[self.os]:
                self.say(line)
            self.say("Undo with: wx-dlh ca --remove")
        return ok

    def _one(self, store: str, action: str) -> bool:
        if self.os == "linux":
            if store == "flatpak":
                return self._flatpak(action)
            if store == "user":
                return self._nss(self.user_db, "user", action)
            return self._linux_system(action)
        if self.os == "macos":
            return self._macos(store, action)
        return self._windows(store, action)

    # -- Linux: NSS ---------------------------------------------------------------------

    def _nss(self, db: Path, label: str, action: str) -> bool:
        certutil = self.which("certutil")
        if certutil is None:
            self.warn(f"[{label}] certutil not found (install nss / libnss3-tools) - skipped")
            return False
        sql = f"sql:{db}"
        if action == "list":
            self.say(f"[{label}] {db}")
            ours = self._nss_ours(certutil, sql, db, label)
            if ours is None:
                return False
            for line in ours:
                found = self._nss_sha256(certutil, sql, line.split()[0])
                self.say(f"  {line}  ({_which_ca(found, self.sha256)})")
            if not ours:
                self.say("  (not installed)")
            return True
        if action == "remove":
            ours = self._nss_ours(certutil, sql, db, label)
            if ours is None:
                return False
            removed, unreadable = [], False
            for nickname in dict.fromkeys(line.split()[0] for line in ours):
                # The nickname is ours; the certificate under it need not be. Deleting
                # what `--list` would call "another CA" would take out a trust anchor
                # nobody asked about - a CA from before the last `wx-dlh ca`, say.
                found = self._nss_sha256(certutil, sql, nickname)
                if found != self.sha256:
                    self.warn(
                        f"[{label}] {nickname} in {db}: {_which_ca(found, self.sha256)}"
                        f" - left alone; take it out with: certutil -d {sql} -D -n {nickname}"
                    )
                    unreadable = unreadable or not found
                    continue
                if not self._change([certutil, "-d", sql, "-D", "-n", nickname], label):
                    return False
                removed.append(nickname)
            if removed:
                self.say(f"[{label}] removed {' and '.join(removed)} from {db}")
            elif not ours:
                self.say(f"[{label}] nothing to remove in {db}")
            return not unreadable
        db.mkdir(parents=True, exist_ok=True)
        if not (db / "cert9.db").is_file() and not self._change(
            [certutil, "-d", sql, "-N", "--empty-password"], label
        ):
            return False
        self._ask([certutil, "-d", sql, "-D", "-n", NICKNAME])
        self._ask([certutil, "-d", sql, "-D", "-n", LEGACY_NICKNAME])
        add = [certutil, "-d", sql, "-A", "-t", "C,,", "-n", NICKNAME, "-i", str(self.cert)]
        if not self._change(add, label):
            return False
        if self._ask([certutil, "-d", sql, "-L", "-n", NICKNAME]).returncode == 0:
            self.say(f"[{label}] installed in {db} (read back)")
            return True
        self.warn(f"[{label}] WARNING: certutil succeeded but {db} does not list it")
        return False

    def _nss_ours(self, certutil: str, sql: str, db: Path, label: str) -> list[str] | None:
        """The database's entries under our two nicknames; None when it could not be read.

        A database certutil cannot open - locked, corrupt, password-protected - is not a
        database our CA is absent from. Reading its failure as "not installed" would make
        `--remove` report an undo that undid nothing, and `--list` call a trusted CA gone.
        """
        if not (db / "cert9.db").is_file():
            return []
        listed = self._ask([certutil, "-d", sql, "-L"])
        if listed.returncode != 0:
            why = " ".join((listed.stderr or listed.stdout).split())
            self.warn(f"[{label}] cannot read {db}: certutil exited {listed.returncode} {why}")
            return None
        names = re.compile(rf"^({re.escape(NICKNAME)}|{re.escape(LEGACY_NICKNAME)})\s")
        return [line.rstrip() for line in listed.stdout.splitlines() if names.match(line)]

    def _nss_sha256(self, certutil: str, sql: str, nickname: str) -> str:
        """The fingerprint of the certificate under `nickname`, "" when it cannot be read.
        The nickname does not say which certificate that is: it stays the same across a
        `wx-dlh ca` that minted a new CA, and so does `mitmproxy`."""
        shown = self._ask([certutil, "-d", sql, "-L", "-n", nickname, "-a"])
        if shown.returncode == 0:
            with contextlib.suppress(ValueError):
                return digest(cert_der(shown.stdout))
        return ""

    def _flatpak(self, action: str) -> bool:
        label = f"flatpak:{self.app_id}"
        if not self._nss(self.flatpak_db, label, action):
            return False
        return action != "install" or self._persist_pki(label)

    def _persist_pki(self, label: str) -> bool:
        """Make the sandbox show `~/.pki` - without it the database above is invisible to
        the app: installed, nothing errors, the proxy still rejected (seen 2026-09-11)."""
        flatpak = self.which("flatpak")
        if flatpak is None:
            return True
        if not self._change([flatpak, "override", "--user", self.app_id, "--persist=.pki"], label):
            return False
        self.say(f"[{label}] ~/.pki is now persisted into the sandbox")
        probe = '[ -f "$HOME/.pki/nssdb/cert9.db" ]'
        seen = self._ask([flatpak, "run", "--command=sh", self.app_id, "-c", probe], timeout=60)
        if seen.returncode == 0:
            self.say(f"[{label}] the app can see the database")
        else:
            self.warn(f"[{label}] WARNING: the app still cannot see $HOME/.pki/nssdb")
        return True

    # -- Linux: system anchors ----------------------------------------------------------

    def _anchor_dir(self) -> tuple[Path, str] | None:
        for directory, tool in self.anchor_dirs:
            if self.which(tool) and Path(directory).is_dir():
                return Path(directory), tool
        return None

    def _linux_system(self, action: str) -> bool:
        found = self._anchor_dir()
        if found is None:
            self.warn(
                "[system] no supported trust-anchor directory (looked for update-ca-trust "
                f"and update-ca-certificates layouts) - install {self.cert} by hand"
            )
            return False
        directory, tool = found
        anchor = directory / f"{NICKNAME}.crt"
        legacy = directory / f"{LEGACY_NICKNAME}.crt"
        if action == "list":
            # Both names, and the fingerprint of each: the file being there says nothing
            # about *which* CA is in it, and a pre-rename install is still an install.
            present = [path for path in (anchor, legacy) if path.is_file()]
            for path in present:
                self.say(f"[system] {path} ({_which_ca(fingerprint(path), self.sha256)})")
            if not present:
                self.say(f"[system] not installed in {directory}")
            return True
        if action == "remove":
            # The path is ours; the certificate in it need not be. Removing what `--list`
            # would call "another CA" would take out a trust anchor nobody asked about.
            found = {path: fingerprint(path) for path in (anchor, legacy) if path.is_file()}
            ours = [path for path, sha in found.items() if sha == self.sha256]
            for path, sha in found.items():
                if path not in ours:
                    undo = " ".join([*self._sudo, "rm", str(path)])
                    rebuild = " ".join([*self._sudo, tool])
                    self.warn(
                        f"[system] {path}: {_which_ca(sha, self.sha256)}"
                        f" - left alone; take it out with: {undo} && {rebuild}"
                    )
            # An anchor we could not read is not "done" - it may well be this very CA,
            # and it stays behind either way. That it sits beside one we did remove makes
            # no difference, so the exit code does not change either (as in the script).
            readable = all(found.values())
            if not ours:
                if not found:
                    self.say(f"[system] nothing to remove in {directory}")
                return readable
            if not (
                self._change([*self._sudo, "rm", "-f", *(str(path) for path in ours)], "system")
                and self._change([*self._sudo, tool], "system")
            ):
                return False
            self.say(f"[system] removed {' and '.join(str(path) for path in ours)}")
            return readable
        if legacy.is_file() and not self._change([*self._sudo, "rm", "-f", str(legacy)], "system"):
            return False
        install = [*self._sudo, "install", "-m", "0644", str(self.cert), str(anchor)]
        if not (self._change(install, "system") and self._change([*self._sudo, tool], "system")):
            return False
        if not _same_bytes(anchor, self.cert):
            self.warn(f"[system] WARNING: {anchor} does not match {self.cert} after install")
            return False
        self.say(f"[system] installed as {anchor} ({tool})")
        self.say("[system] note: a Flatpak app does not see this - it uses the runtime's bundle")
        return True

    # -- macOS: keychain ----------------------------------------------------------------

    def _macos(self, store: str, action: str) -> bool:
        security = self.which("security")
        if security is None:
            self.warn(f"[{store}] the security tool is missing - is this really macOS?")
            return False
        if store == "system":
            keychain, domain, prefix = MACOS_SYSTEM_KEYCHAIN, ["-d"], self._sudo
        else:
            keychain, domain, prefix = self._login_keychain(security), [], []
        if action == "install":
            add = [*prefix, security, "add-trusted-cert", *domain, "-r", "trustRoot"]
            if not self._change([*add, "-k", keychain, str(self.cert)], store):
                return False
            # Trust settings are what make a root trusted, and verify-cert evaluates them;
            # sitting in a keychain alone does not.
            if self._ask([security, "verify-cert", "-c", str(self.cert)]).returncode == 0:
                self.say(f"[{store}] trusted in {keychain} (read back with security verify-cert)")
                return True
            self.warn(
                f"[{store}] WARNING: added to {keychain}, "
                "but security verify-cert does not trust it"
            )
            return False
        listed = self._ask([security, "find-certificate", "-a", "-Z", keychain]).stdout
        present = f"SHA-1 HASH: {self.sha1}" in listed.upper()
        if action == "list":
            self.say(f"[{store}] {'in' if present else 'not in'} {keychain}")
            return True
        self._ask([*prefix, security, "remove-trusted-cert", *domain, str(self.cert)])
        if not present:
            self.say(f"[{store}] nothing to remove in {keychain}")
            return True
        if not self._change(
            [*prefix, security, "delete-certificate", "-Z", self.sha1, keychain], store
        ):
            return False
        self.say(f"[{store}] removed from {keychain}")
        return True

    def _login_keychain(self, security: str) -> str:
        listed = self._ask([security, "login-keychain"]).stdout.strip().strip('"')
        return listed or str(self.home / "Library" / "Keychains" / "login.keychain-db")

    # -- Windows: certificate store -----------------------------------------------------

    def _windows(self, store: str, action: str) -> bool:
        exe = self._certutil_exe()
        if exe is None:
            self.warn(f"[{store}] certutil.exe not found (it ships with Windows, in System32)")
            return False
        if store == "system":
            scope, where = [], "LocalMachine\\Root"
        else:
            scope, where = ["-user"], "CurrentUser\\Root"

        def listed() -> bool:
            return self._ask([exe, *scope, "-store", "Root", self.sha1]).returncode == 0

        if action == "install":
            if not self._change(
                [exe, *scope, "-addstore", "Root", self._win_path(self.cert)], store
            ):
                if store == "system":
                    self.warn(f"[{store}] LocalMachine needs a shell started as administrator")
                return False
            if listed():
                self.say(f"[{store}] installed in {where} (read back)")
                return True
            self.warn(f"[{store}] WARNING: certutil reported success but {where} does not list it")
            return False
        present = listed()
        if action == "list":
            self.say(f"[{store}] {'in' if present else 'not in'} {where}")
            return True
        if not present:
            self.say(f"[{store}] nothing to remove in {where}")
            return True
        if not self._change([exe, *scope, "-delstore", "Root", self.sha1], store):
            return False
        self.say(f"[{store}] removed from {where}")
        return True

    def _certutil_exe(self) -> str | None:
        if self.native_windows:
            return self.which("certutil")
        found = self.which("certutil.exe")
        if found is None and Path(WSL_CERTUTIL).is_file():
            found = WSL_CERTUTIL  # WSL with appendWindowsPath=false
        return found

    def _win_path(self, path: Path) -> str:
        """certutil.exe wants a Windows path; Cygwin and WSL hand it a POSIX one."""
        if self.native_windows:
            return str(path)
        for tool in ("cygpath", "wslpath"):
            exe = self.which(tool)
            if exe:
                converted = self._ask([exe, "-w", str(path)]).stdout.strip()
                if converted:
                    return converted
        return str(path)

    # -- plumbing -----------------------------------------------------------------------

    @property
    def _sudo(self) -> list[str]:
        return [] if self.root else ["sudo"]

    def _ask(
        self, argv: list[str], timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        """A query: captured, and a tool that cannot run counts as a plain "no"."""
        try:
            return self.run(argv, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(argv, 1, "", str(exc))

    def _change(self, argv: list[str], label: str) -> bool:
        """A command that changes a store; False (and why) when it did not succeed."""
        try:
            done = self.run(argv, show=True)
        except OSError as exc:
            self.warn(f"[{label}] could not run {argv[0]}: {exc}")
            return False
        if done.returncode != 0:
            self.warn(f"[{label}] {' '.join(argv)} exited {done.returncode}")
            return False
        return True

    def say(self, line: str) -> None:
        print(line, file=self.out)

    def warn(self, line: str) -> None:
        print(line, file=self.err)


AFTER_INSTALL: dict[str, tuple[str, ...]] = {
    "linux": (
        "Restart WeChat completely (tray -> 退出), then point it at the proxy",
        "(设置 → 通用设置 → 网络代理). For the webview to use the proxy as well, see",
        "scripts/run-wechat-proxied.sh --persist in a checkout of the repository.",
    ),
    "macos": (
        "Quit WeChat completely (⌘Q, not just the window) and open it again - the",
        "trust settings are read when it starts. Not yet verified with a real WeChat",
        "on macOS (issue #11): if the proxy still logs 'tls alert certificate unknown',",
        "note which process connects (lsof -nP -iTCP:8899) and report it there.",
    ),
    "windows": (
        "Quit WeChat completely (tray -> 退出) and open it again. Not yet verified with",
        "a real WeChat on Windows (issue #11).",
    ),
}


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _which_ca(found: str, ours: str) -> str:
    """How an entry a store already holds is reported: the CA asked about, or another
    one - a CA from before the last `wx-dlh ca` keeps both the nickname and the path."""
    if not found:
        return "could not be read"
    return "this CA" if found == ours else f"another CA, SHA-256 {found}"


def hint(ca_dir: Path, app_id: str = FLATPAK_APP_ID, *, os_name: str | None = None) -> str:
    """What `wx-dlh ca` prints: the CA, and the stores this machine has for it."""
    pem = cert_path(ca_dir)
    lines = [
        f"CA certificate: {pem}",
        f"CA private key: {key_path(ca_dir)}  (keep it here, never commit it)",
    ]
    sha256 = fingerprint(pem)
    if sha256:
        lines.append(f"SHA-256:        {sha256}")
    try:
        trust = Trust(os_name or detect_os(), pem, app_id=app_id)
    except TrustError:
        return "\n".join(lines)
    defaults = trust.default_stores()
    lines += ["", f"Trust stores on this machine ({trust.os}); * = what --install uses by default:"]
    for store in STORES[trust.os]:
        mark = "*" if store in defaults else " "
        lines.append(f"  {mark} --{store:<9} {trust.describe(store)}")
    lines += [
        "",
        "  wx-dlh ca --install            # the * stores",
        "  wx-dlh ca --install --system   # that store instead - a store flag replaces the *",
        "  wx-dlh ca --install --all      # every store above, --system included",
        "  wx-dlh ca --list               # where it is now",
        "  wx-dlh ca --remove             # undo",
    ]
    return "\n".join(lines)
