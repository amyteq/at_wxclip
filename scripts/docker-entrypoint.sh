#!/bin/sh
# Entrypoint of the wx-dlh image: run `wx-dlh "$@"` as the user who owns the mounts.
#
# The container writes into host directories (the CA, captures, downloads), so it should
# write as the host user, not as root. Rather than make everyone look up their uid, the
# owner is read off the mounts themselves: the first of them that is not root-owned wins,
# then WX_DLH_UID/WX_DLH_GID if set, then 1000. A directory Docker had to create (root-owned,
# Linux only) is handed to that user. Docker Desktop on macOS and Windows maps ownership
# for you, so there this mostly picks the right uid and changes nothing.
set -eu

dirs="$WX_DLH_CA_DIR $WX_DLH_CAPTURE_DIR $WX_DLH_DOWNLOAD_DIR $WX_DLH_PROCESS_DIR"

if [ "$(id -u)" != 0 ]; then  # `user:` was set in compose: nothing to decide
  exec wx-dlh "$@"
fi

uid="${WX_DLH_UID:-}"
gid="${WX_DLH_GID:-}"
if [ -z "$uid" ]; then
  for dir in $dirs; do
    [ -d "$dir" ] || continue
    owner="$(stat -c %u "$dir")"
    if [ "$owner" != 0 ]; then
      uid="$owner"
      gid="${gid:-$(stat -c %g "$dir")}"
      break
    fi
  done
fi
uid="${uid:-1000}"
gid="${gid:-$uid}"

for dir in $dirs; do
  mkdir -p "$dir"
  if [ "$(stat -c %u "$dir")" = 0 ] && [ "$uid" != 0 ]; then
    chown "$uid:$gid" "$dir" 2>/dev/null || true
  fi
done

exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- wx-dlh "$@"
