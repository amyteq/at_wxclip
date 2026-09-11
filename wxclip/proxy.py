"""MITM proxy: inject the hook script, serve it, take the capture POST back.

Three things happen here, and the middle one is why a passive sniffer is not enough:

1. every flow is summarised into `captures/traffic-*.jsonl`, which is the actual output of
   the M0 spike - it answers "does the Linux client load the Channels H5 page at all";
2. HTML from the Channels host gets `web/inject.js` appended, and any request under
   `/__wxclip/` is answered by us instead of being forwarded - that is how the script gets
   in and how it reports back;
3. what it reports (`url`, `url_token`, `decode_key`, `file_size`) is logged verbatim.

mitmproxy is imported lazily so that `import wxclip.proxy` - and therefore the test suite
and CI - works without it. The addon only ever duck-types the flow objects it is handed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wxclip import __version__, inject
from wxclip.capture import CaptureError, CaptureLog, format_event, parse_event
from wxclip.config import Settings

#: Hosts we do not try to MITM. WeChat's own transport (mmtls) is not TLS - it
#: arrives as plain HTTP POSTs to `/mmtls/<id>` with an opaque body, so there is
#: nothing to read and nothing to inject, and leaving login and messaging alone is
#: worth more than the log lines. `--ignore-host` replaces this list,
#: `--no-ignore-hosts` clears it.
#:
#: The region prefix varies - this machine's client talks to `sgshort.wechat.com`,
#: while the reference implementation's world is `short.weixin.qq.com` - hence the
#: leading wildcard. Observed on 2026-09-11: sgshort, sgminorshort, dns.wechat.com.
DEFAULT_IGNORE_HOSTS: tuple[str, ...] = (
    r"^[a-z0-9-]*(long|short|minorshort|dns)\.(wechat\.com|weixin\.qq\.com):",
)

#: Responses over this size are passed through unbuffered, so a media download
#: cannot stall behind the proxy. Well above any HTML page, which has to stay
#: buffered to be injectable.
DEFAULT_STREAM_LARGE_BODIES = "1m"

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def echo_line(line: str) -> None:
    """Print unbuffered: the proxy is usually watched through a redirected log."""
    print(line, flush=True)


@dataclass
class Stats:
    flows: int = 0
    injected: int = 0
    events: int = 0
    captures: int = 0
    errors: int = 0
    html_hosts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        hosts = ", ".join(f"{h} x{n}" for h, n in sorted(self.html_hosts.items())) or "none"
        return (
            f"flows={self.flows} injected={self.injected} events={self.events} "
            f"captures={self.captures} errors={self.errors}\nhtml hosts: {hosts}"
        )


class WxclipAddon:
    """The whole proxy behaviour, in a form that can be driven by fake flows in tests."""

    def __init__(
        self,
        *,
        script: str,
        page_hosts: Sequence[str] = inject.DEFAULT_PAGE_HOSTS,
        inject_any_html: bool = False,
        strip_csp: bool = True,
        capture_log: CaptureLog | None = None,
        traffic_log: CaptureLog | None = None,
        page_dir: Path | None = None,
        echo: Callable[[str], None] = echo_line,
        response_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.script = script
        self.page_hosts = tuple(page_hosts)
        self.inject_any_html = inject_any_html
        self.strip_csp = strip_csp
        self.capture_log = capture_log
        self.traffic_log = traffic_log
        self.page_dir = page_dir
        self.echo = echo
        self._response_factory = response_factory
        self.stats = Stats()

    # -- mitmproxy hooks ---------------------------------------------------------------

    def request(self, flow: Any) -> None:
        path = flow.request.path.split("?", 1)[0]
        if path == inject.SCRIPT_PATH:
            self._serve_script(flow)
        elif path == inject.CAPTURE_PATH:
            self._take_capture(flow)
        elif path == inject.PING_PATH:
            flow.is_wxclip = True
            flow.response = self._make(
                200,
                f"wxclip {__version__} proxy is intercepting {flow.request.pretty_host}\n",
                {"content-type": "text/plain; charset=utf-8"},
            )

    def response(self, flow: Any) -> None:
        if getattr(flow, "is_wxclip", False):
            return
        host = flow.request.pretty_host
        content_type = flow.response.headers.get("content-type", "")
        self.stats.flows += 1
        if inject.is_html(content_type):
            self.stats.html_hosts[host] = self.stats.html_hosts.get(host, 0) + 1

        injected = False
        if inject.should_inject(
            host=host,
            content_type=content_type,
            status_code=flow.response.status_code,
            page_hosts=self.page_hosts,
            any_host=self.inject_any_html,
        ):
            injected = self._inject(flow)

        self._log_flow(flow, injected=injected)

    def error(self, flow: Any) -> None:
        self.stats.errors += 1
        message = str(getattr(flow, "error", "") or "unknown error")
        self._record(
            "error",
            {
                "at": _now(),
                "host": flow.request.pretty_host if getattr(flow, "request", None) else "",
                "path": flow.request.path if getattr(flow, "request", None) else "",
                "error": message,
            },
        )
        self.echo(f"!! error {getattr(flow.request, 'pretty_host', '?')}: {message}")

    def tls_failed_client(self, data: Any) -> None:
        """The client refused our certificate - i.e. the CA is not trusted (yet)."""
        self.stats.errors += 1
        conn = getattr(data, "conn", None)
        address = getattr(getattr(data, "context", None), "server", None)
        host = getattr(address, "address", None) or getattr(conn, "sni", None) or "?"
        message = getattr(conn, "error", "") or "client rejected the TLS handshake"
        self._record("tls_failed_client", {"at": _now(), "host": str(host), "error": message})
        self.echo(f"!! TLS refused by the client for {host}: {message} (CA not trusted?)")

    def done(self) -> None:
        self.echo("\n" + self.stats.summary())
        for log in (self.traffic_log, self.capture_log):
            if log is not None and log.path.exists():
                self.echo(f"log: {log.path}")

    # -- our own endpoints -------------------------------------------------------------

    def _serve_script(self, flow: Any) -> None:
        flow.is_wxclip = True
        flow.response = self._make(
            200,
            self.script,
            {
                "content-type": "application/javascript; charset=utf-8",
                "cache-control": "no-store",
                "access-control-allow-origin": "*",
            },
        )
        self.echo(f"-> served hook script to {flow.request.pretty_host}")

    def _take_capture(self, flow: Any) -> None:
        flow.is_wxclip = True
        if flow.request.method == "OPTIONS":
            flow.response = self._make(204, b"", self._cors())
            return
        try:
            event = parse_event(flow.request.get_content() or b"")
        except CaptureError as exc:
            self.echo(f"!! bad capture payload from {flow.request.pretty_host}: {exc}")
            flow.response = self._make(400, f"{exc}\n", self._cors())
            return

        self.stats.events += 1
        self.stats.captures += len(event.captures)
        if self.capture_log is not None:
            self.capture_log.append_event(event)
        self.echo(format_event(event))
        for media in event.media:
            if media.decode_key and not media.decode_key_ok:
                self.echo(
                    f"!! decode_key {media.decode_key!r} is not a plain digit string - "
                    "something parsed it as a number and the keystream will be wrong"
                )
        flow.response = self._make(204, b"", self._cors())

    def _cors(self) -> dict[str, str]:
        return {
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "POST, OPTIONS",
            "cache-control": "no-store",
        }

    def _make(self, status: int, content: bytes | str, headers: dict[str, str]) -> Any:
        if self._response_factory is None:
            from mitmproxy import http

            self._response_factory = http.Response.make
        return self._response_factory(status, content, headers)

    # -- injection ---------------------------------------------------------------------

    def _inject(self, flow: Any) -> bool:
        if self.strip_csp:
            for header in inject.STRIPPED_HEADERS:
                if header in flow.response.headers:
                    del flow.response.headers[header]
        try:
            html = flow.response.text
        except (ValueError, UnicodeDecodeError) as exc:  # e.g. an encoding we cannot read
            self.echo(f"!! could not read {flow.request.pretty_host} as text: {exc}")
            return False
        if html is None:
            return False
        if inject.is_injected(html):
            return False

        self._save_page(flow, html)
        flow.response.text = inject.inject_html(html)
        self.stats.injected += 1
        self.echo(f"-> injected hook script into {flow.request.pretty_host}{flow.request.path}")
        return True

    def _save_page(self, flow: Any, html: str) -> None:
        """Keep the original page: whether the H5 page is the one the reference
        implementation expects is exactly what M0 has to answer."""
        if self.page_dir is None:
            return
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        name = _UNSAFE_FILENAME.sub("_", f"{flow.request.pretty_host}{flow.request.path}")[:120]
        self.page_dir.mkdir(parents=True, exist_ok=True)
        (self.page_dir / f"{stamp}-{name}.html").write_text(html, encoding="utf-8")

    # -- logging -----------------------------------------------------------------------

    def _log_flow(self, flow: Any, *, injected: bool) -> None:
        record = {
            "at": _now(),
            "method": flow.request.method,
            "host": flow.request.pretty_host,
            "path": flow.request.path,
            "status": flow.response.status_code,
            "content_type": flow.response.headers.get("content-type", ""),
            "length": flow.response.headers.get("content-length", ""),
            "injected": injected,
        }
        self._record("flow", record)

    def _record(self, kind: str, record: dict[str, Any]) -> None:
        if self.traffic_log is not None:
            self.traffic_log.append({"kind": kind} | record)


def build_addon(
    settings: Settings,
    *,
    page_hosts: Sequence[str] = inject.DEFAULT_PAGE_HOSTS,
    inject_any_html: bool = False,
    include_snippet: bool = True,
    save_pages: bool = True,
    echo: Callable[[str], None] = echo_line,
) -> WxclipAddon:
    return WxclipAddon(
        script=inject.hook_script(version=__version__, include_snippet=include_snippet),
        page_hosts=page_hosts,
        inject_any_html=inject_any_html,
        capture_log=CaptureLog(settings.capture_dir, "captures"),
        traffic_log=CaptureLog(settings.capture_dir, "traffic"),
        page_dir=settings.capture_dir / "pages" if save_pages else None,
        echo=echo,
    )


def proxy_options(
    settings: Settings,
    *,
    ignore_hosts: Iterable[str] = DEFAULT_IGNORE_HOSTS,
    save_flows: bool = True,
    stream_large_bodies: str | None = DEFAULT_STREAM_LARGE_BODIES,
) -> dict[str, Any]:
    """Options handed to mitmproxy after its addons are loaded.

    `connection_strategy=lazy` matters: it keeps mitmproxy from opening an upstream
    connection for requests we answer ourselves, and there is no upstream for those.
    """
    options: dict[str, Any] = {
        "confdir": str(Path(settings.ca_dir).expanduser()),
        "listen_host": settings.proxy_host,
        "listen_port": settings.proxy_port,
        "connection_strategy": "lazy",
        "ignore_hosts": list(ignore_hosts),
        "stream_large_bodies": stream_large_bodies or None,
        # `flow_detail` belongs to the dumper addon, which we do not load: our own
        # handlers do the printing.
        "termlog_verbosity": "info",
    }
    if save_flows:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        options["save_stream_file"] = str(settings.capture_dir / f"flows-{stamp}.mitm")
    return options


def run(
    settings: Settings,
    *,
    addon: WxclipAddon,
    options: dict[str, Any] | None = None,
    echo: Callable[[str], None] = echo_line,
) -> int:
    """Run the proxy until interrupted. Returns a process exit code."""
    import asyncio

    settings.capture_dir.mkdir(parents=True, exist_ok=True)
    resolved = options if options is not None else proxy_options(settings)

    async def serve() -> None:
        # DumpMaster wants a running loop to attach to, so it is built in here.
        import contextlib
        import signal

        from mitmproxy import options as mitm_options
        from mitmproxy.tools.dump import DumpMaster

        master = DumpMaster(mitm_options.Options(), with_termlog=True, with_dumper=False)
        master.addons.add(addon)
        master.options.update(**resolved)

        # Without these, a `kill` skips mitmproxy's shutdown: no summary, and the flow
        # dump is left unflushed. Only ctrl-c would be clean.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, master.shutdown)
        echo(
            f"wxclip proxy listening on http://{settings.proxy_host}:{settings.proxy_port} "
            "(HTTP proxy; point WeChat at it)\npress ctrl-c to stop"
        )
        await master.run()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return 0
    return 0


def describe(settings: Settings, addon: WxclipAddon, options: dict[str, Any]) -> str:
    """What `--dry-run` prints: everything the operator needs without binding a port."""
    return json.dumps(
        {
            "proxy": f"{settings.proxy_host}:{settings.proxy_port}",
            "ca_dir": str(settings.ca_dir),
            "capture_dir": str(settings.capture_dir),
            "page_hosts": list(addon.page_hosts),
            "inject_any_html": addon.inject_any_html,
            "endpoints": {
                "script": inject.SCRIPT_PATH,
                "capture": inject.CAPTURE_PATH,
                "ping": inject.PING_PATH,
            },
            "script_bytes": len(addon.script),
            "mitmproxy_options": options,
        },
        indent=2,
        ensure_ascii=False,
    )
