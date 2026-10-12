#!/usr/bin/env bash
# Build the LD_PRELOAD shim from scripts/appex-flags-preload.c and drop it where the
# WeChat Flatpak sandbox can actually see it.
#
# The sandbox only maps the subpaths of ~/.var/app/<app-id>/ that flatpak is told to
# persist, so the library goes into .wx-dlh/ and we add --persist=.wx-dlh. Inside the
# sandbox that same file appears as $HOME/.wx-dlh/<name>, which is the path LD_PRELOAD
# has to name.
set -euo pipefail

APP_ID="${WX_DLH_FLATPAK_APP_ID:-com.tencent.WeChat}"
SRC="$(cd "$(dirname "$0")" && pwd)/appex-flags-preload.c"
SO_NAME="libwx-dlh-appex-flags.so"
OUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --app-id) APP_ID="$2"; shift ;;
    --output) OUT="$2"; shift ;;
    -h|--help)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      echo
      echo "usage: scripts/build-appex-preload.sh [--app-id ID] [--output PATH]"
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

host_dir="$HOME/.var/app/$APP_ID/.wx-dlh"
[ -n "$OUT" ] || OUT="$host_dir/$SO_NAME"

command -v cc >/dev/null 2>&1 || { echo "no C compiler (cc) found" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"
cc -O2 -fPIC -shared -Wall -Wextra -o "$OUT" "$SRC" -ldl
echo "built $OUT"

# The shim runs against the Flatpak runtime's glibc, which is usually older than the
# host's. Report what it needs so an ABI mismatch is obvious here rather than as a
# silently missing webview later.
if command -v objdump >/dev/null 2>&1; then
  need="$(objdump -p "$OUT" | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1)"
  echo "needs at most $need"
fi

if command -v flatpak >/dev/null 2>&1 && [ -z "${WX_DLH_SKIP_OVERRIDE:-}" ]; then
  flatpak override --user "$APP_ID" --persist=.wx-dlh
  echo "[flatpak:$APP_ID] ~/.var/app/$APP_ID/.wx-dlh is now persisted into the sandbox"
fi

echo "sandbox path for LD_PRELOAD: \$HOME/.wx-dlh/$SO_NAME"
