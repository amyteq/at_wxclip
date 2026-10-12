"""The addon, driven by stand-in flows so no proxy has to run.

Only the mitmproxy `Response.make` factory is faked; the handlers see the same attribute
names mitmproxy hands them.
"""

import json
import re
from types import SimpleNamespace

import pytest

from wx_dlh import inject
from wx_dlh.capture import CaptureLog
from wx_dlh.config import Settings
from wx_dlh.proxy import (
    DEFAULT_IGNORE_HOSTS,
    DEFAULT_STREAM_LARGE_BODIES,
    WxDlhAddon,
    proxy_options,
)

CAPTURE_BODY = json.dumps(
    {
        "kind": "capture",
        "api": "getVideoInfo",
        "err_msg": "finderH5ExtTransfer:ok",
        "media": [
            {
                "url": "https://finder.video.qq.com/x",
                "url_token": "?token=1",
                "decode_key": "13835058055282163712",
                "decode_key_json_type": "string",
                "file_size": 4194304,
            }
        ],
    }
).encode()


def make_response(status_code=200, content=b"", headers=None):
    return SimpleNamespace(status_code=status_code, content=content, headers=dict(headers or {}))


class FakeFlow:
    """Enough of a mitmproxy flow for the handlers under test."""

    def __init__(self, *, host, path="/", method="GET", body=b"", response=None):
        self.request = SimpleNamespace(
            method=method,
            path=path,
            pretty_host=host,
            get_content=lambda: body,
        )
        self.response = response
        self.error = None


def html_flow(
    host="channels.weixin.qq.com", path="/web/pages/feed", html="<body>page</body>", **kw
):
    headers = {"content-type": "text/html; charset=utf-8"} | kw.pop("headers", {})
    response = SimpleNamespace(status_code=kw.pop("status_code", 200), headers=headers, text=html)
    return FakeFlow(host=host, path=path, response=response)


@pytest.fixture
def addon(tmp_path):
    return WxDlhAddon(
        script="/* hook */",
        capture_log=CaptureLog(tmp_path, "captures"),
        traffic_log=CaptureLog(tmp_path, "traffic"),
        page_dir=tmp_path / "pages",
        echo=lambda line: None,
        response_factory=make_response,
    )


def test_serves_the_hook_script_without_going_upstream(addon):
    flow = FakeFlow(host="channels.weixin.qq.com", path=inject.SCRIPT_PATH)
    addon.request(flow)
    assert flow.response.status_code == 200
    assert flow.response.content == "/* hook */"
    assert "javascript" in flow.response.headers["content-type"]
    assert flow.is_wx_dlh is True


def test_answers_the_ping_path_on_any_host(addon):
    flow = FakeFlow(host="example.com", path=inject.PING_PATH)
    addon.request(flow)
    assert flow.response.status_code == 200
    assert "wx-dlh" in flow.response.content


def test_takes_a_capture_and_logs_it(addon):
    flow = FakeFlow(
        host="channels.weixin.qq.com",
        path=inject.CAPTURE_PATH,
        method="POST",
        body=CAPTURE_BODY,
    )
    addon.request(flow)
    assert flow.response.status_code == 204
    assert addon.stats.events == 1
    assert addon.stats.captures == 1
    record = json.loads(addon.capture_log.path.read_text(encoding="utf-8").splitlines()[0])
    assert record["media"][0]["decode_key"] == "13835058055282163712"


def test_a_capture_preflight_is_answered_without_parsing(addon):
    flow = FakeFlow(host="channels.weixin.qq.com", path=inject.CAPTURE_PATH, method="OPTIONS")
    addon.request(flow)
    assert flow.response.status_code == 204
    assert addon.stats.events == 0


def test_a_malformed_capture_is_rejected_not_crashed(addon):
    lines = []
    addon.echo = lines.append
    flow = FakeFlow(
        host="channels.weixin.qq.com", path=inject.CAPTURE_PATH, method="POST", body=b"{oops"
    )
    addon.request(flow)
    assert flow.response.status_code == 400
    assert addon.stats.events == 0
    assert any("bad capture payload" in line for line in lines)


def test_a_key_that_lost_precision_is_called_out(addon):
    lines = []
    addon.echo = lines.append
    body = json.dumps({"kind": "capture", "media": [{"url": "u", "decode_key": "1.38e+19"}]})
    flow = FakeFlow(
        host="channels.weixin.qq.com",
        path=inject.CAPTURE_PATH,
        method="POST",
        body=body.encode(),
    )
    addon.request(flow)
    assert any("not a plain digit string" in line for line in lines)


def test_other_requests_are_left_alone(addon):
    flow = FakeFlow(host="channels.weixin.qq.com", path="/web/pages/feed")
    addon.request(flow)
    assert flow.response is None


def test_injects_into_channels_html_and_drops_the_csp(addon):
    flow = html_flow(headers={"content-security-policy": "script-src 'none'"})
    addon.response(flow)
    assert inject.MARKER in flow.response.text
    assert "content-security-policy" not in flow.response.headers
    assert addon.stats.injected == 1
    assert addon.stats.html_hosts == {"channels.weixin.qq.com": 1}


def test_saves_the_page_as_it_arrived(addon):
    addon.response(html_flow(html="<body>original</body>"))
    saved = list((addon.page_dir).glob("*.html"))
    assert len(saved) == 1
    assert saved[0].read_text(encoding="utf-8") == "<body>original</body>"


def test_does_not_inject_twice_into_an_already_injected_page(addon):
    flow = html_flow(html=inject.inject_html("<body>page</body>"))
    addon.response(flow)
    assert flow.response.text.count(inject.MARKER) == 1
    assert addon.stats.injected == 0


@pytest.mark.parametrize(
    "flow",
    [
        html_flow(host="res.wx.qq.com"),
        html_flow(headers={"content-type": "application/json"}),
        html_flow(status_code=404),
    ],
)
def test_leaves_everything_else_untouched(addon, flow):
    original = flow.response.text
    addon.response(flow)
    assert flow.response.text == original
    assert addon.stats.injected == 0


def test_every_flow_lands_in_the_traffic_log(addon):
    addon.response(html_flow())
    addon.response(html_flow(host="res.wx.qq.com", path="/a.js"))
    records = [
        json.loads(line) for line in addon.traffic_log.path.read_text(encoding="utf-8").splitlines()
    ]
    assert [r["host"] for r in records] == ["channels.weixin.qq.com", "res.wx.qq.com"]
    assert [r["injected"] for r in records] == [True, False]
    assert addon.stats.flows == 2


def test_a_flow_error_is_recorded(addon):
    flow = FakeFlow(host="long.weixin.qq.com", path="/")
    flow.error = "connection killed"
    addon.error(flow)
    record = json.loads(addon.traffic_log.path.read_text(encoding="utf-8").splitlines()[0])
    assert record["kind"] == "error"
    assert record["error"] == "connection killed"
    assert addon.stats.errors == 1


def test_a_client_rejecting_our_certificate_is_recorded(addon):
    lines = []
    addon.echo = lines.append
    data = SimpleNamespace(
        conn=SimpleNamespace(error="unknown ca", sni="channels.weixin.qq.com"),
        context=SimpleNamespace(server=SimpleNamespace(address=("channels.weixin.qq.com", 443))),
    )
    addon.tls_failed_client(data)
    assert any("CA not trusted" in line for line in lines)
    assert addon.stats.errors == 1


@pytest.mark.parametrize(
    "host",
    [
        # what this machine's client actually talks to, observed 2026-09-11
        "sgshort.wechat.com:80",
        "sgminorshort.wechat.com:80",
        "dns.wechat.com:443",
        # and the reference implementation's world
        "short.weixin.qq.com:80",
        "long.weixin.qq.com:443",
        "minorshort.weixin.qq.com:80",
    ],
)
def test_wechat_transport_hosts_are_left_alone_by_default(host):
    """mmtls is not TLS and it carries login; intercepting it buys nothing."""
    assert any(re.search(pattern, host) for pattern in DEFAULT_IGNORE_HOSTS)


@pytest.mark.parametrize(
    "host",
    [
        "channels.weixin.qq.com:443",  # the page we must inject into
        "finder.video.qq.com:443",  # the media we must capture
        "res.wx.qq.com:443",
        "wx.qlogo.cn:443",
        "mmhead.c2c.wechat.com:443",
    ],
)
def test_the_hosts_we_need_are_still_intercepted(host):
    assert not any(re.search(pattern, host) for pattern in DEFAULT_IGNORE_HOSTS)


def test_media_sized_bodies_are_not_buffered(tmp_path):
    """A buffered media response stalls playback; HTML stays small enough to rewrite."""
    options = proxy_options(Settings(ca_dir=tmp_path), save_flows=False)
    assert options["stream_large_bodies"] == DEFAULT_STREAM_LARGE_BODIES
    off = proxy_options(Settings(ca_dir=tmp_path), save_flows=False, stream_large_bodies=None)
    assert off["stream_large_bodies"] is None


def test_options_keep_mitmproxy_from_dialing_out_for_our_own_endpoints(tmp_path):
    settings = Settings(proxy_host="127.0.0.1", proxy_port=8899, ca_dir=tmp_path / "ca")
    options = proxy_options(settings, save_flows=False)
    assert options["connection_strategy"] == "lazy"
    assert options["listen_port"] == 8899
    assert options["confdir"] == str(tmp_path / "ca")
    assert "save_stream_file" not in options
    assert options["ignore_hosts"]


def test_a_capture_is_handed_to_the_event_hook(tmp_path):
    """`wx-dlh capture` hangs its downloader here; it runs on the proxy's event loop."""
    seen = []
    addon = WxDlhAddon(
        script="/* hook */",
        echo=lambda line: None,
        response_factory=make_response,
        on_event=seen.append,
    )
    flow = FakeFlow(
        host="channels.weixin.qq.com",
        path=inject.CAPTURE_PATH,
        method="POST",
        body=CAPTURE_BODY,
    )
    addon.request(flow)
    assert len(seen) == 1
    assert seen[0].captures[0].decode_key == "13835058055282163712"
    assert flow.response.status_code == 204


def test_a_broken_event_hook_does_not_break_the_capture_channel(tmp_path):
    """A download that blows up must still leave the page able to report the next clip."""
    said = []

    def explode(event):
        raise RuntimeError("downloader fell over")

    addon = WxDlhAddon(
        script="/* hook */",
        echo=said.append,
        response_factory=make_response,
        on_event=explode,
    )
    flow = FakeFlow(
        host="channels.weixin.qq.com",
        path=inject.CAPTURE_PATH,
        method="POST",
        body=CAPTURE_BODY,
    )
    addon.request(flow)
    assert flow.response.status_code == 204
    assert any("downloader fell over" in line for line in said)
