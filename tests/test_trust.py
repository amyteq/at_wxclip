"""`wx-dlh ca --install/--remove/--list` on each OS, with every command recorded, not run.

The cases are the ones `tests/test_trust_ca.py` holds `scripts/trust-ca.sh` to, because
this module is that script ported to Python. As there, the macOS and Windows branches can
only be checked for what they *ask* the OS to do - the right tool, the right store, sudo
only where it belongs, a read-back after every install. Whether WeChat then accepts the CA
is the question only a real client answers (issue #11); nothing here claims it does.
"""

from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from wx_dlh import ca, trust
from wx_dlh.__main__ import build_parser, main
from wx_dlh.trust import Trust, TrustError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "trust-ca.sh"
LOGIN_KEYCHAIN = "/Users/me/Library/Keychains/login.keychain-db"
#: Every tool any branch looks up; `which` finds these by their bare names, so the
#: recorded commands read the way the script's do.
TOOLS = {
    "certutil", "flatpak", "update-ca-trust", "update-ca-certificates",
    "security", "certutil.exe", "cygpath", "wslpath",
}  # fmt: skip


class FakeSystem:
    """Stands in for `subprocess.run`: records each command as one string and answers
    from `answers` - (needle, returncode, stdout), first needle found in the command
    wins; stdout may be a callable taking the argv, which is how an install "happens"."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.answers: list[tuple[str, int, str | Callable[[list[str]], str]]] = []

    def answer(self, needle: str, returncode: int = 0, stdout="") -> None:
        self.answers.append((needle, returncode, stdout))

    def __call__(self, argv, timeout=None, show=False, **kwargs):
        argv = list(argv)
        joined = " ".join(argv)
        self.calls.append(joined)
        for needle, code, stdout in self.answers:
            if needle in joined:
                text = stdout(argv) if callable(stdout) else stdout
                return subprocess.CompletedProcess(argv, code, text, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def ran(self, prefix: str) -> list[str]:
        return [c for c in self.calls if c.startswith(prefix)]


@pytest.fixture(scope="module")
def cert(tmp_path_factory) -> tuple[Path, str]:
    """A real CA the way `wx-dlh ca` makes it, and its SHA-1 as the stores show it -
    worked out by `cryptography`, not by the code under test."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    pem = ca.ensure_ca(tmp_path_factory.mktemp("ca"))
    loaded = x509.load_pem_x509_certificate(pem.read_bytes())
    return pem, loaded.fingerprint(hashes.SHA1()).hex().upper()


@pytest.fixture(scope="module")
def stale(tmp_path_factory) -> str:
    """A second CA: what a store still holds after `wx-dlh ca` minted a new one."""
    return ca.ensure_ca(tmp_path_factory.mktemp("stale")).read_text()


def _nssdb(db: Path) -> Path:
    """A NSS database that exists as far as this code looks - nothing here reads it."""
    db.mkdir(parents=True, exist_ok=True)
    (db / "cert9.db").touch()
    return db


@pytest.fixture
def make(tmp_path, cert):
    """Build a Trust for an OS; returns it with its FakeSystem and its captured output."""

    def build(os_name: str = "linux", *, hide: tuple[str, ...] = (), **kwargs):
        system = FakeSystem()
        system.answer("login-keychain", stdout=f'    "{LOGIN_KEYCHAIN}"\n')
        tools = TOOLS - set(hide)
        out, err = io.StringIO(), io.StringIO()
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        options = {"native_windows": False, "anchor_dirs": (), **kwargs}
        t = Trust(
            os_name, cert[0], home=home, run=system,
            which=lambda name: name if name in tools else None,
            out=out, err=err, **options,
        )  # fmt: skip
        return t, system, lambda: out.getvalue() + err.getvalue()

    return build


# --- which OS, which stores ------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "wsl", "expected"),
    [
        ("darwin", False, "macos"),
        ("win32", False, "windows"),
        ("cygwin", False, "windows"),
        ("msys", False, "windows"),
        ("linux", True, "windows"),  # WSL: WeChat runs on the Windows host
        ("linux", False, "linux"),
    ],
)
def test_the_os_is_detected(platform, wsl, expected):
    assert trust.detect_os(platform, wsl=wsl) == expected


def test_an_unknown_os_says_how_to_override():
    with pytest.raises(TrustError, match="--os"):
        trust.detect_os("freebsd14", wsl=False)


def test_wsl_is_recognised_by_its_environment_or_its_kernel(tmp_path):
    assert trust.is_wsl({"WSL_DISTRO_NAME": "Ubuntu"}, tmp_path / "missing")
    release = tmp_path / "osrelease"
    release.write_text("5.15.153.1-microsoft-standard-WSL2\n")
    assert trust.is_wsl({}, release)
    release.write_text("6.10.0-arch1-1\n")
    assert not trust.is_wsl({}, release)


@pytest.mark.parametrize(("os_name", "default"), [("macos", "keychain"), ("windows", "windows")])
def test_the_default_store_follows_the_os(make, os_name, default):
    t, _, _ = make(os_name)
    assert t.stores() == [default]


def test_linux_defaults_to_the_nss_databases_that_exist(make):
    t, system, _ = make("linux")
    system.answer("flatpak info", returncode=1)
    with pytest.raises(TrustError, match="no NSS database found"):
        t.stores()
    t.user_db.mkdir(parents=True)
    assert t.stores() == ["user"]
    system.answers.clear()  # now `flatpak info` succeeds: WeChat is installed as a Flatpak
    assert t.stores() == ["flatpak", "user"]


def test_a_store_from_another_os_is_refused(make):
    t, _, _ = make("linux")
    with pytest.raises(TrustError, match="--keychain is not a store on linux"):
        t.stores(["keychain"])
    t, _, _ = make("macos")
    with pytest.raises(TrustError, match="--keychain --system"):
        t.stores(["flatpak"])


def test_all_means_every_store_once(make):
    t, _, _ = make("windows")
    assert t.stores(["system", "all"]) == ["system", "windows"]


def test_running_under_sudo_is_refused(cert):
    with pytest.raises(TrustError, match="without sudo"):
        Trust.for_this_machine(cert[0], os_name="macos", euid=0, env={"SUDO_USER": "me"})
    # WSL is "windows" - the host's store is the one WeChat reads - but it is POSIX, and
    # sudo there is sudo: root's HOME would pick the wrong CA and the wrong user stores.
    with pytest.raises(TrustError, match="without sudo"):
        Trust.for_this_machine(cert[0], os_name="windows", euid=0, env={"SUDO_USER": "me"})
    # root without sudo (a container) is fine, and needs no sudo in front of anything
    assert Trust.for_this_machine(cert[0], os_name="linux", euid=0, env={}).root
    # Windows' own Python has no geteuid (-1); an elevated shell is how LocalMachine is
    # reached, and it is not sudo
    assert not Trust.for_this_machine(
        cert[0], os_name="windows", euid=-1, env={"SUDO_USER": "me"}
    ).root


def test_a_missing_certificate_says_how_to_make_one(make, tmp_path):
    t, system, _ = make("macos")
    t.cert = tmp_path / "nope.pem"
    for action in ("install", "list"):  # listing too: a store is searched by fingerprint
        with pytest.raises(TrustError, match="wx-dlh ca"):
            t.apply(action, ["keychain"])
    assert not system.calls


def test_the_file_with_the_private_key_in_it_is_refused(make, cert, tmp_path):
    """`--cert` takes a path, and `mitmproxy-ca.pem` sits next to `mitmproxy-ca-cert.pem`
    looking just as plausible - but it is the certificate *and* the CA's private key.
    `--system` installs what it is given as a 0644 trust anchor, so this never gets that
    far: no store is touched, and the file is not copied anywhere."""
    t, system, _ = make("linux", anchor_dirs=_anchors(tmp_path))
    t.cert = ca.key_path(cert[0].parent)
    assert "PRIVATE KEY" in t.cert.read_text()
    for action in ("install", "remove", "list"):
        with pytest.raises(TrustError, match="private key"):
            t.apply(action, ["system", "user"])
    assert not system.calls
    assert not list((tmp_path / "anchors").iterdir())


def test_a_certificate_that_is_not_one_is_refused(make, tmp_path):
    t, system, _ = make("macos")
    t.cert = tmp_path / "junk.pem"
    t.cert.write_text("not a certificate\n")
    with pytest.raises(TrustError, match="BEGIN CERTIFICATE"):
        t.apply("install", ["keychain"])
    assert not system.calls


# --- macOS ---------------------------------------------------------------------------


def test_macos_default_trusts_in_the_login_keychain_without_sudo(make, cert):
    t, system, output = make("macos")
    assert t.apply("install", t.stores())
    assert f"security add-trusted-cert -r trustRoot -k {LOGIN_KEYCHAIN} {cert[0]}" in system.calls
    assert f"security verify-cert -c {cert[0]}" in system.calls  # read back
    assert not system.ran("sudo")
    assert "⌘Q" in output()


def test_macos_system_uses_the_admin_domain_and_sudo(make, cert):
    t, system, _ = make("macos")
    assert t.apply("install", ["system"])
    assert (
        f"sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "
        f"{cert[0]}" in system.calls
    )
    assert not [c for c in system.calls if "update-ca-trust" in c]  # the bug reported in #11


def test_macos_as_root_does_not_put_sudo_in_front(make):
    t, system, _ = make("macos", root=True)
    assert t.apply("install", ["system"])
    assert system.ran("security add-trusted-cert -d")
    assert not system.ran("sudo")


def test_macos_all_means_both_keychains(make):
    t, _, output = make("macos")
    assert t.apply("install", t.stores(["all"]))
    assert "[keychain] trusted" in output()
    assert "[system] trusted" in output()


def test_macos_says_so_when_the_keychain_does_not_trust_it_afterwards(make):
    t, system, output = make("macos")
    system.answer("verify-cert", returncode=1)
    assert not t.apply("install", ["keychain"])
    assert "WARNING" in output()
    assert "does not trust it" in output()


def test_macos_remove_finds_the_certificate_by_fingerprint(make, cert):
    t, system, _ = make("macos")
    system.answer("find-certificate", stdout=f"SHA-1 hash: {cert[1]}\n")
    assert t.apply("remove", ["keychain"])
    assert f"security remove-trusted-cert {cert[0]}" in system.calls
    assert f"security delete-certificate -Z {cert[1]} {LOGIN_KEYCHAIN}" in system.calls


def test_macos_remove_with_nothing_there_deletes_nothing(make):
    t, system, output = make("macos")
    system.answer("find-certificate", stdout="SHA-1 hash: 0000\n")
    assert t.apply("remove", ["keychain"])
    assert "nothing to remove" in output()
    assert not [c for c in system.calls if "delete-certificate" in c]


def test_macos_list_reports_by_fingerprint(make, cert):
    t, system, output = make("macos")
    system.answer("find-certificate", stdout=f"SHA-1 hash: {cert[1].lower()}\n")
    assert t.apply("list", ["keychain"])
    assert f"[keychain] in {LOGIN_KEYCHAIN}" in output()


# --- Windows / WSL -------------------------------------------------------------------


def test_windows_default_is_the_current_user_root_store(make, cert):
    t, system, output = make("windows")
    win = "C:\\Users\\me" + str(cert[0]).replace("/", "\\")
    system.answer("cygpath -w", stdout=win + "\n")
    assert t.apply("install", t.stores())
    assert f"certutil.exe -user -addstore Root {win}" in system.calls
    assert f"certutil.exe -user -store Root {cert[1]}" in system.calls  # read back
    assert "CurrentUser\\Root (read back)" in output()


def test_native_windows_python_needs_no_path_translation(make, cert):
    t, system, _ = make("windows", native_windows=True)
    assert t.apply("install", ["windows"])
    assert f"certutil -user -addstore Root {cert[0]}" in system.calls
    assert not [c for c in system.calls if "cygpath" in c or "wslpath" in c]


def test_windows_system_is_local_machine(make):
    t, system, _ = make("windows")
    assert t.apply("install", ["system"])
    assert system.ran("certutil.exe -addstore Root ")
    assert not system.ran("sudo")


def test_windows_system_without_admin_says_what_is_needed(make):
    t, system, output = make("windows")
    system.answer("-addstore", returncode=5)
    assert not t.apply("install", ["system"])
    assert "administrator" in output()


def test_wsl_installs_into_the_host_store_through_wslpath(make, cert):
    t, system, _ = make("windows", hide=("cygpath",))
    win = "\\\\wsl.localhost\\Ubuntu" + str(cert[0]).replace("/", "\\")
    system.answer("wslpath -w", stdout=win + "\n")
    assert t.apply("install", t.stores())
    assert f"wslpath -w {cert[0]}" in system.calls
    assert f"certutil.exe -user -addstore Root {win}" in system.calls


def test_windows_says_so_when_the_store_does_not_list_it_afterwards(make):
    t, system, output = make("windows")
    system.answer("-store Root", returncode=1)
    assert not t.apply("install", ["windows"])
    assert "does not list it" in output()


def test_windows_remove_deletes_by_fingerprint(make, cert):
    t, system, _ = make("windows")
    assert t.apply("remove", ["windows"])
    assert f"certutil.exe -user -delstore Root {cert[1]}" in system.calls


def test_windows_without_certutil_says_so(make):
    t, _, output = make("windows", hide=("certutil.exe",))
    assert not t.apply("list", ["windows"])
    assert "certutil.exe not found" in output()


# --- Linux ---------------------------------------------------------------------------


def test_linux_nss_replaces_the_old_nickname_and_reads_back(make, cert):
    t, system, output = make("linux")
    assert t.apply("install", ["user"])
    db = t.user_db
    assert f"certutil -d sql:{db} -N --empty-password" in system.calls  # a fresh database
    assert f"certutil -d sql:{db} -D -n wxclip-ca" in system.calls
    assert f"certutil -d sql:{db} -A -t C,, -n wx-dlh-ca -i {cert[0]}" in system.calls
    assert f"certutil -d sql:{db} -L -n wx-dlh-ca" in system.calls
    assert "(read back)" in output()


def test_linux_nss_says_so_when_the_database_does_not_list_it(make):
    t, system, output = make("linux")
    system.answer("-L -n wx-dlh-ca", returncode=255)
    assert not t.apply("install", ["user"])
    assert "does not list it" in output()


def test_linux_nss_without_certutil_is_skipped_not_hidden(make):
    t, system, output = make("linux", hide=("certutil",))
    assert not t.apply("install", ["user"])
    assert "certutil not found" in output()
    assert not system.calls


def test_linux_nss_list_shows_our_entries_only(make, cert):
    t, system, output = make("linux")
    _nssdb(t.user_db)
    system.answer("-L -n wx-dlh-ca -a", stdout=cert[0].read_text())
    system.answer(
        "-L",
        stdout="wx-dlh-ca                    C,,\nmitmproxy                    C,,\n",
    )
    assert t.apply("list", ["user"])
    assert "  wx-dlh-ca" in output()
    assert "(this CA)" in output()
    assert "\n  mitmproxy" not in output()


def test_linux_nss_list_tells_our_ca_from_the_one_that_is_in_there(make, stale):
    """The nickname outlives the certificate: `wx-dlh ca` can mint a new CA while the
    store still holds the old one under the same name. Only the fingerprint says which."""
    t, system, output = make("linux")
    _nssdb(t.user_db)
    system.answer("-L -n wx-dlh-ca -a", stdout=stale)
    system.answer("-L", stdout="wx-dlh-ca                    C,,\n")
    assert t.apply("list", ["user"])
    assert "another CA, SHA-256 " in output()
    assert "(this CA)" not in output()


def test_linux_nss_does_not_read_a_database_it_cannot_open_as_empty(make):
    """certutil failing is not "our CA is not in there": a locked, corrupt or
    password-protected database would otherwise list clean and `--remove` would report
    an undo that undid nothing, both with exit 0."""
    t, system, output = make("linux")
    _nssdb(t.user_db)
    system.answer("-L", returncode=255, stdout="certutil: SEC_ERROR_BAD_DATABASE")
    assert not t.apply("list", ["user"])
    assert not t.apply("remove", ["user"])
    assert output().count("cannot read") == 2
    assert not [c for c in system.calls if " -D " in c]


def test_linux_nss_remove_takes_out_both_nicknames_and_nothing_else(make):
    t, system, output = make("linux")
    db = _nssdb(t.user_db)
    system.answer("-L", stdout="wxclip-ca    C,,\nwx-dlh-ca    C,,\nmitmproxy    C,,\n")
    assert t.apply("remove", ["user"])
    assert f"certutil -d sql:{db} -D -n wx-dlh-ca" in system.calls
    assert f"certutil -d sql:{db} -D -n wxclip-ca" in system.calls
    assert not [c for c in system.calls if "-D -n mitmproxy" in c]
    assert "removed wxclip-ca and wx-dlh-ca" in output()


def test_linux_nss_remove_that_did_not_remove_is_not_success(make):
    t, system, output = make("linux")
    _nssdb(t.user_db)
    system.answer("-L", stdout="wx-dlh-ca                    C,,\n")
    system.answer("-D -n", returncode=255)
    assert not t.apply("remove", ["user"])
    assert "nothing to remove" not in output()


def test_linux_nss_remove_without_a_database_has_nothing_to_remove(make):
    t, system, output = make("linux")
    assert t.apply("remove", ["user"])
    assert "nothing to remove" in output()
    assert not system.calls


def test_flatpak_persists_pki_so_the_app_can_see_the_database(make):
    t, system, output = make("linux")
    assert t.apply("install", ["flatpak"])
    assert f"certutil -d sql:{t.flatpak_db} -A" in " ".join(system.calls)
    assert "flatpak override --user com.tencent.WeChat --persist=.pki" in system.calls
    assert system.ran("flatpak run --command=sh com.tencent.WeChat -c")
    assert "the app can see the database" in output()


def test_flatpak_warns_when_the_app_still_cannot_see_it(make):
    t, system, output = make("linux")
    system.answer("flatpak run", returncode=1)
    assert t.apply("install", ["flatpak"])  # installed; the warning is about visibility
    assert "still cannot see" in output()


def _anchors(tmp_path: Path, tool: str = "update-ca-trust") -> tuple[tuple[str, str], ...]:
    directory = tmp_path / "anchors"
    directory.mkdir(exist_ok=True)
    return ((str(tmp_path / "absent"), "update-ca-trust"), (str(directory), tool))


def _really_install(argv: list[str]) -> str:
    Path(argv[-1]).write_bytes(Path(argv[-2]).read_bytes())
    return ""


def test_linux_system_installs_where_this_distro_keeps_anchors(make, cert, tmp_path):
    t, system, output = make("linux", anchor_dirs=_anchors(tmp_path, "update-ca-certificates"))
    system.answer("install -m 0644", stdout=_really_install)
    assert t.apply("install", ["system"])
    anchor = tmp_path / "anchors" / "wx-dlh-ca.crt"
    assert f"sudo install -m 0644 {cert[0]} {anchor}" in system.calls
    assert "sudo update-ca-certificates" in system.calls
    assert f"installed as {anchor}" in output()


def test_linux_system_says_so_when_the_anchor_does_not_match(make, tmp_path):
    t, _, output = make("linux", anchor_dirs=_anchors(tmp_path))
    assert not t.apply("install", ["system"])  # sudo is only recorded: nothing was copied
    assert "does not match" in output()


def test_linux_system_clears_the_old_name_first(make, tmp_path):
    t, system, _ = make("linux", anchor_dirs=_anchors(tmp_path))
    legacy = tmp_path / "anchors" / "wxclip-ca.crt"
    legacy.write_text("old")
    system.answer("install -m 0644", stdout=_really_install)
    assert t.apply("install", ["system"])
    assert f"sudo rm -f {legacy}" in system.calls


def test_linux_system_remove_rebuilds_the_bundle(make, tmp_path):
    t, system, output = make("linux", anchor_dirs=_anchors(tmp_path))
    (tmp_path / "anchors" / "wx-dlh-ca.crt").write_text("x")
    assert t.apply("remove", ["system"])
    assert system.ran("sudo rm -f ")
    assert "sudo update-ca-trust" in system.calls
    assert "[system] removed" in output()


def test_linux_system_list_names_the_anchor_it_found_and_whose_it_is(make, cert, stale, tmp_path):
    """The anchor's path says nothing about which CA is in it, and the pre-rename name is
    still an install: both files are looked at, and each one by fingerprint."""
    t, _, output = make("linux", anchor_dirs=_anchors(tmp_path))
    anchors = tmp_path / "anchors"
    assert t.apply("list", ["system"])
    assert f"not installed in {anchors}" in output()
    (anchors / "wxclip-ca.crt").write_text(stale)
    (anchors / "wx-dlh-ca.crt").write_text(cert[0].read_text())
    t, _, output = make("linux", anchor_dirs=_anchors(tmp_path))
    assert t.apply("list", ["system"])
    assert f"{anchors / 'wx-dlh-ca.crt'} (this CA)" in output()
    assert f"{anchors / 'wxclip-ca.crt'} (another CA, SHA-256 " in output()


def test_linux_system_without_a_known_layout_says_so(make):
    t, system, output = make("linux")
    assert not t.apply("install", ["system"])
    assert "no supported trust-anchor directory" in output()
    assert not system.calls


def test_one_failing_store_does_not_stop_the_others(make):
    t, system, output = make("linux")
    system.answer("-L -n wx-dlh-ca", returncode=255)
    assert not t.apply("install", ["flatpak", "user"])
    assert output().count("does not list it") == 2


# --- what `wx-dlh ca` prints ---------------------------------------------------------


def test_hint_points_at_the_cert_and_the_subcommand(tmp_path):
    out = trust.hint(tmp_path, os_name="macos")
    assert str(ca.cert_path(tmp_path)) in out
    assert "wx-dlh ca --install" in out
    assert "* --keychain" in out


def test_hint_on_linux_names_the_flatpak_sandbox(tmp_path):
    assert "com.example.App" in trust.hint(tmp_path, "com.example.App", os_name="linux")


def test_hint_does_not_call_a_store_flag_additive(tmp_path):
    """`--system` selects that store, it does not add it to the defaults - and on Flatpak
    Linux the defaults are the only stores the app can see. `--all` is the additive one."""
    out = trust.hint(tmp_path, os_name="linux")
    assert "--install --system   # that store instead" in out
    assert "--install --all      # every store above" in out


# --- the CLI -------------------------------------------------------------------------


@pytest.fixture
def applied(monkeypatch):
    """Record what `Trust.apply` was asked to do instead of doing it."""
    seen: dict = {}

    def fake_apply(self, action, stores):
        seen.update(action=action, stores=list(stores), cert=self.cert, os=self.os)
        return seen.get("ok", True)

    monkeypatch.setattr(Trust, "apply", fake_apply)
    return seen


def test_install_creates_the_ca_and_hands_it_over(tmp_path, applied):
    ca_dir = tmp_path / "ca"
    assert main(["ca", "--install", "--system", "--os", "macos", "--ca-dir", str(ca_dir)]) == 0
    assert applied == {
        "action": "install", "stores": ["system"], "cert": ca.cert_path(ca_dir), "os": "macos",
    }  # fmt: skip
    assert ca.cert_path(ca_dir).is_file()


def test_list_and_remove_never_mint_a_ca(tmp_path, applied):
    ca_dir = tmp_path / "ca"
    assert main(["ca", "--list", "--os", "windows", "--ca-dir", str(ca_dir)]) == 0
    assert applied["action"] == "list"
    assert not ca_dir.exists()


def test_a_failing_store_is_exit_1(tmp_path, applied):
    applied["ok"] = False
    assert main(["ca", "--install", "--os", "macos", "--ca-dir", str(tmp_path / "ca")]) == 1


def test_a_store_flag_without_an_action_is_an_error(capsys):
    assert main(["ca", "--system"]) == 1
    assert "needs --install, --remove or --list" in capsys.readouterr().err


def test_actions_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ca", "--install", "--remove"])


def test_a_refusal_comes_before_any_ca_is_made(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trust.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setenv("SUDO_USER", "me")
    ca_dir = tmp_path / "ca"
    assert main(["ca", "--install", "--os", "linux", "--ca-dir", str(ca_dir)]) == 1
    assert "without sudo" in capsys.readouterr().err
    monkeypatch.delenv("SUDO_USER")  # plain root, as in a container: past that check
    assert main(["ca", "--install", "--keychain", "--os", "linux", "--ca-dir", str(ca_dir)]) == 1
    assert "--keychain is not a store" in capsys.readouterr().err
    assert not ca_dir.exists()


def test_the_cli_refuses_the_combined_key_file_before_it_runs_anything(cert, capsys):
    """Nothing is monkeypatched here: `--cert mitmproxy-ca.pem` has to be stopped by the
    real code path, before a single store command is built."""
    both = ca.key_path(cert[0].parent)
    assert main(["ca", "--install", "--os", "macos", "--cert", str(both)]) == 1
    assert "private key" in capsys.readouterr().err


def test_a_refusal_is_reported_not_raised(cert, capsys):
    assert main(["ca", "--install", "--keychain", "--os", "linux", "--cert", str(cert[0])]) == 1
    assert "--keychain is not a store on linux" in capsys.readouterr().err


# --- in step with scripts/trust-ca.sh ------------------------------------------------


def test_the_subcommand_takes_every_flag_the_script_does():
    usage = SCRIPT.read_text().split("usage() {", 1)[1].split("USAGE\n}", 1)[0]
    script_flags = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", usage))
    ca_parser = build_parser()._subparsers._group_actions[0].choices["ca"]
    ours = {flag for action in ca_parser._actions for flag in action.option_strings}
    assert script_flags - ours == set()


def test_the_script_uses_the_same_names_and_places():
    text = SCRIPT.read_text()
    assert f'NICKNAME="{trust.NICKNAME}"' in text
    assert f'LEGACY_NICKNAME="{trust.LEGACY_NICKNAME}"' in text
    for directory, tool in trust.ANCHOR_DIRS:
        assert directory in text
        assert tool in text
    assert trust.MACOS_SYSTEM_KEYCHAIN in text
    assert trust.WSL_CERTUTIL in text
