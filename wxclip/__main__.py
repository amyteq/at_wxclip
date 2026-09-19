"""Command line entry point: `python -m wxclip` or `wxclip`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from wxclip import __version__, ca, downloader, frames, inject, pipeline, postprocess
from wxclip import decrypt as decrypt_module
from wxclip import extract as extract_module
from wxclip import proxy as proxy_module  # mitmproxy-free at import time
from wxclip import server as server_module
from wxclip import summarize as summarize_module
from wxclip.config import Settings, read_dotenv
from wxclip.downloader import DownloadError, human_size
from wxclip.keystream import KeystreamError
from wxclip.media import MediaError, probe
from wxclip.summarize import SummarizeError

# Subcommands land with their milestones; see docs/development-plan.md.
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


def _add_shot_options(parser: argparse.ArgumentParser) -> None:
    """Options shared by `shots` and `process` - they take stills the same way."""
    parser.add_argument(
        "--scene",
        type=float,
        default=frames.DEFAULT_SCENE_THRESHOLD,
        metavar="SCORE",
        help="how different a frame must be to count as a new shot, 0..1 "
        f"(default {frames.DEFAULT_SCENE_THRESHOLD})",
    )
    parser.add_argument(
        "--every",
        type=float,
        metavar="SEC",
        help="ignore scene detection and take a still every SEC seconds",
    )
    parser.add_argument(
        "--max-shots",
        type=int,
        default=frames.DEFAULT_MAX_SHOTS,
        help=f"keep at most this many stills (default {frames.DEFAULT_MAX_SHOTS})",
    )
    parser.add_argument(
        "--min-shots",
        type=int,
        default=frames.DEFAULT_MIN_SHOTS,
        help="below this many scene changes, fall back to evenly spaced stills "
        f"(default {frames.DEFAULT_MIN_SHOTS})",
    )
    parser.add_argument(
        "--shot-width",
        type=int,
        default=frames.DEFAULT_WIDTH,
        metavar="PX",
        help=f"scale stills down to this width if wider (default {frames.DEFAULT_WIDTH}; "
        "0 keeps the native size)",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=frames.DEFAULT_QUALITY,
        help=f"jpeg quality, 2 is best (default {frames.DEFAULT_QUALITY})",
    )


def _add_ocr_options(parser: argparse.ArgumentParser) -> None:
    """Options shared by `extract` and `process`."""
    parser.add_argument(
        "--lang",
        default=extract_module.DEFAULT_LANG,
        help=f"tesseract languages (default {extract_module.DEFAULT_LANG})",
    )
    parser.add_argument(
        "--psm",
        type=int,
        default=extract_module.DEFAULT_PSM,
        help=f"tesseract page segmentation mode (default {extract_module.DEFAULT_PSM})",
    )
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        help="directory of .traineddata files, for language data installed without root",
    )
    parser.add_argument(
        "--similarity",
        type=float,
        default=extract_module.DEFAULT_SIMILARITY,
        help="above this, two lines count as the same caption read twice "
        f"(default {extract_module.DEFAULT_SIMILARITY})",
    )


def _add_model_options(parser: argparse.ArgumentParser) -> None:
    """Options shared by `summarize` and `process`."""
    parser.add_argument(
        "--model",
        default=summarize_module.DEFAULT_MODEL,
        help=f"which model to ask (default {summarize_module.DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--effort",
        default=summarize_module.DEFAULT_EFFORT,
        choices=["low", "medium", "high", "xhigh", "max"],
        help=f"how hard it should think (default {summarize_module.DEFAULT_EFFORT})",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=summarize_module.DEFAULT_MAX_TOKENS,
        help=f"response ceiling (default {summarize_module.DEFAULT_MAX_TOKENS})",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=summarize_module.DEFAULT_MAX_IMAGES,
        help="how many stills to send alongside the text "
        f"(default {summarize_module.DEFAULT_MAX_IMAGES}; 0 sends text only)",
    )
    parser.add_argument(
        "--image-width",
        type=int,
        default=summarize_module.DEFAULT_IMAGE_WIDTH,
        metavar="PX",
        help=f"downscale those stills to this width (default "
        f"{summarize_module.DEFAULT_IMAGE_WIDTH}; 0 sends them as they are)",
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
    shots = sub.add_parser(
        "shots",
        help="take keyframe screenshots of a downloaded video",
        description=(
            "Picks the moments where the picture actually changes - ffmpeg's scene score - "
            "and writes a still for each into the clip's own directory, alongside a "
            "shots.json saying what was chosen and when. A clip with nothing to detect "
            "falls back to evenly spaced stills."
        ),
    )
    shots.add_argument("video", type=Path, help="a decrypted, playable file")
    shots.add_argument(
        "--out", type=Path, default=defaults.process_dir, help="root for per-clip directories"
    )
    shots.add_argument(
        "--redo", action="store_true", help="take the stills again instead of reusing shots.json"
    )
    _add_shot_options(shots)

    extract = sub.add_parser(
        "extract",
        help="read the text in a clip's stills with OCR",
        description=(
            "OCRs the stills and folds them into one transcript, dropping the captions "
            "that repeat across consecutive frames. Give it a video and it takes the "
            "stills first; give it a directory from an earlier run and it reuses them. "
            "Needs tesseract and the language data - `--check` says whether they are there."
        ),
    )
    extract.add_argument(
        "target", type=Path, nargs="?", help="a video, or a directory holding shots.json"
    )
    extract.add_argument(
        "--out", type=Path, default=defaults.process_dir, help="root for per-clip directories"
    )
    extract.add_argument(
        "--check", action="store_true", help="report whether OCR can run at all, and stop"
    )
    extract.add_argument(
        "--redo", action="store_true", help="run OCR again instead of reusing text.json"
    )
    _add_ocr_options(extract)
    _add_shot_options(extract)

    summarize = sub.add_parser(
        "summarize",
        help="ask a model what the clip was about",
        description=(
            "Sends the extracted text and a few of the stills to the Messages API and "
            "writes summary.md. Give it a video and it runs the two earlier steps first; "
            "give it a directory and it reuses what is in there, which is what makes "
            "iterating on the prompt cheap. Needs ANTHROPIC_API_KEY (or "
            "ANTHROPIC_AUTH_TOKEN); --dry-run prints the request instead of sending it."
        ),
    )
    summarize.add_argument(
        "target", type=Path, nargs="?", help="a video, or a directory holding text.json"
    )
    summarize.add_argument(
        "--out",
        type=Path,
        # No default, so `--text FILE` can tell "write next to the text file" (nothing
        # given) apart from "write here" - which is how the evals/ samples are rerun
        # without dropping output into the fixture directory.
        help=f"root for per-clip directories (default {defaults.process_dir}/)",
    )
    summarize.add_argument(
        "--text", type=Path, help="summarize this text file instead of running OCR"
    )
    summarize.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent, including the prompt, and call nothing",
    )
    summarize.add_argument(
        "--redo", action="store_true", help="redo the earlier steps rather than reusing them"
    )
    _add_model_options(summarize)
    _add_ocr_options(summarize)
    _add_shot_options(summarize)

    process = sub.add_parser(
        "process",
        help="screenshots, text and summary in one go",
        description=(
            "Runs all three steps over a playable file and leaves everything in one "
            "directory. Each artefact is written as soon as it exists, so a failure in "
            "the model call still leaves the stills and the transcript behind."
        ),
    )
    process.add_argument("video", type=Path, help="a decrypted, playable file")
    process.add_argument(
        "--out", type=Path, default=defaults.process_dir, help="root for per-clip directories"
    )
    process.add_argument(
        "--no-summary",
        action="store_true",
        help="stop after the text - no model call, no credentials needed",
    )
    process.add_argument(
        "--redo", action="store_true", help="redo every step instead of reusing earlier output"
    )
    _add_shot_options(process)
    _add_ocr_options(process)
    _add_model_options(process)

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


def _shot_kwargs(args: argparse.Namespace) -> dict:
    return {
        "threshold": args.scene,
        "max_shots": args.max_shots,
        "min_shots": args.min_shots,
        "every": args.every,
        "width": args.shot_width or None,
        "quality": args.quality,
    }


def _ocr_kwargs(args: argparse.Namespace) -> dict:
    return {
        "lang": args.lang,
        "psm": args.psm,
        "tessdata_dir": args.tessdata_dir,
        "threshold": args.similarity,
    }


def _model_kwargs(args: argparse.Namespace) -> dict:
    return {
        "model": args.model,
        "effort": args.effort,
        "max_tokens": args.max_tokens,
        "max_images": args.max_images,
        "image_width": args.image_width or None,
    }


def _env() -> dict[str, str]:
    """`.env` under the real environment, the same precedence `Settings.from_env` uses.

    So a key can live in the git-ignored `.env` next to the proxy settings instead of
    having to be exported into every shell.
    """
    return {**read_dotenv(Path(".env")), **os.environ}


def _resolve(target: Path, root: Path) -> tuple[Path | None, Path]:
    """`(video, bundle directory)` from whichever of the two the operator named."""
    if target.is_dir():
        return postprocess.find_video(target), target
    if target.is_file():
        return target, postprocess.bundle_dir(root, target)
    raise FileNotFoundError(f"{target} is neither a file nor a directory")


def cmd_shots(args: argparse.Namespace) -> int:
    try:
        shots = postprocess.ensure_shots(
            args.video,
            postprocess.bundle_dir(args.out, args.video),
            redo=args.redo,
            **_shot_kwargs(args),
        )
    except (MediaError, OSError) as exc:
        return _fail(str(exc))
    return 0 if shots.shots else 1


def cmd_extract(args: argparse.Namespace) -> int:
    if args.check:
        try:
            print(extract_module.check_tesseract(args.lang))
        except MediaError as exc:
            return _fail(str(exc))
        return 0
    if args.target is None:
        return _fail("give a video or a directory (or --check to test the OCR setup)")

    try:
        video, directory = _resolve(args.target, args.out)
        if video is None:
            return _fail(f"{directory} has no shots.json - run `wxclip shots` on the video first")
        shots = postprocess.ensure_shots(video, directory, redo=args.redo, **_shot_kwargs(args))
        extraction = postprocess.ensure_text(shots, directory, redo=args.redo, **_ocr_kwargs(args))
    except (MediaError, FileNotFoundError, OSError) as exc:
        return _fail(str(exc))
    print(f"-> {directory / extract_module.TEXT_NAME}")
    return 0 if extraction.lines else 1


def _redact(body: dict) -> dict:
    """The request with the image blobs replaced by their size.

    `--dry-run` is for reading the prompt, and four base64 jpegs would bury it.
    """
    shown = json.loads(json.dumps(body))
    for message in shown.get("messages") or []:
        for block in message.get("content") or []:
            source = block.get("source") if isinstance(block, dict) else None
            if isinstance(source, dict) and "data" in source:
                source["data"] = f"<{len(source['data'])} base64 chars>"
    return shown


def cmd_summarize(args: argparse.Namespace) -> int:
    text = ""
    shots = None
    info = None

    try:
        if args.text is not None:
            text = args.text.read_text(encoding="utf-8")
            directory = args.out or args.text.parent
        else:
            if args.target is None:
                return _fail("give a video or a directory, or --text FILE")
            video, directory = _resolve(
                args.target, args.out or Settings.from_env(dotenv=Path(".env")).process_dir
            )
            if video is None:
                return _fail(f"{directory} has no shots.json - run `wxclip shots` first")
            info = probe(video)
            shots = postprocess.ensure_shots(
                video, directory, info=info, redo=args.redo, **_shot_kwargs(args)
            )
            text = postprocess.ensure_text(
                shots, directory, redo=args.redo, **_ocr_kwargs(args)
            ).text

        if args.dry_run:
            # Built with the same arguments the real call would use, so what is printed
            # is the request itself and not an approximation of it.
            content = summarize_module.build_content(
                text,
                summarize_module.pick_images(shots.shots if shots else (), args.max_images),
                info=info,
                image_width=args.image_width or None,
            )
            body = summarize_module.build_body(
                content,
                model=args.model,
                max_tokens=args.max_tokens,
                effort=args.effort,
            )
            print(json.dumps(_redact(body), ensure_ascii=False, indent=2))
            return 0

        summary = summarize_module.summarize(
            text, shots or (), info=info, env=_env(), **_model_kwargs(args)
        )
    except (MediaError, SummarizeError, FileNotFoundError, OSError) as exc:
        return _fail(str(exc))

    path = summarize_module.write_summary(summary, directory)
    print(summary.summary())
    print(f"-> {path}")
    return 0 if summary.ok else 1


def cmd_process(args: argparse.Namespace) -> int:
    try:
        bundle = postprocess.process(
            args.video,
            args.out,
            shot_options=_shot_kwargs(args),
            extract_options=_ocr_kwargs(args),
            summarize_options={**_model_kwargs(args), "env": _env()},
            with_summary=not args.no_summary,
            redo=args.redo,
        )
    except (MediaError, SummarizeError, OSError) as exc:
        return _fail(str(exc))
    print(bundle.report())
    if bundle.model_summary is not None and not bundle.model_summary.ok:
        return 1
    if bundle.extraction is not None and bundle.extraction.is_empty:
        return 1
    return 0


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
        "shots": cmd_shots,
        "extract": cmd_extract,
        "summarize": cmd_summarize,
        "process": cmd_process,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
