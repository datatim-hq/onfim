"""Release stage 2: first run with init and alerts through a webhook."""
import pathlib
import re

from mem import alerts, cli


def test_init_creates_person_namespace_and_working_agent(app_client):
    """init: organization, person, personal namespace and agent; the agent token writes and finds a record; repeating is safe."""
    tc = app_client
    r = cli.init(tc.svc, "initco", "ann", "Ann Example")
    assert (r["namespace"], r["agent"]) == ("ann.work", "ann-agent")
    h = {"Authorization": f"Bearer {r['token']}"}
    ev = [{"source_ref": "test:init", "source_scope": "dev", "observed_at": "2026-10-09T12:00:00+00:00"}]
    w = tc.post("/v1/ann.work/memories", json={"text": "Деплой только через make release", "kind": "convention",
                                               "evidence": ev}, headers=h)
    assert w.status_code in (200, 201), w.text
    assert tc.post("/v1/ann.work/recall", json={"query": "деплой"}, headers=h).json()["items"]
    assert tc.get("/v1/work/stats", headers=h).status_code == 403          # another namespace
    again = cli.init(tc.svc, "initco", "ann", "Ann Example")
    assert again["namespace"] == "ann.work" and again["token"] != r["token"]
    assert tc.get("/v1/ann.work/stats", headers=h).status_code == 401      # the old token was reissued
    assert tc.get("/v1/ann.work/stats", headers={"Authorization": f"Bearer {again['token']}"}).status_code == 200


def test_alert_goes_to_webhook(monkeypatch):
    """P5: with MEM_ALERT_WEBHOOK_URL an alert goes out as a POST with JSON {"text": ...}; a failed send does not break the call."""
    calls = []
    monkeypatch.setenv("MEM_ALERT_WEBHOOK_URL", "https://hooks.example.com/x")
    monkeypatch.delenv("MEM_TG_BOT_TOKEN", raising=False)
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: calls.append((url, json)))
    alerts.send("сон FAILED")
    assert calls == [("https://hooks.example.com/x", {"text": "сон FAILED"})]

    def boom(*a, **k):
        raise httpx.ConnectError("нет сети")
    monkeypatch.setattr(httpx, "post", boom)
    alerts.send("ещё алерт")                                                # does not raise
    assert alerts.SENT[-1] == "ещё алерт"


def test_migrations_create_no_people_or_namespaces():
    """Stage 2: migrations create no organizations, principals, namespaces or grants; a new install starts with init."""
    sql = "\n".join(p.read_text() for p in sorted(pathlib.Path("migrations").glob("*.sql")))
    seeds = re.findall(r"INSERT\s+INTO\s+(tenants|principals|namespaces|namespace_grants|clients)\b", sql, re.I)
    assert seeds == []


def test_service_admin_is_created_once(app_client):
    """Stage 2: the service principal admin for MEM_ADMIN_TOKEN is in organization system; calling again changes nothing."""
    svc = app_client.svc
    svc.ensure_service_admin()
    svc.ensure_service_admin()
    with svc.pool.connection() as conn:
        rows = conn.execute("SELECT tenant_id, kind FROM principals WHERE id='admin'").fetchall()
    assert [(r["tenant_id"], r["kind"]) for r in rows] == [("system", "service")]
