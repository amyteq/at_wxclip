#!/usr/bin/env bash
# Launch the WeChat Flatpak with proxy environment variables set, so the *webview* uses
# the wxclip proxy too.
#
# Why this exists: WeChat's in-app proxy setting only covers its main process. The
# Channels page runs in a separate Chromium-based process (RadiumWMPF/WeChatAppEx) that
# gets neither `--proxy-server` nor any proxy env var, so it connects directly and the
# proxy never sees the page (observed 2026-09-11, see docs/M0-操作步骤.md). Chromium falls
# back to http_proxy/https_proxy/all_proxy when it has no other proxy configuration, and
# a Flatpak app inherits the env vars given to `flatpak run`.
#
# WeChat has to be fully quit first: env vars only apply to a fresh process tree.
set -euo pipefail

APP_ID="${WXCLIP_FLATPAK_APP_ID:-com.tencent.WeChat}"
PROXY="${WXCLIP_PROXY:-http://127.0.0.1:8899}"
KILL=0
BACKGROUND=0
ACTION="run"

while [ $# -gt 0 ]; do
  case "$1" in
    --proxy) PROXY="$2"; shift ;;
    --app-id) APP_ID="$2"; shift ;;
    --kill) KILL=1 ;;
    --background) BACKGROUND=1 ;;
    --persist) ACTION="persist" ;;
    --reset) ACTION="reset" ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      echo
      cat <<'USAGE'

usage: scripts/run-wechat-proxied.sh [--proxy URL] [--app-id ID] [options]

  (no option)    launch WeChat now with the proxy env vars set
  --kill         quit a running WeChat first
  --background   detach, so closing the terminal does not quit WeChat
  --persist      store the env vars as a flatpak override, so *any* launch route
                 gets them - the desktop icon and the tray included. Survives
                 restarts until --reset. WeChat then needs the proxy running to
                 load H5 pages such as 视频号
  --reset        drop the override again
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$ACTION" = "reset" ]; then
  flatpak override --user --reset "$APP_ID"
  echo "override cleared for $APP_ID - restart WeChat to go back to a direct connection"
  flatpak override --user --show "$APP_ID" || true
  exit 0
fi

host_port="${PROXY#*://}"
if ! (exec 3<>"/dev/tcp/${host_port%%:*}/${host_port##*:}") 2>/dev/null; then
  echo "nothing is listening on $PROXY - start it first:" >&2
  echo "  .venv/bin/python -m wxclip proxy" >&2
  exit 1
fi
echo "proxy reachable: $PROXY"

if [ "$ACTION" = "persist" ]; then
  flatpak override --user "$APP_ID" \
    --env=http_proxy="$PROXY" \
    --env=https_proxy="$PROXY" \
    --env=HTTP_PROXY="$PROXY" \
    --env=HTTPS_PROXY="$PROXY" \
    --env=all_proxy="$PROXY" \
    --env=no_proxy="localhost"
  echo "stored for $APP_ID:"
  flatpak override --user --show "$APP_ID" | sed 's/^/  /'
  echo
  echo "Now quit WeChat (tray -> 退出) and start it however you normally do -"
  echo "the webview will come through $PROXY. Undo with --reset."
  exit 0
fi

if pgrep -f "/app/extra/wechat/wechat" >/dev/null 2>&1; then
  if [ "$KILL" -eq 1 ]; then
    echo "quitting the running WeChat ..."
    flatpak kill "$APP_ID" || true
    sleep 3
  else
    echo "WeChat is still running. Quit it (tray → 退出) and re-run, or pass --kill." >&2
    exit 1
  fi
fi

echo "launching $APP_ID with http_proxy=$PROXY ..."
echo "(the in-app proxy setting can stay on - it covers the main process, this covers the webview)"

set -- flatpak run \
  --env=http_proxy="$PROXY" \
  --env=https_proxy="$PROXY" \
  --env=HTTP_PROXY="$PROXY" \
  --env=HTTPS_PROXY="$PROXY" \
  --env=all_proxy="$PROXY" \
  --env=no_proxy="localhost" \
  "$APP_ID"

if [ "$BACKGROUND" -eq 1 ]; then
  # detached, so closing this terminal does not take WeChat down with it
  setsid "$@" >/dev/null 2>&1 < /dev/null &
  echo "started in the background (pid $!). Closing this terminal is fine."
else
  echo "keep this terminal open - closing it quits WeChat (or re-run with --background)"
  exec "$@"
fi
