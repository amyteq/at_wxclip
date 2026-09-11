"""Command line entry point: `python -m wxclip` or `wxclip`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from wxclip import __version__, ca, inject
from wxclip import proxy as proxy_module  # mitmproxy-free at import time
from wxclip.config import Settings

# Subcommands land with their milestones; see docs/开发计划.md.
PLANNED = {
    "download": "download a captured url directly (no proxy) and decrypt its head",
    "decrypt": "decrypt an already downloaded file's first 128 KiB from its decode_key",
    "capture": "inject, capture, download and decrypt in one go",
    "serve": "serve the admin / download dashboard",
}

MISSING_MITMPROXY = (
    "wxclip proxy needs mitmproxy, which is an optional dependency.\n"
    "  make setup                                   # installs .[dev,proxy]\n"
    "  uv pip install --python .venv/bin/python -e '.[proxy]'"
)


def build_parser(defaults: Settings | None = None) -> argparse.ArgumentParser:
    defaults = defaults or Settings.from_env(dotenv=Path(".env"))
    parser = argparse.ArgumentParser(
        prog="wxclip",
        description="Capture, decode and download WeChat videos playable in your WeChat client.",
    )
    parser.add_argument("--version", action="version", version=f"wxclip {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    proxy = sub.add_parser(
        "proxy",
        help="run the MITM proxy: inject the hook script, receive url + decode_key",
        description=(
            "Runs an HTTP proxy that WeChat should point at. It injects web/inject.js into "
            "the Channels H5 page, answers the script and capture requests itself, and logs "
            "every flow it sees under the capture directory."
        ),
    )
    proxy.add_argument("--host", default=defaults.proxy_host, help="listen address")
    proxy.add_argument("--port", type=int, default=defaults.proxy_port, help="listen port")
    proxy.add_argument("--ca-dir", type=Path, default=defaults.ca_dir, help="CA directory")
    proxy.add_argument(
        "--capture-dir", type=Path, default=defaults.capture_dir, help="logs and saved pages"
    )
    proxy.add_argument(
        "--page-host",
        action="append",
        metavar="HOST",
        help="host whose HTML gets the hook script "
        f"(default: {', '.join(inject.DEFAULT_PAGE_HOSTS)})",
    )
    proxy.add_argument(
        "--any-html",
        action="store_true",
        help="inject into HTML from every host - for finding the page, not for daily use",
    )
    proxy.add_argument(
        "--ignore-host",
        action="append",
        metavar="REGEX",
        help="host regex to tunnel untouched (replaces the default mmtls host list)",
    )
    proxy.add_argument(
        "--no-ignore-hosts",
        action="store_true",
        help="intercept everything, WeChat's own mmtls transport included",
    )
    proxy.add_argument(
        "--stream-large-bodies",
        metavar="SIZE",
        default=proxy_module.DEFAULT_STREAM_LARGE_BODIES,
        help="pass bodies over SIZE through unbuffered, so media cannot stall behind the "
        f"proxy (default {proxy_module.DEFAULT_STREAM_LARGE_BODIES}; 'off' to buffer everything)",
    )
    proxy.add_argument("--no-save-pages", action="store_true", help="do not keep page HTML")
    proxy.add_argument("--no-save-flows", action="store_true", help="do not write a .mitm dump")
    proxy.add_argument(
        "--no-resp-snippet",
        action="store_true",
        help="do not report the JSAPI response body alongside a capture",
    )
    proxy.add_argument(
        "--dry-run", action="store_true", help="prepare the CA, print the plan, listen for nothing"
    )

    ca_cmd = sub.add_parser(
        "ca",
        help="create the proxy CA if needed and print how to trust it",
    )
    ca_cmd.add_argument("--ca-dir", type=Path, default=defaults.ca_dir, help="CA directory")
    ca_cmd.add_argument("--app-id", default=ca.FLATPAK_APP_ID, help="Flatpak app id to check for")

    for name, description in PLANNED.items():
        sub.add_parser(name, help=f"(planned) {description}")

    return parser


def cmd_ca(args: argparse.Namespace) -> int:
    try:
        ca.ensure_ca(args.ca_dir)
    except ImportError:
        print(MISSING_MITMPROXY, file=sys.stderr)
        return 3
    print(ca.trust_hint(args.ca_dir, args.app_id))
    return 0


def cmd_proxy(args: argparse.Namespace) -> int:
    settings = Settings.from_env(dotenv=Path(".env"))
    settings = Settings(
        proxy_host=args.host,
        proxy_port=args.port,
        server_host=settings.server_host,
        server_port=settings.server_port,
        download_dir=settings.download_dir,
        capture_dir=args.capture_dir,
        ca_dir=args.ca_dir,
    )
    try:
        cert = ca.ensure_ca(settings.ca_dir)
    except ImportError:
        print(MISSING_MITMPROXY, file=sys.stderr)
        return 3

    addon = proxy_module.build_addon(
        settings,
        page_hosts=tuple(args.page_host) if args.page_host else inject.DEFAULT_PAGE_HOSTS,
        inject_any_html=args.any_html,
        include_snippet=not args.no_resp_snippet,
        save_pages=not args.no_save_pages,
    )
    ignore_hosts: tuple[str, ...] = ()
    if not args.no_ignore_hosts:
        ignore_hosts = tuple(args.ignore_host or proxy_module.DEFAULT_IGNORE_HOSTS)
    options = proxy_module.proxy_options(
        settings,
        ignore_hosts=ignore_hosts,
        save_flows=not args.no_save_flows,
        stream_large_bodies=None if args.stream_large_bodies == "off" else args.stream_large_bodies,
    )

    if args.dry_run:
        print(proxy_module.describe(settings, addon, options))
        print(f"\n{ca.trust_hint(settings.ca_dir)}")
        return 0

    print(f"CA: {cert}")
    return proxy_module.run(settings, addon=addon, options=options)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command in PLANNED:
        print(
            f"wxclip {args.command}: not implemented yet - {PLANNED[args.command]}",
            file=sys.stderr,
        )
        return 2
    return {"proxy": cmd_proxy, "ca": cmd_ca}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
