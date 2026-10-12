"""What gets injected, and into which responses."""

import pytest

from wx_dlh import inject


def test_injects_before_the_last_body_tag():
    html = "<html><body><p>hi</p></body></html>"
    result = inject.inject_html(html)
    assert result.index(inject.script_tag()) < result.index("</body>")
    assert result.endswith("</body></html>")


def test_injects_before_html_when_there_is_no_body():
    result = inject.inject_html("<html><head></head></html>")
    expected = "<html><head></head>" + inject.script_tag() + "</html>"
    assert result == expected
    assert inject.script_tag() == '<script data-wx-dlh="1" src="/__wx-dlh/inject.js"></script>'


def test_appends_when_there_is_no_closing_tag_at_all():
    assert inject.inject_html("<p>fragment").endswith(inject.script_tag())


def test_closing_tag_match_is_case_insensitive():
    assert "</BODY>" in inject.inject_html("<HTML><BODY></BODY></HTML>")


def test_injection_is_idempotent():
    once = inject.inject_html("<body></body>")
    assert inject.inject_html(once) == once
    assert once.count(inject.MARKER) == 1


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("channels.weixin.qq.com", True),
        ("CHANNELS.weixin.qq.com", True),
        ("cdn.channels.weixin.qq.com", True),
        ("channels.weixin.qq.com.evil.example", False),
        ("res.wx.qq.com", False),
    ],
)
def test_page_host_matching(host, expected):
    assert inject.host_matches(host, inject.DEFAULT_PAGE_HOSTS) is expected


def test_should_inject_needs_html_and_a_200():
    assert inject.should_inject(
        host="channels.weixin.qq.com",
        content_type="text/html; charset=utf-8",
        status_code=200,
    )
    assert not inject.should_inject(
        host="channels.weixin.qq.com", content_type="application/json", status_code=200
    )
    assert not inject.should_inject(
        host="channels.weixin.qq.com", content_type="text/html", status_code=302
    )
    assert not inject.should_inject(host="example.com", content_type="text/html", status_code=200)
    assert inject.should_inject(
        host="example.com", content_type="text/html", status_code=200, any_host=True
    )


def test_hook_script_is_fully_templated():
    script = inject.hook_script(version="9.9.9")
    assert "__WX_DLH_" not in script
    assert inject.CAPTURE_PATH in script
    assert "9.9.9" in script
    # the whole point: the key is read out of the raw text, not out of a parsed number
    assert '"decode_key"' in script


def test_asset_source_serves_the_dashboard_from_the_same_place_as_the_hook_script():
    assert inject.asset_source(inject.SCRIPT_NAME) == inject.script_source()
    assert inject.asset_source("dashboard.html").is_file()
    assert inject.asset_source("dashboard.js").is_file()


def test_a_missing_asset_says_where_it_looked():
    with pytest.raises(FileNotFoundError, match="web"):
        inject.asset_source("nope.js")


def test_hook_script_snippet_flag_is_a_boolean_literal():
    assert "INCLUDE_SNIPPET = false" in inject.hook_script(include_snippet=False)
    assert "INCLUDE_SNIPPET = true" in inject.hook_script(include_snippet=True)
