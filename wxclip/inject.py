"""What the proxy injects, and where.

The reference implementation pulls its hook script from `res.wx.qq.com` because the
Channels page ships a CSP that blocks inline scripts. We serve the script from the page's
own origin instead: `script-src 'self'` accepts it, and it keeps the whole exchange -
script fetch and capture POST - on one host, so no CORS preflight is involved either.

Everything here is pure string work so it can be tested without a proxy running.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

#: Every wxclip endpoint lives under this path prefix, on whatever host asks for it.
WXCLIP_PREFIX = "/__wxclip/"
SCRIPT_PATH = WXCLIP_PREFIX + "inject.js"
CAPTURE_PATH = WXCLIP_PREFIX + "capture"
PING_PATH = WXCLIP_PREFIX + "ping"

#: Marks an already injected page, so a re-read of the same response stays idempotent.
MARKER = "data-wxclip"

#: Hosts whose HTML gets the hook script. The reference implementation only rewrites
#: `/web/pages/feed`; on Linux the page path is one of the M0 unknowns, so we take any
#: HTML from the Channels host and let the traffic log show what actually loaded.
DEFAULT_PAGE_HOSTS: tuple[str, ...] = ("channels.weixin.qq.com",)

#: Response headers dropped from an injected page: a CSP would otherwise decide whether
#: our script runs, and caching would hide the injection on the next load.
STRIPPED_HEADERS: tuple[str, ...] = (
    "content-security-policy",
    "content-security-policy-report-only",
)


def script_tag(script_path: str = SCRIPT_PATH) -> str:
    return f'<script {MARKER}="1" src="{script_path}"></script>'


def is_injected(html: str) -> bool:
    return MARKER in html


def inject_html(html: str, script_path: str = SCRIPT_PATH) -> str:
    """Put the hook script last in the document, before `</body>` when there is one."""
    if is_injected(html):
        return html
    tag = script_tag(script_path)
    lowered = html.lower()
    for anchor in ("</body>", "</html>"):
        at = lowered.rfind(anchor)
        if at != -1:
            return html[:at] + tag + html[at:]
    return html + tag


def host_matches(host: str, hosts: Sequence[str]) -> bool:
    """True for an exact host or any subdomain of one of `hosts`."""
    host = host.lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in (x.lower() for x in hosts))


def is_html(content_type: str | None) -> bool:
    return bool(content_type) and "text/html" in content_type.lower()


def should_inject(
    *,
    host: str,
    content_type: str | None,
    status_code: int,
    page_hosts: Sequence[str] = DEFAULT_PAGE_HOSTS,
    any_host: bool = False,
) -> bool:
    if status_code != 200 or not is_html(content_type):
        return False
    return any_host or host_matches(host, page_hosts)


def _script_candidates() -> Iterable[Path]:
    """`web/inject.js` in a checkout; the copy hatch force-includes in a wheel."""
    here = Path(__file__).resolve()
    yield here.parents[1] / "web" / "inject.js"
    yield here.parent / "_web" / "inject.js"


def script_source() -> Path:
    for candidate in _script_candidates():
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(c) for c in _script_candidates())
    raise FileNotFoundError(f"hook script not found, looked in: {tried}")


def hook_script(
    *,
    capture_path: str = CAPTURE_PATH,
    version: str = "",
    include_snippet: bool = True,
) -> str:
    """Read `web/inject.js` and fill in what only the proxy knows."""
    template = script_source().read_text(encoding="utf-8")
    return (
        template.replace("__WXCLIP_CAPTURE_PATH__", capture_path)
        .replace("__WXCLIP_VERSION__", version)
        .replace('"__WXCLIP_INCLUDE_SNIPPET__"', "true" if include_snippet else "false")
    )
