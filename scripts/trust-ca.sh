#!/usr/bin/env bash
# Install, list or remove the wxclip proxy CA in the trust stores WeChat reads.
#
# WeChat renders Channels pages in a Chromium-based webview (RadiumWMPF/WeChatAppEx), and
# Chromium on Linux takes user-added roots from a NSS database, not from OpenSSL's. Which
# database depends on how WeChat was installed:
#
#   flatpak  the app has no access to the host home, so its NSS db is inside the sandbox
#            home: ~/.var/app/<app-id>/.pki/nssdb
#   user     a distro package (e.g. /opt/wechat) reads the normal ~/.pki/nssdb
#   system   OpenSSL-based traffic reads the system anchors; needs sudo, and a Flatpak app
#            does not see them (the runtime ships its own bundle)
#
# Restart WeChat after a change - the store is read at startup.
set -euo pipefail

CERT="${WXCLIP_CA_DIR:-$HOME/.config/wxclip/ca}/mitmproxy-ca-cert.pem"
APP_ID="${WXCLIP_FLATPAK_APP_ID:-com.tencent.WeChat}"
NICKNAME="wxclip-ca"
ANCHOR="/etc/ca-certificates/trust-source/anchors/wxclip-ca.crt"
ACTION="add"
TARGETS=()

usage() {
  cat <<'USAGE'
usage: scripts/trust-ca.sh [--flatpak] [--user] [--system] [--remove|--list]
                           [--cert PATH] [--app-id ID]

With no target, every store that this machine has is used, except --system (sudo).
  --flatpak   NSS db inside the WeChat Flatpak sandbox
  --user      NSS db in ~/.pki/nssdb (non-Flatpak builds; also your browsers)
  --system    /etc/ca-certificates/trust-source/anchors via update-ca-trust (sudo)
  --remove    take the certificate back out
  --list      show what is installed where
  --cert      certificate to install (default: $WXCLIP_CA_DIR/mitmproxy-ca-cert.pem)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --flatpak) TARGETS+=(flatpak) ;;
    --user) TARGETS+=(user) ;;
    --system) TARGETS+=(system) ;;
    --all) TARGETS+=(flatpak user system) ;;
    --remove) ACTION="remove" ;;
    --list) ACTION="list" ;;
    --cert) CERT="$2"; shift ;;
    --app-id) APP_ID="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

flatpak_db() { echo "$HOME/.var/app/$APP_ID/.pki/nssdb"; }
have() { command -v "$1" >/dev/null 2>&1; }

if [ ${#TARGETS[@]} -eq 0 ]; then
  if have flatpak && flatpak info "$APP_ID" >/dev/null 2>&1; then
    TARGETS+=(flatpak)
  fi
  [ -d "$HOME/.pki/nssdb" ] && TARGETS+=(user)
  if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "no NSS database found; pass --user to create ~/.pki/nssdb or --system" >&2
    exit 1
  fi
fi

if [ "$ACTION" != "list" ] && [ ! -f "$CERT" ]; then
  echo "certificate not found: $CERT" >&2
  echo "create it first:  .venv/bin/python -m wxclip ca" >&2
  exit 1
fi

if [ "$ACTION" != "list" ]; then
  echo "certificate: $CERT"
  have openssl && openssl x509 -in "$CERT" -noout -subject -fingerprint -sha256 | sed 's/^/  /'
fi

nss_apply() {
  local db="$1" label="$2"
  if ! have certutil; then
    echo "[$label] certutil not found (install nss) - skipped" >&2
    return 1
  fi
  case "$ACTION" in
    add)
      mkdir -p "$db"
      [ -f "$db/cert9.db" ] || certutil -d "sql:$db" -N --empty-password
      certutil -d "sql:$db" -D -n "$NICKNAME" >/dev/null 2>&1 || true
      certutil -d "sql:$db" -A -t "C,," -n "$NICKNAME" -i "$CERT"
      echo "[$label] installed in $db"
      ;;
    remove)
      if certutil -d "sql:$db" -D -n "$NICKNAME" >/dev/null 2>&1; then
        echo "[$label] removed from $db"
      else
        echo "[$label] nothing to remove in $db"
      fi
      ;;
    list)
      echo "[$label] $db"
      certutil -d "sql:$db" -L 2>/dev/null | grep -E "^$NICKNAME|Certificate Nickname" || echo "  (not installed)"
      ;;
  esac
}

system_apply() {
  case "$ACTION" in
    add)
      sudo install -m 0644 "$CERT" "$ANCHOR"
      sudo update-ca-trust
      echo "[system] installed as $ANCHOR"
      echo "[system] note: a Flatpak app does not see this - it uses the runtime's bundle"
      ;;
    remove)
      if [ -f "$ANCHOR" ]; then
        sudo rm -f "$ANCHOR"
        sudo update-ca-trust
        echo "[system] removed $ANCHOR"
      else
        echo "[system] nothing to remove"
      fi
      ;;
    list)
      [ -f "$ANCHOR" ] && echo "[system] $ANCHOR present" || echo "[system] not installed"
      ;;
  esac
}

for target in "${TARGETS[@]}"; do
  case "$target" in
    flatpak) nss_apply "$(flatpak_db)" "flatpak:$APP_ID" || true ;;
    user) nss_apply "$HOME/.pki/nssdb" "user" || true ;;
    system) system_apply ;;
  esac
done

if [ "$ACTION" = "add" ]; then
  echo
  echo "Restart WeChat, then point it at the proxy (设置 → 通用设置 → 网络代理)."
  echo "Undo with: scripts/trust-ca.sh --remove"
fi
