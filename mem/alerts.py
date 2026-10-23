"""Alerts (P5): webhook MEM_ALERT_WEBHOOK_URL (POST JSON {"text": ...}; works with Slack, Mattermost or your own
receiver) and/or Telegram when MEM_TG_BOT_TOKEN and MEM_TG_CHAT_ID are set; without them, only the log.
Tests replace send and check that the alert was raised.

Telegram can go through a SOCKS5 proxy: MEM_TG_PROXY=socks5://host:port (for hosts where Telegram is reachable only
through a tunnel). A malformed value means the Telegram alert is not sent at all (and this is logged), never sent
directly around the proxy."""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("mem.alerts")
SENT: list[str] = []   # latest alerts of this process (for tests and /status)
TG_API = "https://api.telegram.org"


class ProxyError(ValueError):
    pass


def proxy() -> str | None:
    """MEM_TG_PROXY -> SOCKS5 address, or None when unset. Only socks5://host:port, without user and password."""
    raw = (os.environ.get("MEM_TG_PROXY") or "").strip()
    if not raw:
        return None
    m = re.fullmatch(r"socks5://([A-Za-z0-9.-]+):(\d{1,5})", raw)
    if not m or not 0 < int(m.group(2)) < 65536:
        raise ProxyError("MEM_TG_PROXY: expected socks5://host:port")
    return raw


def send(text: str) -> None:
    SENT.append(text)
    del SENT[:-50]
    log.warning("ALERT: %s", text)
    hook = os.environ.get("MEM_ALERT_WEBHOOK_URL")
    token, chat = os.environ.get("MEM_TG_BOT_TOKEN"), os.environ.get("MEM_TG_CHAT_ID")
    if hook:
        _post(hook, {"text": text[:3500]}, "webhook")
    if token and chat:
        try:
            via = proxy()
        except ProxyError as e:
            log.error("telegram alert not sent: %s", e)
            return
        _post(f"{TG_API}/bot{token}/sendMessage", {"chat_id": chat, "text": text[:3500]}, "telegram", via)


def _post(url: str, payload: dict, kind: str, via: str | None = None) -> None:
    try:
        import httpx
        httpx.post(url, json=payload, timeout=10, **({"proxy": via} if via else {}))
    except Exception as e:  # noqa: BLE001 — an alert must not break the sleep; the URL may hold a token, so only the type is logged
        log.error("%s alert failed: %s", kind, type(e).__name__)
