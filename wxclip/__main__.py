"""Command line entry point: `python -m wxclip` or `wxclip`."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from wxclip import __version__, ca, downloader, inject, pipeline
from wxclip import decrypt as decrypt_module
from wxclip import proxy as proxy_module  # mitmproxy-free at import time
from wxclip import server as server_module
from wxclip.config import Settings
from wxclip.downloader import DownloadError, human_size
from wxclip.keystream import KeystreamError

# Subcommands land with their milestones; see docs/开发计划.md.
PLANNED: dict[str, str] = {}

MISSING_MITMPROXY = (
    "wxclip proxy needs mitmproxy, which is an optional dependency.\n"
    "  make setup                                   # installs .[dev,proxy]\n"
    "  uv pip install --python .venv/bin/python -e '.[proxy]'"
)


def _add_proxy_options(parser: argparse.ArgumentParser, defaults: Settings) -> None:
    """Options shared by `proxy` and `capture` - they run the same proxy."""
    parser.add_argument("--host", default=defaults.proxy_host, help="listen address")
    parser.add_argument("--port", type=int, default=defaults.proxy_port, help="listen port")
    parser.add_argument("--ca-dir", type=Path, default=defaults.ca_dir, help="CA directory")
    parser.add_argument(
        "--capture-dir", type=Path, default=defaults.capture_dir, help="logs and saved pages"
    )
    parser.add_argument(
        "--page-host",
        action="append",
        metavar="HOST",
        help="host whose HTML gets the hook script "
        f"(default: {', '.join(inject.DEFAULT_PAGE_HOSTS)})",
    )
    parser.add_argument(
        "--any-html",
        action="store_true",
        help="inject into HTML from every host - for finding the page, not for daily use",
    )
    parser.add_argument(
        "--ignore-host",
        action="append",
        metavar="REGEX",
        help="host regex to tunnel untouched (replaces the default mmtls host list)",
    )
    parser.add_argument(
        "--no-ignore-hosts",
        action="store_true",
        help="intercept everything, WeChat's own mmtls transport included",
    )
    parser.add_argument(
        "--stream-large-bodies",
        metavar="SIZE",
        default=proxy_module.DEFAULT_STREAM_LARGE_BODIES,
        help="pass bodies over SIZE through unbuffered, so media cannot stall behind the "
        f"proxy (default {proxy_module.DEFAULT_STREAM_LARGE_BODIES}; 'off' to buffer everything)",
    )
    parser.add_argument("--no-save-pages", action="store_true", help="do not keep page HTML")
    parser.add_argument("--no-save-flows", action="store_true", help="do not write a .mitm dump")
    parser.add_argument(
        "--no-resp-snippet",
        action="store_true",
        help="do not report the JSAPI response body alongside a capture",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="prepare the CA, print the plan, listen for nothing"
    )


def _add_fetch_options(parser: argparse.ArgumentParser, defaults: Settings) -> None:
    """Options shared by `download` and `capture` - they fetch the same way."""
    parser.add_argument(
        "--dir", type=Path, default=defaults.download_dir, help="where finished files go"
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="ignore any .part file and start the download from zero",
    )
    parser.add_argument("--force", action="store_true", help="overwrite a file already there")
    parser.add_argument(
        "--user-agent",
        default=downloader.DEFAULT_USER_AGENT,
        help="what to ask the CDN as (default: the desktop Chrome string that works)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=downloader.DEFAULT_TIMEOUT,
        help=f"per-request timeout in seconds (default {downloader.DEFAULT_TIMEOUT:g})",
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
    _add_proxy_options(proxy, defaults)

    ca_cmd = sub.add_parser(
        "ca",
        help="create the proxy CA if needed and print how to trust it",
    )
    ca_cmd.add_argument("--ca-dir", type=Path, default=defaults.ca_dir, help="CA directory")
    ca_cmd.add_argument("--app-id", default=ca.FLATPAK_APP_ID, help="Flatpak app id to check for")

    download = sub.add_parser(
        "download",
        help="download a captured url directly (no proxy) and decrypt its head",
        description=(
            "Fetches the media URL straight from the CDN - the proxy is not involved - "
            "streaming it to disk and XORing the encrypted first 128 KiB as it goes. An "
            "interrupted run leaves a .part file that the next one resumes from."
        ),
    )
    download.add_argument("url", nargs="?", help="the download url (url + url_token)")
    download.add_argument("-k", "--key", help="the decode_key, as the decimal string it is")
    download.add_argument("-o", "--out", type=Path, help="exact output path")
    download.add_argument(
        "--from-capture",
        action="store_true",
        help="take the url and key from the newest captures log instead of the command line",
    )
    download.add_argument(
        "--index",
        type=int,
        default=0,
        help="which entry of that log to take (default 0, as --list numbers them)",
    )
    download.add_argument(
        "--list", action="store_true", help="list what the newest captures log holds, and stop"
    )
    download.add_argument(
        "--capture-dir",
        type=Path,
        default=defaults.capture_dir,
        help="where to look for captures-*.jsonl",
    )
    download.add_argument(
        "--no-decrypt",
        action="store_true",
        help="write the file exactly as served, still encrypted - for `wxclip decrypt` later",
    )
    _add_fetch_options(download, defaults)

    decrypt = sub.add_parser(
        "decrypt",
        help="decrypt an already downloaded file's first 128 KiB from its decode_key",
        description=(
            "For re-running a decrypt without re-downloading. XOR is its own inverse, so "
            "this also re-encrypts a file that was already decrypted."
        ),
    )
    decrypt.add_argument("file", type=Path, help="the downloaded file")
    decrypt.add_argument(
        "-k", "--key", required=True, help="the decode_key, as the decimal string it is"
    )
    decrypt.add_argument("-o", "--out", type=Path, help="write here instead of alongside the input")
    decrypt.add_argument(
        "--in-place",
        action="store_true",
        help="rewrite only the head of the file itself - no copy, whatever its size",
    )

    capture = sub.add_parser(
        "capture",
        help="inject, capture, download and decrypt in one go",
        description=(
            "Runs the proxy and, the moment the hook script reports a media response, "
            "downloads and decrypts it. Stops once it has --count files. A capture event "
            "carries the whole visible feed, so --index picks which entry of it to take."
        ),
    )
    _add_proxy_options(capture, defaults)
    _add_fetch_options(capture, defaults)
    capture.add_argument(
        "--index", type=int, default=0, help="which media entry of a capture to take (default 0)"
    )
    capture.add_argument(
        "--all", action="store_true", help="take every media entry, not just one per capture"
    )
    capture.add_argument(
        "--max-size", metavar="SIZE", help="skip clips larger than this (e.g. 200m)"
    )
    capture.add_argument(
        "--count",
        type=int,
        default=1,
        help="stop after this many files (default 1; 0 keeps the proxy running)",
    )
    capture.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="give up if nothing has been captured after this many seconds (default: no limit)",
    )

    serve = sub.add_parser(
        "serve",
        help="serve the admin / download dashboard",
        description=(
            "Runs a local web panel over the same capture logs the CLI reads: the whole "
            "captured feed in one list, the proxy's status, and a download button per "
            "clip. It never downloads anything it did not capture itself, and it does not "
            "run the proxy - start that with `wxclip proxy` alongside it."
        ),
    )
    serve.add_argument("--host", default=defaults.server_host, help="listen address")
    serve.add_argument("--port", type=int, default=defaults.server_port, help="listen port")
    serve.add_argument(
        "--dir", type=Path, default=defaults.download_dir, help="where finished files go"
    )
    serve.add_argument(
        "--capture-dir",
        type=Path,
        default=defaults.capture_dir,
        help="where to look for captures-*.jsonl",
    )
    serve.add_argument(
        "--proxy-host", default=defaults.proxy_host, help="proxy address to report the status of"
    )
    serve.add_argument(
        "--proxy-port",
        type=int,
        default=defaults.proxy_port,
        help="proxy port to report the status of",
    )

    for name, description in PLANNED.items():
        sub.add_parser(name, help=f"(planned) {description}")

    return parser


class ProgressPrinter:
    """A download's progress on one line.

    Rewritten in place on a terminal; on anything else the same line would append forever,
    so it is printed at intervals instead. Progress goes to stderr so that piping the
    proxy's own output somewhere stays readable.
    """

    def __init__(self, stream: TextIO | None = None, interval: float = 10.0) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.interval = interval
        self._last = 0.0
        self._open = False

    def __call__(self, progress: downloader.Progress) -> None:
        now = time.monotonic()
        if not self.tty and now - self._last < self.interval:
            return
        self._last = now
        self.stream.write("  " + progress.line().ljust(56) + ("\r" if self.tty else "\n"))
        self.stream.flush()
        self._open = self.tty

    def close(self) -> None:
        """End a line left dangling by `\\r`, so the next print starts clean."""
        if self._open:
            self.stream.write("\n")
            self.stream.flush()
            self._open = False


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def cmd_ca(args: argparse.Namespace) -> int:
    try:
        ca.ensure_ca(args.ca_dir)
    except ImportError:
        print(MISSING_MITMPROXY, file=sys.stderr)
        return 3
    print(ca.trust_hint(args.ca_dir, args.app_id))
    return 0


def _proxy_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env(dotenv=Path(".env"))
    return Settings(
        proxy_host=args.host,
        proxy_port=args.port,
        server_host=settings.server_host,
        server_port=settings.server_port,
        download_dir=getattr(args, "dir", settings.download_dir),
        capture_dir=args.capture_dir,
        ca_dir=args.ca_dir,
    )


def _proxy_options(args: argparse.Namespace, settings: Settings) -> dict:
    ignore_hosts: tuple[str, ...] = ()
    if not args.no_ignore_hosts:
        ignore_hosts = tuple(args.ignore_host or proxy_module.DEFAULT_IGNORE_HOSTS)
    return proxy_module.proxy_options(
        settings,
        ignore_hosts=ignore_hosts,
        save_flows=not args.no_save_flows,
        stream_large_bodies=None if args.stream_large_bodies == "off" else args.stream_large_bodies,
    )


def cmd_proxy(args: argparse.Namespace) -> int:
    settings = _proxy_settings(args)
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
    options = _proxy_options(args, settings)

    if args.dry_run:
        print(proxy_module.describe(settings, addon, options))
        print(f"\n{ca.trust_hint(settings.ca_dir)}")
        return 0

    print(f"CA: {cert}")
    return proxy_module.run(settings, addon=addon, options=options)


def _log_entries(capture_dir: Path) -> tuple[Path, list]:
    log = pipeline.newest_capture_log(capture_dir)
    if log is None:
        raise FileNotFoundError(f"no captures-*.jsonl under {capture_dir} - run `wxclip proxy`")
    return log, pipeline.media_from_log(pipeline.read_capture_log(log))


def cmd_download(args: argparse.Namespace) -> int:
    media = None
    if args.list or args.from_capture:
        if args.url:
            return _fail("give a url or --from-capture, not both")
        try:
            log, entries = _log_entries(args.capture_dir)
        except FileNotFoundError as exc:
            return _fail(str(exc))
        if args.list:
            print(f"{len(entries)} media entries with a decode_key in {log}")
            for i, entry in enumerate(entries):
                size = human_size(entry.file_size) if entry.file_size else "size unknown"
                print(f"  [{i}] {size:>12}  {entry.decode_key}  {entry.url[:70]}")
            return 0
        if not 0 <= args.index < len(entries):
            return _fail(f"--index {args.index} but {log} holds {len(entries)} entries")
        media = entries[args.index]

    url = args.url or (media.download_url if media else "")
    key = args.key or (media.decode_key if media else "")
    if not url:
        return _fail("nothing to download: pass a url, or --from-capture")
    if not key and not args.no_decrypt:
        return _fail(
            "no --key: without the decode_key the first 128 KiB stay unplayable "
            "(pass --no-decrypt to download the ciphertext anyway)"
        )

    if args.out:
        dest = args.out
    else:
        name = downloader.suggest_filename(
            media.url if media else url,
            description=media.description if media else "",
            nickname=media.nickname if media else "",
        )
        dest = args.dir / name

    printer = ProgressPrinter()
    try:
        result = downloader.download(
            url,
            dest,
            decode_key=None if args.no_decrypt else key,
            expected_size=media.file_size if media else None,
            resume=not args.no_resume,
            overwrite=args.force,
            user_agent=args.user_agent,
            timeout=args.timeout,
            on_progress=printer,
        )
    except (DownloadError, KeystreamError) as exc:
        printer.close()
        return _fail(str(exc))
    printer.close()
    print(result.summary())
    return 0 if args.no_decrypt or result.looks_like_mp4 else 1


def cmd_decrypt(args: argparse.Namespace) -> int:
    src: Path = args.file
    if not src.is_file():
        return _fail(f"{src} is not a file")
    try:
        if args.in_place:
            if args.out:
                return _fail("--in-place and --out ask for different things")
            result = decrypt_module.decrypt_in_place(src, args.key)
        else:
            dest = args.out or src.with_name(f"{src.stem}-decrypted{src.suffix or '.mp4'}")
            result = decrypt_module.decrypt_file(src, dest, args.key)
    except (KeystreamError, ValueError, OSError) as exc:
        return _fail(str(exc))
    print(result.summary())
    return 0 if result.looks_like_mp4 else 1


def cmd_capture(args: argparse.Namespace) -> int:
    settings = _proxy_settings(args)
    try:
        max_size = downloader.parse_size(args.max_size) if args.max_size else None
    except ValueError as exc:
        return _fail(str(exc))
    try:
        cert = ca.ensure_ca(settings.ca_dir)
    except ImportError:
        print(MISSING_MITMPROXY, file=sys.stderr)
        return 3

    printer = ProgressPrinter()

    def echo(line: str) -> None:
        printer.close()  # never let a \r line swallow the next message
        print(line, flush=True)

    runner = pipeline.CaptureRunner(
        args.dir,
        index=args.index,
        all_media=args.all,
        max_size=max_size,
        stop_after=max(0, args.count),
        overwrite=args.force,
        echo=echo,
        on_progress=lambda _media, progress: printer(progress),
    )
    addon = proxy_module.build_addon(
        settings,
        page_hosts=tuple(args.page_host) if args.page_host else inject.DEFAULT_PAGE_HOSTS,
        inject_any_html=args.any_html,
        include_snippet=not args.no_resp_snippet,
        save_pages=not args.no_save_pages,
        echo=echo,
        on_event=runner.on_event,
    )
    options = _proxy_options(args, settings)

    if args.dry_run:
        print(proxy_module.describe(settings, addon, options))
        print(f"\n{ca.trust_hint(settings.ca_dir)}")
        return 0

    def on_ready(stop) -> None:
        runner.on_finished = stop
        if args.wait > 0:
            # Nothing captured in time means WeChat is not going through the proxy;
            # the operator wants their prompt back, not an idle listener.
            import threading

            timer = threading.Timer(args.wait, stop)
            timer.daemon = True
            timer.start()

    print(f"CA: {cert}")
    print(f"saving into {args.dir}")
    proxy_module.run(settings, addon=addon, options=options, echo=echo, on_ready=on_ready)
    runner.close()
    printer.close()
    print(runner.summary())
    if runner.failures:
        return 1
    return 0 if runner.results else 2


def cmd_serve(args: argparse.Namespace) -> int:
    defaults = Settings.from_env(dotenv=Path(".env"))
    settings = Settings(
        proxy_host=args.proxy_host,
        proxy_port=args.proxy_port,
        server_host=args.host,
        server_port=args.port,
        download_dir=args.dir,
        capture_dir=args.capture_dir,
        ca_dir=defaults.ca_dir,
    )
    try:
        return server_module.serve(settings)
    except OSError as exc:  # the usual one: something already has the port
        return _fail(f"cannot listen on {args.host}:{args.port}: {exc}")


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
    commands = {
        "proxy": cmd_proxy,
        "ca": cmd_ca,
        "download": cmd_download,
        "decrypt": cmd_decrypt,
        "capture": cmd_capture,
        "serve": cmd_serve,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
