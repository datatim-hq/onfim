"""08: organizations, groups, agent delegation, record classification."""
from datetime import datetime, timedelta, timezone

import pytest

NOW = "2026-09-24T10:00:00+00:00"


def ev(ref="dev:1", scope="dev", speaker="agent"):
    return {"source_ref": ref, "source_scope": scope, "speaker": speaker, "observed_at": NOW}


@pytest.fixture(scope="module")
def acme(app_client):
    svc = app_client.svc
    svc.create_tenant("acme", "ACME")
    svc.create_group("acme", "acme", "ACME", kind="org_unit")
    svc.create_group("acme/pmo", "acme", "PMO", parent="acme", kind="org_unit")
    svc.create_group("acme/pmo/shop", "acme", "Команда Shop", parent="acme/pmo")
    for pid, clr in (("ivanov", 1), ("petrova", 2), ("sidorov", 3)):
        svc.create_principal(pid, "acme", "user", clearance=clr)
    svc.create_principal("ivanov-cc", "acme", "agent", on_behalf_of="ivanov", clearance=3)
    svc.add_member("acme/pmo/shop", "ivanov")
    svc.add_member("acme/pmo", "petrova", role="curator")
    svc.create_namespace("acme.pmo", "acme", "group", owner_group="acme/pmo", default_classification=1)
    svc.create_namespace("acme.finance", "acme", "group", default_classification=2)
    svc.grant("acme.pmo", "group", "acme/pmo", "contributor", 1)
    svc.grant("acme.pmo", "principal", "petrova", "curator", 2)
    svc.grant("acme.finance", "principal", "sidorov", "owner", 3)
    svc.grant("acme.pmo", "principal", "sidorov", "reader", 3,
              expires_at=datetime.now(timezone.utc) - timedelta(days=1))   # expired grant
    t = {}
    for pid in ("ivanov", "petrova", "sidorov"):
        t[pid] = svc.create_client(f"tok-{pid}", ["*"], ["read", "write", "review", "forget", "manage"], principal_id=pid)
    t["ivanov-cc"] = svc.create_client("tok-ivanov-cc", ["acme.pmo", "acme.finance"], ["read", "write"],
                                       principal_id="ivanov-cc")
    return t


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def put(tc, tok, ns, text, cls=None, expect=201):
    body = {"text": text, "kind": "decision", "evidence": [ev()]}
    if cls is not None:
        body["classification"] = cls
    r = tc.post(f"/v1/{ns}/memories", json=body, headers=H(tok))
    assert r.status_code == expect, r.text
    return r.json()


def test_group_inheritance_and_no_grant(app_client, acme):
    """A member of a child group reads the parent group's namespace, and a person with an expired grant gets 403."""
    tc = app_client
    assert tc.get("/v1/acme.pmo/memories", headers=H(acme["ivanov"])).status_code == 200   # member of a child group
    assert tc.get("/v1/acme.pmo/memories", headers=H(acme["sidorov"])).status_code == 403  # grant expired


def test_agent_never_exceeds_its_human(app_client, acme):
    """An agent never gets more rights than its person and the token ceiling: finance is closed and it cannot confirm.
    The audit log records both the agent and the person it acts for."""
    tc = app_client
    assert tc.get("/v1/acme.pmo/memories", headers=H(acme["ivanov-cc"])).status_code == 200
    assert tc.get("/v1/acme.finance/memories", headers=H(acme["ivanov-cc"])).status_code == 403
    m = put(tc, acme["ivanov-cc"], "acme.pmo", "Решение команды: релиз по четвергам")
    assert m["source"] == "tok-ivanov-cc" and m["classification"] == 1
    with tc.svc.pool.connection() as conn:
        a = conn.execute("SELECT principal_id, on_behalf_of FROM audit_log WHERE memory_id=%s AND action='remember'",
                         (m["id"],)).fetchone()
        conn.rollback()
    assert (a["principal_id"], a["on_behalf_of"]) == ("ivanov-cc", "ivanov")
    # token ceiling: the agent has no review
    assert tc.post(f"/v1/acme.pmo/memories/{m['id']}/confirm", headers=H(acme["ivanov-cc"])).status_code == 403


def test_classification_filter(app_client, acme):
    """A level 2 record is invisible to a person with clearance 1 in search, list and stats.
    A curator with clearance 2 reads it, and that read goes to access_log."""
    tc = app_client
    pub = put(tc, acme["petrova"], "acme.pmo", "Бюджет портфеля обсуждается на комитете", cls=1)
    sec = put(tc, acme["petrova"], "acme.pmo", "Бюджет портфеля PMO — 12 млн, резерв 2 млн", cls=2)
    iv = H(acme["ivanov"])
    ids = [i["id"] for i in tc.post("/v1/acme.pmo/recall", json={"query": "бюджет портфеля"}, headers=iv).json()["items"]]
    assert pub["id"] in ids and sec["id"] not in ids
    assert sec["id"] not in [i["id"] for i in tc.get("/v1/acme.pmo/memories", headers=iv).json()["items"]]
    assert tc.get(f"/v1/acme.pmo/memories/{sec['id']}", headers=iv).status_code == 404
    st = tc.get("/v1/acme.pmo/stats", headers=iv).json()
    assert st["counts"]["decision"]["current"] == 1
    # the curator sees it, and the level 2 read goes to access_log
    assert tc.get(f"/v1/acme.pmo/memories/{sec['id']}", headers=H(acme["petrova"])).status_code == 200
    with tc.svc.pool.connection() as conn:
        n = conn.execute("SELECT count(*) n FROM access_log WHERE principal_id='petrova' AND %s = ANY(memory_ids)",
                         (sec["id"],)).fetchone()["n"]
        conn.rollback()
    assert n >= 1


def test_cannot_write_above_clearance(app_client, acme):
    """A record with classification above the author's clearance is refused with 400."""
    put(app_client, acme["ivanov"], "acme.pmo", "секрет", cls=2, expect=400)


def test_tenant_isolation(app_client, acme):
    """Another organization is closed both ways, and a missing namespace answers with the same 403 as another one's."""
    tc = app_client
    assert tc.get("/v1/work/memories", headers=H(acme["sidorov"])).status_code == 403
    assert tc.get("/v1/acme.pmo/memories", headers=H(tc.tokens["cowork"])).status_code == 403
    # a missing namespace gives the same answer as another one's
    r1 = tc.get("/v1/acme.nope/memories", headers=H(acme["sidorov"]))
    r2 = tc.get("/v1/work/memories", headers=H(acme["sidorov"]))
    assert r1.status_code == r2.status_code == 403


def test_export_owner_only(app_client, acme):
    """Export from an outside address is available to the namespace owner; the curator gets 403."""
    tc = app_client
    ext = {"X-Real-IP": "1.2.3.4"}
    assert tc.get("/v1/acme.finance/export", headers={**H(acme["sidorov"]), **ext}).status_code == 200
    assert tc.get("/v1/acme.pmo/export", headers={**H(acme["petrova"]), **ext}).status_code == 403


def test_recall_layers(app_client, acme):
    """Layered search covers only namespaces where the person has a valid grant.
    Asking for another layer explicitly gives 403, and the cowork token sees only work."""
    tc = app_client
    put(tc, acme["petrova"], "acme.pmo", "Решение PMO: статус-отчёт по пятницам", cls=1)
    put(tc, acme["sidorov"], "acme.finance", "Решение финансов: статус-отчёт по бюджету ежемесячно", cls=2)
    r = tc.post("/v1/recall", json={"query": "статус-отчёт"}, headers=H(acme["ivanov"])).json()
    assert r["namespaces"] == ["acme.pmo"] and all(i["layer"]["namespace"] == "acme.pmo" for i in r["items"])
    r2 = tc.post("/v1/recall", json={"query": "статус-отчёт"}, headers=H(acme["sidorov"])).json()
    assert "acme.finance" in r2["namespaces"] and "acme.pmo" not in r2["namespaces"]   # the grant in pmo expired
    assert tc.post("/v1/recall", json={"query": "x", "namespaces": ["acme.finance"]},
                   headers=H(acme["ivanov"])).status_code == 403
    r3 = tc.post("/v1/recall", json={"query": "статус-отчёт"}, headers=H(tc.tokens["cowork"])).json()
    assert set(r3["namespaces"]) <= {"work"}
