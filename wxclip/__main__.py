"""Command line entry point: `python -m wxclip` or `wxclip`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from wxclip import __version__

# Subcommands land with their milestones; see docs/开发计划.md.
PLANNED = {
    "proxy": "run the MITM proxy: inject the hook script, receive url + decode_key",
    "download": "download a captured url directly (no proxy) and decrypt its head",
    "decrypt": "decrypt an already downloaded file's first 128 KiB from its decode_key",
    "capture": "inject, capture, download and decrypt in one go",
    "serve": "serve the admin / download dashboard",
}


def build_parser() -> argparse.ArgumentParser:
    planned = ", ".join(f"{name} ({desc})" for name, desc in PLANNED.items())
    parser = argparse.ArgumentParser(
        prog="wxclip",
        description="Capture, decode and download WeChat videos playable in your WeChat client.",
        epilog=f"planned commands: {planned}",
    )
    parser.add_argument("--version", action="version", version=f"wxclip {__version__}")
    parser.add_argument(
        "command",
        nargs="?",
        choices=sorted(PLANNED),
        help="subcommand to run (none implemented yet)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    print(f"wxclip {args.command}: not implemented yet - {PLANNED[args.command]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
