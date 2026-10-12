"""scripts/trust-ca.sh on each OS, with the platform tools replaced by recording stubs.

This machine is Linux, so the macOS and Windows branches can only be checked for what
they *ask* the OS to do - the right tool, the right store, sudo only where it belongs,
and a read-back after every install. Whether WeChat then accepts the CA is the question
only a real client answers (issue #11); nothing here claims it does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "trust-ca.sh"
#: Point this at another bash (macOS ships 3.2) to check the script still parses there.
BASH = os.environ.get("TRUST_CA_BASH", "bash")

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None or shutil.which(BASH) is None,
    reason="needs bash and openssl",
)

# Each stub appends "<name> <args>" to $STUB_LOG; behaviour that a test needs to vary
# comes from STUB_* variables so one set of stubs serves every case.
STUBS = {
    "uname": 'echo "${STUB_UNAME:-Linux}"',
    "id": 'echo "${STUB_UID:-1000}"',  # the script only asks for -u
    "sudo": "",  # recorded, never run: nothing here may touch the real system
    "security": """case "$1" in
  login-keychain) echo '    "/Users/me/Library/Keychains/login.keychain-db"' ;;
  verify-cert) exit "${STUB_VERIFY:-0}" ;;
  find-certificate) [ "${STUB_FIND:-0}" = 1 ] && echo "SHA-1 hash: $STUB_SHA1" ;;
esac
exit 0""",
    "certutil.exe": 'case " $* " in *" -store "*) exit "${STUB_STORE:-0}" ;; esac',
    "cygpath": 'echo "C:\\\\Users\\\\me$(echo "$2" | tr / \\\\\\\\)"',
    "wslpath": 'echo "\\\\\\\\wsl.localhost\\\\Ubuntu$(echo "$2" | tr / \\\\\\\\)"',
    # STUB_NSS_LIST is what `-L` shows, STUB_NSS_PEM the file `-L -n X -a` prints, and
    # STUB_NSS_L the exit code of both - how a database certutil cannot open behaves.
    "certutil": """case " $* " in
  *" -L "*" -a "*) [ -z "${STUB_NSS_PEM:-}" ] || cat "$STUB_NSS_PEM" ;;
  *" -L "*) [ -z "${STUB_NSS_LIST:-}" ] || printf '%s\\n' "$STUB_NSS_LIST" ;;
  *) exit 0 ;;
esac
exit "${STUB_NSS_L:-0}"
""",
    "flatpak": 'case "$1" in info) exit 1 ;; esac',
    "update-ca-trust": "",
    "update-ca-certificates": "",
}


def _make_cert(where: Path) -> tuple[Path, str]:
    """A throwaway self-signed CA named like mitmproxy's, plus its SHA-1 as stores show
    it. The private key stays next to it as `key.pem`, as mitmproxy's own CA does."""
    pem = where / "mitmproxy-ca-cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-keyout", str(where / "key.pem"), "-out", str(pem),
         "-subj", "/CN=mitmproxy/O=mitmproxy", "-days", "1"],
        check=True, capture_output=True,
    )  # fmt: skip
    out = subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-noout", "-fingerprint", "-sha1"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return pem, out.strip().partition("=")[2].replace(":", "")


@pytest.fixture(scope="module")
def cert(tmp_path_factory) -> tuple[Path, str]:
    return _make_cert(tmp_path_factory.mktemp("ca"))


@pytest.fixture(scope="module")
def stale(tmp_path_factory) -> Path:
    """A second CA: what a store still holds after `wx-dlh ca` minted a new one."""
    return _make_cert(tmp_path_factory.mktemp("stale"))[0]


@pytest.fixture
def run(tmp_path, cert):
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, body in STUBS.items():
        stub = stubs / name
        stub.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$STUB_LOG"\n{body}\n')
        stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.log"

    def invoke(
        *args: str, cert_arg: bool = True, hide: tuple[str, ...] = (), **env: str
    ) -> tuple[int, str, list[str]]:
        for name in hide:
            (stubs / name).unlink(missing_ok=True)
        log.write_text("")
        full_env = {
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "STUB_LOG": str(log),
            "STUB_SHA1": cert[1],
            **env,
        }
        argv = [BASH, str(SCRIPT), *args]
        if cert_arg:
            argv += ["--cert", str(cert[0])]
        done = subprocess.run(argv, env=full_env, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr, log.read_text().splitlines()

    return invoke


@pytest.mark.parametrize(
    ("uname", "env", "os_name"),
    [
        ("Darwin", {}, "macos"),
        ("MINGW64_NT-10.0-22631", {}, "windows"),
        ("MSYS_NT-10.0", {}, "windows"),
        ("CYGWIN_NT-10.0", {}, "windows"),
        ("Linux", {"WSL_DISTRO_NAME": "Ubuntu"}, "windows"),
    ],
)
def test_the_os_is_detected_and_the_default_store_follows(run, uname, env, os_name):
    code, out, _ = run("--list", STUB_UNAME=uname, **env)
    assert code == 0, out
    expected = {"macos": "[keychain]", "windows": "[windows]"}[os_name]
    assert expected in out


def test_install_can_be_spelled_out_like_wx_dlh_ca_install(run):
    code, out, calls = run("--install", STUB_UNAME="Darwin")
    assert code == 0, out
    assert [c for c in calls if c.startswith("security add-trusted-cert")]


def test_plain_linux_is_linux(run):
    code, out, _ = run("--list", "--user", STUB_UNAME="Linux")
    assert code == 0, out
    assert "[user]" in out


def test_a_store_from_another_os_is_refused(run):
    code, out, _ = run("--keychain", STUB_UNAME="Linux")
    assert code == 1
    assert "--keychain is not a store on linux" in out
    code, out, _ = run("--flatpak", STUB_UNAME="Darwin")
    assert code == 1
    assert "--keychain --system" in out


def test_running_the_whole_script_under_sudo_is_refused(run):
    code, out, calls = run("--system", STUB_UNAME="Darwin", STUB_UID="0", SUDO_USER="me")
    assert code == 1
    assert "without sudo" in out
    assert not [c for c in calls if c.startswith("security add")]


def test_sudo_is_refused_under_wsl_too(run):
    """WSL is "windows" here because WeChat runs on the host, but it is POSIX: sudo there
    is still sudo, and root's HOME would mean a different CA from the proxy's."""
    code, out, calls = run(
        STUB_UNAME="Linux", WSL_DISTRO_NAME="Ubuntu", STUB_UID="0", SUDO_USER="me"
    )
    assert code == 1
    assert "without sudo" in out
    assert not [c for c in calls if c.startswith("certutil.exe")]


def test_an_elevated_git_bash_is_not_sudo(run):
    """MSYS/Cygwin have no sudo; `id -u` can read 0 in an elevated shell, and that is how
    LocalMachine is reached. Only the kernel tells the two apart."""
    code, out, calls = run(STUB_UNAME="MINGW64_NT-10.0", STUB_UID="0", SUDO_USER="me")
    assert code == 0, out
    assert [c for c in calls if c.startswith("certutil.exe -user -addstore")]


def test_the_file_with_the_private_key_in_it_is_refused(run, tmp_path, cert):
    """--cert takes a path, and mitmproxy-ca.pem looks as plausible as -ca-cert.pem next
    to it - but --system installs what it is given as a 0644 trust anchor."""
    both = tmp_path / "mitmproxy-ca.pem"
    both.write_text((cert[0].parent / "key.pem").read_text() + cert[0].read_text())
    code, out, calls = run("--cert", str(both), cert_arg=False, STUB_UNAME="Darwin")
    assert code == 1
    assert "private key" in out
    assert not [c for c in calls if c.startswith("security")]


# --- macOS ---------------------------------------------------------------------------


def test_macos_default_trusts_in_the_login_keychain_without_sudo(run, cert):
    code, out, calls = run(STUB_UNAME="Darwin")
    assert code == 0, out
    assert (
        f"security add-trusted-cert -r trustRoot -k /Users/me/Library/Keychains/"
        f"login.keychain-db {cert[0]}" in calls
    )
    assert f"security verify-cert -c {cert[0]}" in calls  # read back
    assert not [c for c in calls if c.startswith("sudo")]
    assert "⌘Q" in out


def test_macos_system_uses_the_admin_domain_and_sudo(run, cert):
    code, out, calls = run("--system", STUB_UNAME="Darwin")
    assert code == 0, out
    assert (
        f"sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "
        f"{cert[0]}" in calls
    )
    assert not [c for c in calls if "update-ca-trust" in c]  # the bug reported in #11


def test_macos_all_means_both_keychains(run):
    code, out, calls = run("--all", STUB_UNAME="Darwin")
    assert code == 0, out
    assert "[keychain] trusted" in out
    assert "[system] trusted" in out


def test_macos_says_so_when_the_keychain_does_not_trust_it_afterwards(run):
    code, out, _ = run(STUB_UNAME="Darwin", STUB_VERIFY="1")
    assert code == 1
    assert "WARNING" in out
    assert "does not trust it" in out


def test_macos_remove_finds_the_certificate_by_fingerprint(run, cert):
    code, out, calls = run("--remove", STUB_UNAME="Darwin", STUB_FIND="1")
    assert code == 0, out
    assert f"security remove-trusted-cert {cert[0]}" in calls
    kc = "/Users/me/Library/Keychains/login.keychain-db"
    assert f"security delete-certificate -Z {cert[1]} {kc}" in calls


def test_macos_remove_with_nothing_there_deletes_nothing(run):
    code, out, calls = run("--remove", STUB_UNAME="Darwin", STUB_FIND="0")
    assert code == 0, out
    assert "nothing to remove" in out
    assert not [c for c in calls if "delete-certificate" in c]


# --- Windows / WSL -------------------------------------------------------------------


def test_windows_default_is_the_current_user_root_store(run, cert):
    code, out, calls = run(STUB_UNAME="MINGW64_NT-10.0")
    assert code == 0, out
    win = "C:\\Users\\me" + str(cert[0]).replace("/", "\\")
    assert f"certutil.exe -user -addstore Root {win}" in calls
    assert f"certutil.exe -user -store Root {cert[1]}" in calls  # read back
    assert "CurrentUser\\Root (read back)" in out


def test_windows_system_is_local_machine(run, cert):
    code, out, calls = run("--system", STUB_UNAME="MINGW64_NT-10.0")
    assert code == 0, out
    assert [c for c in calls if c.startswith("certutil.exe -addstore Root ")]
    assert not [c for c in calls if c.startswith("sudo")]


def test_wsl_installs_into_the_host_store_through_wslpath(run, cert):
    code, out, calls = run(STUB_UNAME="Linux", WSL_DISTRO_NAME="Ubuntu", hide=("cygpath",))
    assert code == 0, out
    assert f"wslpath -w {cert[0]}" in calls
    win = "\\\\wsl.localhost\\Ubuntu" + str(cert[0]).replace("/", "\\")
    assert f"certutil.exe -user -addstore Root {win}" in calls


def test_windows_says_so_when_the_store_does_not_list_it_afterwards(run):
    code, out, _ = run(STUB_UNAME="MINGW64_NT-10.0", STUB_STORE="1")
    assert code == 1
    assert "does not list it" in out


def test_windows_remove_deletes_by_fingerprint(run, cert):
    code, out, calls = run("--remove", STUB_UNAME="MINGW64_NT-10.0")
    assert code == 0, out
    assert f"certutil.exe -user -delstore Root {cert[1]}" in calls


# --- Linux ---------------------------------------------------------------------------


def test_linux_nss_replaces_the_old_nickname_and_reads_back(run, cert, tmp_path):
    code, out, calls = run("--user", "--os", "linux")
    assert code == 0, out
    db = tmp_path / "home" / ".pki" / "nssdb"
    assert f"certutil -d sql:{db} -D -n wxclip-ca" in calls
    assert f"certutil -d sql:{db} -A -t C,, -n wx-dlh-ca -i {cert[0]}" in calls
    assert f"certutil -d sql:{db} -L -n wx-dlh-ca" in calls
    assert "(read back)" in out


def _nssdb(tmp_path: Path) -> Path:
    """The user database as far as this script looks at it - nothing here reads it."""
    db = tmp_path / "home" / ".pki" / "nssdb"
    db.mkdir(parents=True, exist_ok=True)
    (db / "cert9.db").write_text("")
    return db


def test_linux_nss_list_says_which_ca_sits_under_the_nickname(run, tmp_path, cert, stale):
    """The nickname outlives the certificate: `wx-dlh ca` can mint a new CA while the
    store still holds the old one under the same name. Only the fingerprint says which."""
    _nssdb(tmp_path)
    listed = "wx-dlh-ca                    C,,"
    code, out, _ = run(
        "--list", "--user", "--os", "linux", STUB_NSS_LIST=listed, STUB_NSS_PEM=str(cert[0])
    )
    assert code == 0, out
    assert "wx-dlh-ca" in out
    assert "(this CA)" in out
    code, out, _ = run(
        "--list", "--user", "--os", "linux", STUB_NSS_LIST=listed, STUB_NSS_PEM=str(stale)
    )
    assert code == 0, out
    assert "another CA, SHA-256 " in out


def test_a_database_certutil_cannot_open_is_not_an_empty_one(run, tmp_path):
    """Locked, corrupt, password-protected: reading that as "our CA is not in there"
    would make --remove report an undo that undid nothing, with exit 0."""
    _nssdb(tmp_path)
    for action in ("--list", "--remove"):
        code, out, calls = run(action, "--user", "--os", "linux", STUB_NSS_L="255")
        assert code == 1
        assert "cannot read" in out
        assert not [c for c in calls if " -D " in c]


KNOWN_ANCHORS = {
    "/etc/ca-certificates/trust-source/anchors": "update-ca-trust",
    "/etc/pki/ca-trust/source/anchors": "update-ca-trust",
    "/usr/local/share/ca-certificates": "update-ca-certificates",
    "/etc/pki/trust/anchors": "update-ca-certificates",
}


def test_linux_system_installs_where_this_distro_keeps_anchors(run, cert):
    code, out, calls = run("--system", "--os", "linux")
    present = {d: tool for d, tool in KNOWN_ANCHORS.items() if Path(d).is_dir()}
    if not present:
        assert code == 1
        assert "no supported trust-anchor directory" in out
        return
    installs = [c for c in calls if c.startswith("sudo install -m 0644 ")]
    assert len(installs) == 1, calls
    anchor = installs[0].split()[-1]
    assert anchor.endswith("/wx-dlh-ca.crt")
    directory = anchor.rsplit("/", 1)[0]
    assert directory in present
    assert f"sudo {present[directory]}" in calls
    # sudo is only recorded here, so the read-back finds nothing - and must say so
    assert code == 1
    assert "does not match" in out


def test_the_default_certificate_is_the_one_wx_dlh_ca_creates(run, tmp_path, cert):
    ca_dir = tmp_path / "home" / ".config" / "wx-dlh" / "ca"
    ca_dir.mkdir(parents=True)
    shutil.copy(cert[0], ca_dir / "mitmproxy-ca-cert.pem")
    code, out, calls = run(cert_arg=False, STUB_UNAME="Darwin")
    assert code == 0, out
    assert any(c.endswith(str(ca_dir / "mitmproxy-ca-cert.pem")) for c in calls)


def test_a_missing_certificate_says_how_to_make_one(run, tmp_path):
    code, out, _ = run("--cert", str(tmp_path / "nope.pem"), cert_arg=False, STUB_UNAME="Darwin")
    assert code == 1
    assert "wx-dlh ca" in out
