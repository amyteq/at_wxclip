"""scripts/trust-ca.sh on each OS, with the platform tools replaced by recording stubs.

This machine is Linux, so the macOS and Windows branches can only be checked for what
they *ask* the OS to do - the right tool, the right store, sudo only where it belongs,
and a read-back after every install. Whether WeChat then accepts the CA is the question
only a real client answers (issue #11); nothing here claims it does.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "trust-ca.sh"
#: Point this at another bash (macOS ships 3.2) to check the script still parses there.
BASH = os.environ.get("TRUST_CA_BASH", "bash")
#: Resolved here, because a test can hand the script a PATH without openssl on it.
BASH_EXE = shutil.which(BASH)

#: openssl is the script's own requirement, not just this suite's: it reads the
#: certificate with it before any store is touched. Without it there is nothing to test -
#: `test_the_script_stops_when_openssl_is_missing` is what covers that case.
pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None or BASH_EXE is None,
    reason="needs bash and openssl, which the script itself requires",
)

#: PEM delimiters around base64 that is not a certificate: `b"abc"`, in fact.
WRAPPED_JUNK = b"-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----\n"

#: Enough of a PATH for the script to reach its openssl check without finding openssl.
#: `bash` is on it because the stubs below start with `#!/usr/bin/env bash`.
BARE_TOOLS = ("bash", "grep", "sed", "awk", "sort", "cat", "rm", "mkdir", "cmp", "install")

# Each stub appends "<name> <args>" to $STUB_LOG; behaviour that a test needs to vary
# comes from STUB_* variables so one set of stubs serves every case.
STUBS = {
    "uname": 'echo "${STUB_UNAME:-Linux}"',
    "id": 'echo "${STUB_UID:-1000}"',  # the script only asks for -u
    # Recorded, never run: nothing here may touch the real system. STUB_UPDATE is the
    # exit code of the one command it is asked to run that can fail on its own, the
    # distro's trust-bundle rebuild.
    "sudo": 'case " $* " in *" update-ca-"*) exit "${STUB_UPDATE:-0}" ;; esac',
    # STUB_FIND says what the keychain query does: 0 read, nothing of ours in it; 1 ours
    # is in it; 44 errSecItemNotFound; anything else a keychain it could not read at all.
    # The last two messages differ on purpose - only one of them means absence.
    "security": """case "$1" in
  login-keychain) echo '    "/Users/me/Library/Keychains/login.keychain-db"' ;;
  verify-cert) exit "${STUB_VERIFY:-0}" ;;
  add-trusted-cert) exit "${STUB_ADD:-0}" ;;
  delete-certificate) exit "${STUB_DELETE:-0}" ;;
  find-certificate)
    code="${STUB_FIND:-0}"
    case "$code" in
      1) echo "SHA-1 hash: $STUB_SHA1"; code=0 ;;
      0) ;;
      44) echo "security: the specified item could not be found in the keychain." >&2 ;;
      *) echo "security: the specified keychain could not be found." >&2 ;;
    esac
    exit "$code" ;;
esac
exit 0""",
    # STUB_STORE is what the search does: 0 the store holds it, 4 the store was searched
    # and does not - certutil says CRYPT_E_NOT_FOUND, and 4 is the low byte of that
    # HRESULT, which is all a Linux parent sees - anything else a store it could not
    # search. STUB_DELSTORE is the deletion's own exit code.
    "certutil.exe": """case " $* " in
  *" -store "*)
    code="${STUB_STORE:-0}"
    [ "$code" != 4 ] || echo "CertUtil: -store command FAILED: 0x80092004 CRYPT_E_NOT_FOUND"
    exit "$code" ;;
  *" -delstore "*) exit "${STUB_DELSTORE:-0}" ;;
esac""",
    "cygpath": 'echo "C:\\\\Users\\\\me$(echo "$2" | tr / \\\\\\\\)"',
    "wslpath": 'echo "\\\\\\\\wsl.localhost\\\\Ubuntu$(echo "$2" | tr / \\\\\\\\)"',
    # STUB_NSS_LIST is what `-L` shows, STUB_NSS_PEM the file `-L -n X -a` prints
    # (STUB_NSS_PEM_LEGACY for the old nickname, so the two can hold different CAs), and
    # STUB_NSS_L the exit code of both - how a database certutil cannot open behaves.
    # STUB_NSS_A and STUB_NSS_D are the exit codes of the import and of a deletion.
    "certutil": """case " $* " in
  *" -L "*" -a "*)
    pem="${STUB_NSS_PEM:-}"
    case " $* " in *" -n wxclip-ca "*) pem="${STUB_NSS_PEM_LEGACY:-$pem}" ;; esac
    [ -z "$pem" ] || cat "$pem" ;;
  *" -L "*) [ -z "${STUB_NSS_LIST:-}" ] || printf '%s\\n' "$STUB_NSS_LIST" ;;
  *" -A "*) exit "${STUB_NSS_A:-0}" ;;
  *" -D "*) exit "${STUB_NSS_D:-0}" ;;
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
    bare = tmp_path / "bare"  # the PATH a machine without openssl on it has
    bare.mkdir()
    for name in BARE_TOOLS:
        found = shutil.which(name)
        if found:
            (bare / name).symlink_to(found)

    def invoke(
        *args: str,
        cert_arg: bool = True,
        hide: tuple[str, ...] = (),
        openssl: bool = True,
        **env: str,
    ) -> tuple[int, str, list[str]]:
        for name in hide:
            (stubs / name).unlink(missing_ok=True)
        log.write_text("")
        path = os.environ["PATH"] if openssl else str(bare)
        full_env = {
            "PATH": f"{stubs}{os.pathsep}{path}",
            "HOME": str(home),
            "STUB_LOG": str(log),
            "STUB_SHA1": cert[1],
            **env,
        }
        argv = [BASH_EXE, str(SCRIPT), *args]
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


@pytest.mark.parametrize(
    ("name", "content", "says"),
    [
        ("wrapped.pem", WRAPPED_JUNK, "no X.509"),
        ("bundle.pem", None, "2 certificates"),
        ("binary.cer", bytes(range(256)), "not a PEM certificate"),
    ],
)  # fmt: skip
def test_what_is_between_the_pem_delimiters_is_parsed_not_just_decoded(
    run, tmp_path, cert, stale, name, content, says
):
    """The delimiters prove nothing - a BEGIN CERTIFICATE block can wrap any bytes at all -
    and the store commands copy the file as it stands, so openssl reads it first. A bundle
    goes the same way: only its first certificate would ever be looked at."""
    junk = tmp_path / name
    bundle = cert[0].read_bytes() + stale.read_bytes()  # two certificates in one file
    junk.write_bytes(bundle if content is None else content)
    code, out, calls = run("--cert", str(junk), cert_arg=False, STUB_UNAME="Darwin")
    assert code == 1
    assert says in out
    assert not [c for c in calls if c.startswith("security")]


def _der(pem: Path) -> bytes:
    """The certificate's own DER, as openssl re-encodes it."""
    return subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-outform", "DER"], check=True, capture_output=True
    ).stdout


def _block(der: bytes) -> bytes:
    """One PEM certificate block around exactly these bytes."""
    return (
        b"-----BEGIN CERTIFICATE-----\n" + base64.encodebytes(der) + b"-----END CERTIFICATE-----\n"
    )


def _behind_the_certificate(der: bytes) -> bytes:
    """One certificate and then opaque bytes, in one block: `openssl x509` reads the first
    ASN.1 value and never looks at the rest, so the parse above says nothing about them."""
    return _block(der + bytes(32))


def _padded_length(der: bytes) -> bytes:
    """The same certificate with a redundant zero byte in its outer length. openssl takes
    it and canonicalises it, so the fingerprint it prints is of bytes the file does not
    hold - and that fingerprint is what every store here is searched by."""
    count = der[1] & 0x7F
    return _block(der[:1] + bytes([0x80 | (count + 1)]) + b"\x00" + der[2:])


def _after_the_block(der: bytes) -> bytes:
    """A DER private key appended after the block. --system installs the file whole, and
    the text search for a `BEGIN ... PRIVATE KEY` line cannot see bytes like these."""
    return _block(der) + b"\x30\x82\x01\x00\x02\x01\x00"


@pytest.mark.parametrize(
    ("build", "says"),
    [
        (_behind_the_certificate, "more than the certificate between its PEM delimiters"),
        (_padded_length, "more than the certificate between its PEM delimiters"),
        (_after_the_block, "bytes outside its PEM certificate block"),
    ],
)  # fmt: skip
def test_only_the_certificate_may_be_in_the_file(run, tmp_path, cert, build, says):
    """Each of these parses as one certificate and would be installed as it stands, so the
    script compares what openssl re-encodes with what the block decodes to, and refuses
    anything outside the block as well. `wx_dlh/ca.py` holds the same line in Python."""
    junk = tmp_path / "extra.pem"
    junk.write_bytes(build(_der(cert[0])))
    code, out, calls = run("--cert", str(junk), cert_arg=False, STUB_UNAME="Darwin")
    assert code == 1
    assert "no X.509 certificate" not in out  # openssl itself is happy with all three
    assert says in out
    assert not [c for c in calls if c.startswith("security")]


def test_a_certificate_that_is_no_ca_is_refused(run, tmp_path):
    """Every store here is handed the file as a trust *anchor*, so the script asks openssl
    whether it may be one: `-purpose` prints X509_check_ca as its OCSP helper CA line. That
    is the function `wx-dlh ca` reaches through Python's ssl, where `get_ca_certs` hands
    back only the certificates it accepts, so the two halves refuse the same file."""
    leaf = tmp_path / "leaf.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-keyout", str(tmp_path / "leaf-key.pem"), "-out", str(leaf),
         "-subj", "/CN=mitmproxy/O=mitmproxy", "-days", "1",
         "-addext", "basicConstraints=critical,CA:FALSE"],
        check=True, capture_output=True,
    )  # fmt: skip
    code, out, calls = run("--cert", str(leaf), cert_arg=False, STUB_UNAME="Darwin")
    assert code == 1
    assert "not a CA certificate" in out
    assert not [c for c in calls if c.startswith("security")]


def test_the_script_stops_when_openssl_is_missing(run, tmp_path):
    """openssl is what reads the certificate before a store is touched and what every
    store is searched by here, so the script says so instead of installing a file it
    never parsed. (`wx-dlh ca` needs no openssl at all - that is the way out.)"""
    code, out, calls = run("--install", openssl=False, STUB_UNAME="Darwin")
    assert code == 1
    assert "openssl not found" in out
    assert "wx-dlh ca" in out
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


def test_macos_a_keychain_that_could_not_be_read_is_not_an_empty_one(run):
    """A keychain `security` cannot open is not a keychain our CA is absent from: saying
    so would make --list call a trusted CA gone and --remove report an undo that undid
    nothing, both with exit 0. The query is captured rather than piped so that its status
    survives, and "the specified keychain could not be found" is a failure - it is the
    keychain that is missing, not the certificate."""
    for action in ("--list", "--remove"):
        code, out, calls = run(action, STUB_UNAME="Darwin", STUB_FIND="50")
        assert code == 1
        assert "cannot read" in out
        assert "not in /Users/me" not in out
        assert "nothing to remove" not in out
        assert not [c for c in calls if "delete-certificate" in c]


def test_macos_an_item_that_is_not_there_is_absence_not_a_failure(run):
    """errSecItemNotFound, which a shell sees as 44: the keychain was read and holds no
    such certificate. Every --list on a machine without the CA goes this way."""
    code, out, _ = run("--list", STUB_UNAME="Darwin", STUB_FIND="44")
    assert code == 0, out
    assert "not in /Users/me/Library/Keychains/login.keychain-db" in out
    code, out, calls = run("--remove", STUB_UNAME="Darwin", STUB_FIND="44")
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
    code, out, _ = run(STUB_UNAME="MINGW64_NT-10.0", STUB_STORE="4")
    assert code == 1
    assert "does not list it" in out


def test_windows_remove_deletes_by_fingerprint(run, cert):
    code, out, calls = run("--remove", STUB_UNAME="MINGW64_NT-10.0")
    assert code == 0, out
    assert f"certutil.exe -user -delstore Root {cert[1]}" in calls


def test_windows_a_store_that_could_not_be_searched_is_not_an_empty_one(run):
    """Access denied, a store that will not open: certutil fails without saying
    CRYPT_E_NOT_FOUND, and that is not "our CA is not in there". Reading it as absence
    would make --list call a trusted CA gone and --remove report an undo that undid
    nothing, both with exit 0."""
    for action in ("--list", "--remove"):
        code, out, calls = run(action, STUB_UNAME="MINGW64_NT-10.0", STUB_STORE="5")
        assert code == 1
        assert "cannot read the Root store" in out
        assert "not in CurrentUser" not in out
        assert "nothing to remove" not in out
        assert not [c for c in calls if "-delstore" in c]


def test_windows_crypt_e_not_found_is_absence_not_a_failure(run):
    """The other half of the same check: a search that ran and found nothing must still
    read as "not installed", or every --list on a clean machine would be an error."""
    code, out, calls = run("--list", STUB_UNAME="MINGW64_NT-10.0", STUB_STORE="4")
    assert code == 0, out
    assert "not in CurrentUser\\Root" in out
    code, out, calls = run("--remove", STUB_UNAME="MINGW64_NT-10.0", STUB_STORE="4")
    assert code == 0, out
    assert "nothing to remove" in out
    assert not [c for c in calls if "-delstore" in c]


def test_windows_remove_that_did_not_delete_is_not_a_removal(run):
    code, out, _ = run("--remove", STUB_UNAME="MINGW64_NT-10.0", STUB_DELSTORE="1")
    assert code == 1
    assert "removed from" not in out
    assert "-delstore Root" in out  # the command that failed is named


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


def test_linux_nss_remove_leaves_another_cas_certificate_where_it_is(run, tmp_path, cert, stale):
    """The nickname is ours, the certificate under it need not be: `wx-dlh ca` can mint a
    new CA while the store still holds the old one under the same name. `--list` calls
    that entry "another CA", so `--remove` must not delete it - it is a trust anchor
    nobody asked about. Only the one the fingerprint matches goes."""
    db = _nssdb(tmp_path)
    code, out, calls = run(
        "--remove", "--user", "--os", "linux",
        STUB_NSS_LIST="wxclip-ca     C,,\nwx-dlh-ca     C,,",
        STUB_NSS_PEM=str(cert[0]), STUB_NSS_PEM_LEGACY=str(stale),
    )  # fmt: skip
    assert code == 0, out
    assert f"certutil -d sql:{db} -D -n wx-dlh-ca" in calls
    assert not [c for c in calls if "-D -n wxclip-ca" in c]
    assert "another CA, SHA-256 " in out
    assert "left alone" in out
    assert "removed wx-dlh-ca" in out


def test_linux_nss_remove_does_not_delete_an_entry_it_could_not_read(run, tmp_path):
    """Unreadable is not "someone else's" either: it might be ours. It stays, and the
    script says it did not finish rather than reporting an undo that undid nothing."""
    _nssdb(tmp_path)
    code, out, calls = run(
        "--remove", "--user", "--os", "linux", STUB_NSS_LIST="wx-dlh-ca     C,,"
    )  # no STUB_NSS_PEM: `certutil -L -n ... -a` prints nothing to fingerprint
    assert code == 1
    assert not [c for c in calls if " -D " in c]
    assert "could not be read" in out
    assert "nothing to remove" not in out


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


def _anchor_dir_here() -> str | None:
    """The directory the script would pick on this machine - both update tools are stubs
    on the PATH these tests build, so the first one that exists is it. Nothing is written
    there: sudo is a stub that records its arguments and runs nothing."""
    return next((d for d in KNOWN_ANCHORS if Path(d).is_dir()), None)


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


# --- a store command that failed -----------------------------------------------------
#
# Every apply function in the script is called on the left of `||` in the loop at the
# end, and that switches `set -e` off inside it: a store command whose result is not
# looked at falls through to the line that reports success, leaving the store as it was
# with exit 0. wx_dlh/trust.py checks each of these through `_change`; these are the
# cases that hold the script to the same answer.


def test_nss_install_that_could_not_import_is_not_an_install(run):
    code, out, _ = run("--install", "--user", "--os", "linux", STUB_NSS_A="1")
    assert code == 1
    assert "read back" not in out
    assert "-A -t C,," in out  # the command that failed is named


def test_nss_remove_that_could_not_delete_is_not_a_removal(run, tmp_path, cert):
    _nssdb(tmp_path)
    code, out, _ = run(
        "--remove", "--user", "--os", "linux",
        STUB_NSS_LIST="wx-dlh-ca     C,,", STUB_NSS_PEM=str(cert[0]), STUB_NSS_D="1",
    )  # fmt: skip
    assert code == 1
    assert "removed" not in out
    assert "exited 1" in out


def test_clearing_an_earlier_install_stays_best_effort(run):
    """The two deletions before the import only clear a previous install, and there is
    usually nothing to delete. The port ignores their result as well, so this is parity,
    not an oversight."""
    code, out, _ = run("--install", "--user", "--os", "linux", STUB_NSS_D="255")
    assert code == 0, out
    assert "(read back)" in out


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory too")
def test_a_database_directory_that_could_not_be_made_is_not_an_install(run, tmp_path):
    """certutil is a stub here and reports success whatever it is handed, so without the
    check this prints "installed ... (read back)" for a database that does not exist."""
    home = tmp_path / "home"
    home.chmod(0o500)
    try:
        code, out, calls = run("--install", "--user", "--os", "linux")
    finally:
        home.chmod(0o700)
    assert code == 1
    assert "cannot create" in out
    assert "read back" not in out
    assert not [c for c in calls if " -A " in c]


def test_macos_install_that_was_not_added_is_not_a_trusted_root(run):
    """verify-cert is a stub that says yes, as a machine that already trusts another
    mitmproxy CA would: the read-back cannot stand in for the add's own exit code."""
    code, out, _ = run("--install", STUB_UNAME="Darwin", STUB_ADD="1")
    assert code == 1
    assert "trusted in" not in out
    assert "add-trusted-cert" in out


def test_macos_remove_that_could_not_delete_is_not_a_removal(run):
    code, out, _ = run("--remove", STUB_UNAME="Darwin", STUB_FIND="1", STUB_DELETE="1")
    assert code == 1
    assert "removed from" not in out
    assert "delete-certificate" in out


def test_linux_system_install_that_could_not_rebuild_the_bundle_is_not_an_install(run):
    """The anchor file alone is not a trust anchor until the distro's tool rebuilds the
    bundle from it. A failed rebuild leaves a file nothing reads, so it stops there -
    before the comparison that would otherwise have called it installed."""
    if not _anchor_dir_here():
        pytest.skip("no trust-anchor directory on this machine")
    code, out, calls = run("--system", "--os", "linux", STUB_UPDATE="1")
    assert code == 1
    assert "installed as" not in out
    assert "does not match" not in out  # it stopped at the rebuild, before the read-back
    assert [c for c in calls if c.startswith("sudo update-ca-")]
    assert "exited 1" in out


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
