"""05: alerts to Telegram go through the SOCKS5 proxy when one is set; a malformed setting never sends around it."""
import httpx
import pytest

from mem import alerts


@pytest.fixture
def posts(monkeypatch):
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.append((url, kw)))
    monkeypatch.delenv("MEM_ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("MEM_TG_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("MEM_TG_CHAT_ID", "42")
    return sent


def test_alert_through_proxy(posts, monkeypatch):
    """05: MEM_TG_PROXY=socks5://tgproxy:1080 — the Bot API request goes through that SOCKS5 proxy."""
    monkeypatch.setenv("MEM_TG_PROXY", "socks5://tgproxy:1080")
    alerts.send("sleep failed")
    (url, kw), = posts
    assert url == "https://api.telegram.org/bot123:abc/sendMessage" and kw["proxy"] == "socks5://tgproxy:1080"
    assert kw["json"] == {"chat_id": "42", "text": "sleep failed"}


def test_no_proxy_direct(posts, monkeypatch):
    """05: without MEM_TG_PROXY the alert goes to Telegram directly."""
    monkeypatch.delenv("MEM_TG_PROXY", raising=False)
    alerts.send("x")
    assert "proxy" not in posts[0][1]


@pytest.mark.parametrize("bad", ["http://tgproxy:1080", "socks5://user:pw@tgproxy:1080", "socks5://tgproxy",
                                 "tgproxy:1080", "socks5://tgproxy:99999"])
def test_bad_proxy_never_goes_direct(posts, monkeypatch, bad):
    """05: a typo in MEM_TG_PROXY — the Telegram alert is not sent at all (only logged), never around the proxy."""
    monkeypatch.setenv("MEM_TG_PROXY", bad)
    alerts.send("x")
    assert posts == [] and alerts.SENT[-1] == "x"


def test_webhook_does_not_use_telegram_proxy(posts, monkeypatch):
    """05: the webhook alert goes directly; the Telegram proxy applies only to Telegram."""
    monkeypatch.setenv("MEM_ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    monkeypatch.setenv("MEM_TG_PROXY", "socks5://tgproxy:1080")
    alerts.send("x")
    (hook_url, hook_kw), (tg_url, tg_kw) = posts
    assert hook_url == "https://hooks.example.com/x" and "proxy" not in hook_kw and tg_kw["proxy"]


def test_send_failure_does_not_raise(monkeypatch, caplog):
    """05: proxy unreachable — the alert does not break the sleep; the log has the error type, not the URL with the
    token."""
    def down(url, **kw):
        raise httpx.ConnectError("proxy down")
    monkeypatch.setattr(httpx, "post", down)
    monkeypatch.delenv("MEM_ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("MEM_TG_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("MEM_TG_CHAT_ID", "42")
    monkeypatch.setenv("MEM_TG_PROXY", "socks5://tgproxy:1080")
    alerts.send("x")
    assert "ConnectError" in caplog.text and "123:abc" not in caplog.text


def test_socks_support_installed():
    """05: httpx has SOCKS support (socksio) installed, otherwise an alert through the proxy would fail on send."""
    with httpx.Client(proxy="socks5://127.0.0.1:1") as c:
        assert c is not None
