#!/usr/bin/env bash
# Install, list or remove the wx-dlh proxy CA in the trust stores WeChat reads.
#
# With wx-dlh installed, `wx-dlh ca --install` does the same - it is this script ported to
# Python (wx_dlh/trust.py), and on Windows it needs no Git Bash. This script stays for a
# machine that runs wx-dlh in Docker and has no Python of its own. The two take the same
# flags and are held to the same cases, so a change to one is a change to both.
#
# Which store that is depends on the OS, and on Linux on how WeChat was installed:
#
# Linux - WeChat renders Channels pages in a Chromium-based webview (RadiumWMPF/WeChatAppEx),
# and Chromium on Linux takes user-added roots from a NSS database, not from OpenSSL's:
#
#   flatpak  the app has no access to the host home, so its NSS db is inside the sandbox
#            home: ~/.var/app/<app-id>/.pki/nssdb. Writing the file there is not enough:
#            a Flatpak home only shows the subpaths its metadata persists (for WeChat
#            that is .xwechat and xwechat_files), so `.pki` has to be persisted too or
#            the app simply does not see the database. This script does that for you.
#   user     a distro package (e.g. /opt/wechat) reads the normal ~/.pki/nssdb
#   system   OpenSSL-based traffic reads the system anchors; needs sudo, and a Flatpak app
#            does not see them (the runtime ships its own bundle). Arch, Fedora/RHEL,
#            Debian/Ubuntu and openSUSE each keep them somewhere else; all four are handled.
#
# macOS - the keychain, through `security add-trusted-cert`:
#
#   keychain the login keychain, trusted for this user (no sudo; macOS asks for your
#            password in a dialog, so run it from a desktop session, not over ssh)
#   system   the System keychain, trusted for every user - what the reference
#            implementation asks for (sudo)
#
# Windows - the certificate store, through certutil.exe. Runs from Git Bash/MSYS2/Cygwin,
# and from WSL, where it targets the *Windows host's* store: WeChat runs on the host, so
# a CA in the WSL distro's own stores would never be consulted.
#
#   windows  CurrentUser\Root (no admin; Windows shows a confirmation dialog)
#   system   LocalMachine\Root (needs a shell started "as administrator")
#
# It needs openssl, which the Python version does not: that is where the certificate is
# parsed before anything is installed, and where the fingerprint every store is searched
# by comes from. Rather than install a file it never read, the script stops without it.
#
# Only Linux has been carried end to end (docs/M0-runbook.md). The macOS and Windows
# branches run the commands the reference implementation documents and then check the
# store they wrote to, but nobody has yet watched WeChat accept the CA on either - see
# issue #11. Every install is read back from the real store afterwards: on Linux each
# command succeeded long before WeChat accepted anything.
#
# Restart WeChat after a change - the store is read at startup.
set -euo pipefail

CA_DIR="${WX_DLH_CA_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/wx-dlh/ca}"
CERT="$CA_DIR/mitmproxy-ca-cert.pem"
APP_ID="${WX_DLH_FLATPAK_APP_ID:-com.tencent.WeChat}"
NICKNAME="wx-dlh-ca"
LEGACY_NICKNAME="wxclip-ca"  # before the rename; the same CA moves over, so clear it too
ACTION="add"
OS=""
TARGETS=()

usage() {
  cat <<'USAGE'
usage: scripts/trust-ca.sh [TARGET...] [--install|--remove|--list] [--cert PATH]
                           [--app-id ID] [--os linux|macos|windows]

With no target, the stores this OS has are used - never one that needs sudo or admin.

  Linux    --flatpak   NSS db inside the WeChat Flatpak sandbox
           --user      NSS db in ~/.pki/nssdb (non-Flatpak builds; also your browsers)
           --system    the distro's trust anchors + update-ca-trust/-certificates (sudo)
  macOS    --keychain  login keychain, trusted for you (default; a password dialog)
           --system    System keychain, trusted for all users (sudo)
  Windows  --windows   CurrentUser\Root via certutil.exe (default; a confirmation dialog)
           --system    LocalMachine\Root (run from an administrator shell)
  (WSL counts as Windows: WeChat runs on the host, so the host's store is the one.)

  --all       every store this OS has, including --system
  --install   put the certificate in (the default)
  --remove    take the certificate back out
  --list      show what is installed where
  --cert      certificate to use - the certificate alone, never the mitmproxy-ca.pem
              beside it that carries the private key as well (default:
              $WX_DLH_CA_DIR/mitmproxy-ca-cert.pem, i.e.
              ~/.config/wx-dlh/ca/mitmproxy-ca-cert.pem)
  --os        skip detection; under WSL, --os linux targets the distro's own stores

Needs openssl: it reads the certificate with it before any store is touched, and finds
certificates in a store by fingerprint. `wx-dlh ca` does the same without openssl.

--remove takes out only the certificate given here. An entry under the same nickname or
file name that holds another CA is reported and left alone.

Do not run it with sudo: it asks for sudo itself, and only for --system.
USAGE
}

die() { echo "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --flatpak) TARGETS+=(flatpak) ;;
    --user) TARGETS+=(user) ;;
    --keychain) TARGETS+=(keychain) ;;
    --windows) TARGETS+=(windows) ;;
    --system) TARGETS+=(system) ;;
    --all) TARGETS+=(all) ;;
    --install) ACTION="add" ;;
    --remove) ACTION="remove" ;;
    --list) ACTION="list" ;;
    --cert) [ $# -ge 2 ] || die "--cert needs a path"; CERT="$2"; shift ;;
    --app-id) [ $# -ge 2 ] || die "--app-id needs an id"; APP_ID="$2"; shift ;;
    --os) [ $# -ge 2 ] || die "--os needs a name"; OS="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# --- which OS --------------------------------------------------------------------------

is_wsl() {
  [ -n "${WSL_DISTRO_NAME:-}" ] || [ -n "${WSL_INTEROP:-}" ] \
    || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null
}

KERNEL="$(uname -s)"
if [ -z "$OS" ]; then
  case "$KERNEL" in
    Darwin) OS=macos ;;
    MINGW*|MSYS*|CYGWIN*) OS=windows ;;
    Linux) if is_wsl; then OS=windows; else OS=linux; fi ;;
    *) die "unsupported OS: $KERNEL - pass --os linux|macos|windows" ;;
  esac
fi
case "$OS" in
  linux|macos|windows) ;;
  *) die "--os must be linux, macos or windows, not: $OS" ;;
esac

# openssl is a requirement, not a nicety: it is what reads the certificate before a store
# is touched, and what turns it into the fingerprint every store here is searched by.
# Without it this script would have to hand a file it never parsed to a trust store.
have openssl || die "openssl not found - this script needs it to read the certificate and
its fingerprint; install it (openssl / libressl), or use: wx-dlh ca --install"

# The whole script under sudo would put root's HOME (and on macOS root's keychain) in
# play, and the default certificate path with it. Ask for sudo per command instead.
# What decides this is the kernel, not $OS: WSL is "windows" because WeChat runs on the
# host, but it is POSIX underneath and sudo there is still sudo. Git Bash/MSYS/Cygwin
# have no sudo at all - an elevated shell is how LocalMachine is reached there.
case "$KERNEL" in MINGW*|MSYS*|CYGWIN*) POSIX_HOST=no ;; *) POSIX_HOST=yes ;; esac
if [ "$POSIX_HOST" = yes ] && [ "$(id -u)" = 0 ] && [ -n "${SUDO_USER:-}" ]; then
  die "run this without sudo - it asks for sudo itself, and only for --system"
fi
SUDO="sudo"
[ "$POSIX_HOST" = yes ] || SUDO=""
[ "$(id -u)" != 0 ] || SUDO=""  # already root (a container, say)

# --- which stores ----------------------------------------------------------------------

stores_for_os() {
  case "$OS" in
    linux) echo "flatpak user system" ;;
    macos) echo "keychain system" ;;
    windows) echo "windows system" ;;
  esac
}

if [ ${#TARGETS[@]} -gt 0 ]; then
  wanted=()
  for target in "${TARGETS[@]}"; do
    if [ "$target" = all ]; then
      for store in $(stores_for_os); do wanted+=("$store"); done
      continue
    fi
    case " $(stores_for_os) " in
      *" $target "*) wanted+=("$target") ;;
      *) die "--$target is not a store on $OS (this OS has: $(stores_for_os | sed 's/\([a-z][a-z]*\)/--\1/g'))" ;;
    esac
  done
  TARGETS=("${wanted[@]}")
else
  case "$OS" in
    linux)
      if have flatpak && flatpak info "$APP_ID" >/dev/null 2>&1; then
        TARGETS+=(flatpak)
      fi
      [ -d "$HOME/.pki/nssdb" ] && TARGETS+=(user)
      if [ ${#TARGETS[@]} -eq 0 ]; then
        die "no NSS database found; pass --user to create ~/.pki/nssdb or --system"
      fi
      ;;
    macos) TARGETS=(keychain) ;;
    windows) TARGETS=(windows) ;;
  esac
fi

# Listing needs the certificate too: a store is searched by fingerprint, so without it
# there is nothing to search for.
if [ ! -f "$CERT" ]; then
  echo "certificate not found: $CERT" >&2
  echo "create it first:  wx-dlh ca   (or: python -m wx_dlh ca)" >&2
  exit 1
fi

# --cert takes a path, and mitmproxy-ca.pem sits next to mitmproxy-ca-cert.pem looking
# just as plausible - but it is the certificate *and* the CA's private key, and --system
# installs what it is given as a 0644 trust anchor. Refuse it before any store is touched.
if grep -q -- "-----BEGIN .*PRIVATE KEY-----" "$CERT"; then
  die "$CERT holds the CA private key as well as the certificate; pass mitmproxy-ca-cert.pem"
fi
# The delimiters say nothing about what is between them - a BEGIN CERTIFICATE block can
# wrap any bytes at all - and a bundle would have all but its first certificate installed
# unchecked, so openssl parses the file here, before a store sees it.
BLOCKS="$(grep -c -- "-----BEGIN CERTIFICATE-----" "$CERT" 2>/dev/null || true)"
[ "${BLOCKS:-0}" -ge 1 ] || die "not a PEM certificate: $CERT"
[ "${BLOCKS:-0}" -le 1 ] || die "$CERT holds $BLOCKS certificates; pass the CA's own, alone"
openssl x509 -in "$CERT" -noout >/dev/null 2>&1 \
  || die "openssl reads no X.509 certificate in $CERT"

# Parsing is not enough on its own: openssl stops at the end of the first ASN.1 value in
# the block and never looks at what follows it, while --system copies the file in as it
# stands, 0644. So what openssl re-encodes has to be every byte the block decodes to.
# One comparison, two things refused: bytes riding behind the certificate (another
# certificate, a raw private key - which the text search above cannot see - or padding),
# and a length DER would not write that way, which openssl silently canonicalises before
# it prints the fingerprint the stores below are searched by. wx_dlh/ca.py draws the same
# line in Python, where there is no openssl to re-encode with.
pem_block_bytes() {
  sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' "$1" \
    | sed -e '/-----/d' -e 's/[[:space:]]//g' | openssl enc -base64 -d -A 2>/dev/null
}
cmp -s <(openssl x509 -in "$CERT" -outform DER 2>/dev/null) <(pem_block_bytes "$CERT") \
  || die "$CERT holds more than the certificate between its PEM delimiters, or a length
DER would not write that way; pass the certificate alone, re-encoding it if need be with:
openssl x509 -in $CERT -out <new file>"
# And nothing outside the block either, for the same reason: the file is installed whole,
# so a DER private key appended after -----END CERTIFICATE----- would ride along unread.
if sed -e '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/d' "$CERT" \
    | grep -q '[^[:space:]]'; then
  die "$CERT holds bytes outside its PEM certificate block; pass a file with nothing in it
but the certificate, e.g.: openssl x509 -in $CERT -out <new file>"
fi

# Stores are searched by fingerprint, not by name: every mitmproxy CA is called
# "mitmproxy", and another tool's copy may well be sitting in the same store.
SHA1="$(openssl x509 -in "$CERT" -noout -fingerprint -sha1 | sed 's/.*=//; s/://g')"
SHA256="$(openssl x509 -in "$CERT" -noout -fingerprint -sha256 | sed 's/.*=//')"

echo "certificate: $CERT ($OS)"
openssl x509 -in "$CERT" -noout -subject -fingerprint -sha256 | sed 's/^/  /'

# Which certificate a store already holds: the one asked about, or another - a CA from
# before the last `wx-dlh ca` keeps both the nickname and the file name.
which_ca() {
  if [ -z "$1" ]; then echo "could not be read"
  elif [ "$1" = "$SHA256" ]; then echo "this CA"
  else echo "another CA, SHA-256 $1"; fi
}

file_sha256() { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//'; }

# --- Linux: NSS ------------------------------------------------------------------------

flatpak_db() { echo "$HOME/.var/app/$APP_ID/.pki/nssdb"; }

# The entries under our two nicknames, one per line; non-zero when the database is there
# but certutil cannot open it. A database it cannot open - locked, corrupt,
# password-protected - is not a database our CA is absent from, and saying so would make
# --remove report an undo that undid nothing and --list call a trusted CA gone.
nss_ours() {
  local db="$1" label="$2" listed
  [ -f "$db/cert9.db" ] || return 0
  if ! listed="$(certutil -d "sql:$db" -L 2>&1)"; then
    echo "[$label] cannot read $db: certutil failed" >&2
    return 1
  fi
  printf '%s\n' "$listed" | grep -E "^($NICKNAME|$LEGACY_NICKNAME)[[:space:]]" || true
}

nss_apply() {
  local db="$1" label="$2" ours nick found removed unreadable
  if ! have certutil; then
    echo "[$label] certutil not found (install nss / libnss3-tools) - skipped" >&2
    return 1
  fi
  case "$ACTION" in
    add)
      mkdir -p "$db"
      [ -f "$db/cert9.db" ] || certutil -d "sql:$db" -N --empty-password
      certutil -d "sql:$db" -D -n "$NICKNAME" >/dev/null 2>&1 || true
      certutil -d "sql:$db" -D -n "$LEGACY_NICKNAME" >/dev/null 2>&1 || true
      certutil -d "sql:$db" -A -t "C,," -n "$NICKNAME" -i "$CERT"
      if certutil -d "sql:$db" -L -n "$NICKNAME" >/dev/null 2>&1; then
        echo "[$label] installed in $db (read back)"
      else
        echo "[$label] WARNING: certutil succeeded but $db does not list it" >&2
        return 1
      fi
      ;;
    remove)
      ours="$(nss_ours "$db" "$label")" || return 1
      removed=""
      unreadable=0
      for nick in $(printf '%s\n' "$ours" | awk '{print $1}' | sort -u); do
        # The nickname is ours; the certificate under it need not be. Deleting what
        # --list would call "another CA" would take out an anchor nobody asked about.
        found="$(nss_sha256 "$db" "$nick")"
        if [ "$found" != "$SHA256" ]; then
          echo "[$label] $nick in $db: $(which_ca "$found") - left alone; take it out" \
               "with: certutil -d sql:$db -D -n $nick" >&2
          [ -n "$found" ] || unreadable=1
          continue
        fi
        certutil -d "sql:$db" -D -n "$nick" || return 1
        removed="$removed $nick"
      done
      if [ -n "$removed" ]; then
        echo "[$label] removed$removed from $db"
      elif [ -z "$ours" ]; then
        echo "[$label] nothing to remove in $db"
      fi
      [ "$unreadable" = 0 ] || return 1
      ;;
    list)
      echo "[$label] $db"
      ours="$(nss_ours "$db" "$label")" || return 1
      if [ -z "$ours" ]; then
        echo "  (not installed)"
      else
        printf '%s\n' "$ours" | while read -r line; do
          nick="${line%% *}"
          echo "  $line  ($(which_ca "$(nss_sha256 "$db" "$nick")"))"
        done
      fi
      ;;
  esac
}

# Which certificate sits under a nickname: the name alone does not say, and it stays the
# same across a `wx-dlh ca` that minted a new CA.
nss_sha256() {
  certutil -d "sql:$1" -L -n "$2" -a 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//'
}

# A Flatpak home only exposes the subpaths its metadata persists, so the database above
# is invisible to the app until `.pki` is persisted as well. Without this the certificate
# is installed, nothing errors, and the app still rejects the proxy (observed 2026-09-11).
flatpak_persist_pki() {
  [ "$ACTION" = "add" ] || return 0
  have flatpak || return 0
  flatpak override --user "$APP_ID" --persist=.pki
  echo "[flatpak:$APP_ID] ~/.pki is now persisted into the sandbox"
  if timeout 60 flatpak run --command=sh "$APP_ID" \
      -c '[ -f "$HOME/.pki/nssdb/cert9.db" ]' >/dev/null 2>&1; then
    echo "[flatpak:$APP_ID] the app can see the database"
  else
    echo "[flatpak:$APP_ID] WARNING: the app still cannot see \$HOME/.pki/nssdb" >&2
  fi
}

# --- Linux: system anchors -------------------------------------------------------------

# Each family keeps its anchors in its own place and rebuilds the bundle with its own
# tool. Debian's tool only picks up files ending in .crt, so that is the name everywhere.
linux_anchor_dir() {
  local dir
  if have update-ca-trust; then
    for dir in /etc/ca-certificates/trust-source/anchors /etc/pki/ca-trust/source/anchors; do
      [ -d "$dir" ] && { echo "$dir update-ca-trust"; return 0; }
    done
  fi
  if have update-ca-certificates; then
    for dir in /usr/local/share/ca-certificates /etc/pki/trust/anchors; do
      [ -d "$dir" ] && { echo "$dir update-ca-certificates"; return 0; }
    done
  fi
  return 1
}

linux_system_apply() {
  local found dir update anchor legacy path shown sha removed unreadable
  if ! found="$(linux_anchor_dir)"; then
    echo "[system] no supported trust-anchor directory (looked for update-ca-trust and" >&2
    echo "         update-ca-certificates layouts) - install $CERT by hand" >&2
    return 1
  fi
  dir="${found% *}"
  update="${found##* }"
  anchor="$dir/$NICKNAME.crt"
  legacy="$dir/$LEGACY_NICKNAME.crt"
  case "$ACTION" in
    add)
      [ -f "$legacy" ] && $SUDO rm -f "$legacy"
      $SUDO install -m 0644 "$CERT" "$anchor"
      $SUDO "$update"
      if cmp -s "$CERT" "$anchor"; then
        echo "[system] installed as $anchor ($update)"
      else
        echo "[system] WARNING: $anchor does not match $CERT after install" >&2
        return 1
      fi
      echo "[system] note: a Flatpak app does not see this - it uses the runtime's bundle"
      ;;
    remove)
      # The file name is ours; the certificate in it need not be. Removing what --list
      # would call "another CA" would take out an anchor nobody asked about.
      removed=""
      unreadable=0
      shown=0
      for path in "$anchor" "$legacy"; do
        [ -f "$path" ] || continue
        shown=1
        sha="$(file_sha256 "$path")"
        if [ "$sha" = "$SHA256" ]; then
          $SUDO rm -f "$path"
          removed="$removed $path"
        else
          echo "[system] $path: $(which_ca "$sha") - left alone; take it out with:" \
               "${SUDO:+$SUDO }rm $path && ${SUDO:+$SUDO }$update" >&2
          [ -n "$sha" ] || unreadable=1
        fi
      done
      if [ -n "$removed" ]; then
        $SUDO "$update"
        echo "[system] removed$removed"
      elif [ "$shown" = 0 ]; then
        echo "[system] nothing to remove in $dir"
      fi
      [ "$unreadable" = 0 ] || return 1
      ;;
    list)
      # Both names, and the fingerprint of each: the file being there says nothing about
      # *which* CA is in it, and a pre-rename install is still an install.
      shown=0
      for path in "$anchor" "$legacy"; do
        [ -f "$path" ] || continue
        shown=1
        echo "[system] $path ($(which_ca "$(file_sha256 "$path")"))"
      done
      [ "$shown" = 1 ] || echo "[system] not installed in $dir"
      ;;
  esac
}

# --- macOS: keychain -------------------------------------------------------------------

login_keychain() {
  local kc
  kc="$(security login-keychain 2>/dev/null | sed 's/^[[:space:]]*"//; s/"[[:space:]]*$//')"
  [ -n "$kc" ] || kc="$HOME/Library/Keychains/login.keychain-db"
  echo "$kc"
}

# Trusted means trust settings say so, which is what `verify-cert` evaluates; being in
# a keychain alone does not make a root trusted.
macos_trusted() { security verify-cert -c "$CERT" >/dev/null 2>&1; }

macos_in_keychain() {
  security find-certificate -a -Z "$1" 2>/dev/null | grep -qi "SHA-1 hash: $SHA1"
}

macos_apply() {
  local label="$1" kc domain=() run=()
  if ! have security; then
    echo "[$label] the security tool is missing - is this really macOS?" >&2
    return 1
  fi
  if [ "$label" = system ]; then
    kc="/Library/Keychains/System.keychain"
    domain=(-d)
    [ -z "$SUDO" ] || run=("$SUDO")
  else
    kc="$(login_keychain)"
  fi
  case "$ACTION" in
    add)
      # Bash 3.2 (macOS's) treats "${empty[@]}" as unbound under set -u; hence the +.
      ${run[@]+"${run[@]}"} security add-trusted-cert ${domain[@]+"${domain[@]}"} \
        -r trustRoot -k "$kc" "$CERT"
      if macos_trusted; then
        echo "[$label] trusted in $kc (read back with security verify-cert)"
      else
        echo "[$label] WARNING: added to $kc, but security verify-cert does not trust it" >&2
        return 1
      fi
      ;;
    remove)
      ${run[@]+"${run[@]}"} security remove-trusted-cert ${domain[@]+"${domain[@]}"} "$CERT" \
        >/dev/null 2>&1 || true
      if macos_in_keychain "$kc"; then
        ${run[@]+"${run[@]}"} security delete-certificate -Z "$SHA1" "$kc"
        echo "[$label] removed from $kc"
      else
        echo "[$label] nothing to remove in $kc"
      fi
      ;;
    list)
      if macos_in_keychain "$kc"; then echo "[$label] in $kc"; else echo "[$label] not in $kc"; fi
      ;;
  esac
}

# --- Windows: certificate store --------------------------------------------------------

certutil_exe() {
  if have certutil.exe; then
    echo certutil.exe
  elif [ -x /mnt/c/Windows/System32/certutil.exe ]; then
    echo /mnt/c/Windows/System32/certutil.exe  # WSL with appendWindowsPath=false
  else
    return 1
  fi
}

# certutil.exe wants a Windows path. Git Bash would convert a POSIX one by itself, but
# Cygwin and WSL do not, so convert explicitly and stop MSYS from converting again.
win_path() {
  if have cygpath; then
    cygpath -w "$1"
  elif have wslpath; then
    wslpath -w "$1"
  else
    echo "$1"
  fi
}

windows_apply() {
  local label="$1" exe scope=() store
  if ! exe="$(certutil_exe)"; then
    echo "[$label] certutil.exe not found - run this from Git Bash, MSYS2, Cygwin or WSL" >&2
    return 1
  fi
  if [ "$label" = system ]; then store="LocalMachine\\Root"; else scope=(-user); store="CurrentUser\\Root"; fi
  case "$ACTION" in
    add)
      if ! MSYS2_ARG_CONV_EXCL='*' "$exe" ${scope[@]+"${scope[@]}"} -addstore Root "$(win_path "$CERT")"; then
        [ "$label" = system ] && echo "[$label] LocalMachine needs a shell started as administrator" >&2
        return 1
      fi
      if "$exe" ${scope[@]+"${scope[@]}"} -store Root "$SHA1" >/dev/null 2>&1; then
        echo "[$label] installed in $store (read back)"
      else
        echo "[$label] WARNING: certutil reported success but $store does not list it" >&2
        return 1
      fi
      ;;
    remove)
      if "$exe" ${scope[@]+"${scope[@]}"} -store Root "$SHA1" >/dev/null 2>&1; then
        "$exe" ${scope[@]+"${scope[@]}"} -delstore Root "$SHA1"
        echo "[$label] removed from $store"
      else
        echo "[$label] nothing to remove in $store"
      fi
      ;;
    list)
      if "$exe" ${scope[@]+"${scope[@]}"} -store Root "$SHA1" >/dev/null 2>&1; then
        echo "[$label] in $store"
      else
        echo "[$label] not in $store"
      fi
      ;;
  esac
}

# --- run -------------------------------------------------------------------------------

failed=0
for target in "${TARGETS[@]}"; do
  case "$OS:$target" in
    linux:flatpak) { nss_apply "$(flatpak_db)" "flatpak:$APP_ID" && flatpak_persist_pki; } || failed=1 ;;
    linux:user) nss_apply "$HOME/.pki/nssdb" "user" || failed=1 ;;
    linux:system) linux_system_apply || failed=1 ;;
    macos:keychain|macos:system) macos_apply "$target" || failed=1 ;;
    windows:windows|windows:system) windows_apply "$target" || failed=1 ;;
  esac
done

if [ "$ACTION" = "add" ]; then
  echo
  case "$OS" in
    linux)
      echo "Restart WeChat completely (tray -> 退出), then point it at the proxy"
      echo "(设置 → 通用设置 → 网络代理). For the webview to use the proxy as well, see"
      echo "scripts/run-wechat-proxied.sh --persist."
      ;;
    macos)
      echo "Quit WeChat completely (⌘Q, not just the window) and open it again - the"
      echo "trust settings are read when it starts. Not yet verified with a real WeChat"
      echo "on macOS (issue #11): if the proxy still logs 'tls alert certificate unknown',"
      echo "note which process connects (lsof -nP -iTCP:8899) and report it there."
      ;;
    windows)
      echo "Quit WeChat completely (tray -> 退出) and open it again. Not yet verified with"
      echo "a real WeChat on Windows (issue #11)."
      ;;
  esac
  echo "Undo with: scripts/trust-ca.sh --remove"
fi
exit "$failed"
