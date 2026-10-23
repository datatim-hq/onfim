"""Stage 1 integration tests: Postgres + fake embedder, REST and MCP through TestClient."""
import json

import pytest

NOW = "2026-09-24T10:00:00+00:00"


def H(tc, who="cowork", **extra):
    return {"Authorization": f"Bearer {tc.tokens[who]}", **extra}


def ev(ref="claude-code:mem", scope="dev", speaker="agent", quote=None, at=NOW, **kw):
    d = {"source_ref": ref, "source_scope": scope, "speaker": speaker, "observed_at": at}
    if quote:
        d["quote"] = quote
    d.update(kw)
    return d


def remember(tc, ns="work", who="cowork", expect=201, **body):
    body.setdefault("kind", "gotcha")
    body.setdefault("evidence", [ev()])
    r = tc.post(f"/v1/{ns}/memories", json=body, headers=H(tc, who))
    assert r.status_code == expect, r.text
    return r.json()


# ------------------------------------------------------------------ validation and P1

def test_no_evidence_400(app_client):
    """A record without evidence is refused with bad_request and is not stored."""
    tc = app_client
    r = tc.post("/v1/work/memories", json={"text": "x", "kind": "note"}, headers=H(tc))
    assert r.status_code == 400 and r.json()["error"] == "bad_request"
    assert tc.get("/v1/work/memories", headers=H(tc)).json()["items"] == []


def test_no_kind_and_bad_kind_400(app_client):  # M-L2
    """M-L2: a record without a kind, with an unknown kind or with a kind of another namespace is refused with 400."""
    tc = app_client
    assert tc.post("/v1/work/memories", json={"text": "x", "evidence": [ev()]}, headers=H(tc)).status_code == 400
    assert tc.post("/v1/work/memories", json={"text": "x", "kind": "fact", "evidence": [ev()]}, headers=H(tc)).status_code == 400
    # a kind from another namespace
    assert tc.post("/v1/work/memories", json={"text": "x", "kind": "episode", "evidence": [ev()]}, headers=H(tc)).status_code == 400


def test_fields_stored_as_sent(app_client):  # F-1, P1
    """F-1, P1: kind, subject, tags, importance and text are stored exactly as the client sent them."""
    tc = app_client
    m = remember(tc, text="Shop: Next.js redirect через request.url уводит на localhost:3002",
                 kind="gotcha", tags=["project:shop", "nextjs"], subject="project:shop", importance=3)
    g = tc.get(f"/v1/work/memories/{m['id']}", headers=H(tc)).json()
    assert (g["kind"], g["subject"], g["tags"], g["importance"]) == ("gotcha", "project:shop", ["project:shop", "nextjs"], 3)
    assert g["text"].startswith("Shop: Next.js") and g["source"] == "cowork" and len(g["evidence"]) == 1


def test_quote_limit(app_client):
    """A quote longer than 200 characters is refused with 400."""
    tc = app_client
    r = tc.post("/v1/work/memories", json={"text": "x", "kind": "note", "evidence": [ev(quote="я" * 201)]}, headers=H(tc))
    assert r.status_code == 400


# ------------------------------------------------------------------ access (M-A1, M-A2)

def test_auth(app_client):
    """M-A1, M-A2: no token or a wrong token gives 401, another namespace 403.
    Confirming without the review right and deleting without the forget right also give 403; with review, confirming works."""
    tc = app_client
    assert tc.get("/v1/work/memories").status_code == 401
    assert tc.get("/v1/work/memories", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert tc.get("/v1/assistant/memories", headers=H(tc, "cowork")).status_code == 403
    m = remember(tc, text="решение", kind="decision")
    assert tc.post(f"/v1/work/memories/{m['id']}/confirm", headers=H(tc, "claude-code")).status_code == 403
    assert tc.post(f"/v1/work/memories/{m['id']}/confirm", headers=H(tc, "cowork")).status_code == 200
    # purge only with the forget right
    assert tc.delete(f"/v1/work/memories/{m['id']}", headers=H(tc, "cowork")).status_code == 403


def test_admin_only_local(app_client):
    """The admin token works only from localhost: from an outside address, export gives 403."""
    tc = app_client
    assert tc.get("/v1/work/export", headers=H(tc, "admin")).status_code == 200
    assert tc.get("/v1/work/export", headers=H(tc, "admin", **{"X-Real-IP": "1.2.3.4"})).status_code == 403


# ------------------------------------------------------------------ strength (M-S2, M-S3)

def test_initial_strength_owner_bonus(app_client):
    """A record of importance 2 starts at strength 0.55, and at 0.65 from the owner's words."""
    tc = app_client
    a = remember(tc, text="важно", kind="note", importance=2)
    b = remember(tc, text="сказал владелец", kind="note", importance=2, evidence=[ev(speaker="owner", scope="owner")])
    assert a["strength"] == pytest.approx(0.55) and b["strength"] == pytest.approx(0.65)


def test_independent_vs_repeat(app_client):
    """M-S2, M-S3: three independent pieces of evidence give more strength than one, five repeats less than one independent.
    Repeating the same source_ref changes nothing."""
    tc = app_client
    one = remember(tc, ns="assistant", who="assistant", kind="person_fact", subject="person:ivanova",
                   text="Иванова отвечает только письменно", evidence=[ev("tg:1/1", "group:a", "person:x")])
    three = remember(tc, ns="assistant", who="assistant", kind="person_fact", subject="person:ivanova2",
                     text="Иванова-2 отвечает только письменно", evidence=[ev("tg:1/2", "group:a", "person:x")])
    for i, (scope, sp) in enumerate([("group:b", "person:y"), ("group:c", "person:z")]):
        r = tc.post(f"/v1/assistant/memories/{three['id']}/evidence", json=ev(f"tg:9/{i}", scope, sp), headers=H(tc, "assistant"))
        assert r.json()["independent"] is True
    rep = remember(tc, ns="assistant", who="assistant", kind="person_fact", subject="person:ivanova3",
                   text="Иванова-3 отвечает только письменно", evidence=[ev("tg:1/3", "group:a", "person:x")])
    for i in range(5):
        r = tc.post(f"/v1/assistant/memories/{rep['id']}/evidence", json=ev(f"tg:1/3{i}", "group:a", "person:x"), headers=H(tc, "assistant"))
        assert r.json()["independent"] is False
    s = {m: tc.get(f"/v1/assistant/memories/{x['id']}", headers=H(tc, "assistant")).json()["strength"]
         for m, x in (("one", one), ("three", three), ("rep", rep))}
    assert s["three"] > s["one"]
    assert s["rep"] - s["one"] < 0.30 * (1 - s["one"])      # 5 repeats < one independent
    # repeating the same source_ref is ignored
    r = tc.post(f"/v1/assistant/memories/{one['id']}/evidence", json=ev("tg:1/1", "group:a", "person:x"), headers=H(tc, "assistant"))
    assert r.json()["changed"] is False


def test_decay_with_clock(app_client):
    """In 30 days an episode loses half its strength, while a decision, a pinned record and an open commitment do not decay.
    A closed commitment starts to decay (H=14); the min_strength filter uses current strength."""
    tc = app_client
    ep = remember(tc, ns="assistant", who="assistant", kind="episode", subject="chat:dacha", text="24.09: обсудили смету")
    dec = remember(tc, kind="decision", text="mem на Postgres")
    pinned = remember(tc, ns="assistant", who="assistant", kind="episode", subject="chat:x", text="закреплённый эпизод")
    tc.post(f"/v1/assistant/memories/{pinned['id']}/pin", headers=H(tc, "assistant"))
    com = remember(tc, ns="assistant", who="assistant", kind="commitment_mirror", text="прислать смету Чижову до 30.09")
    r = tc.post("/v1/_stand/clock", json={"advance_days": 30}, headers=H(tc, "admin"))
    assert r.status_code == 200
    g = lambda ns, x, who: tc.get(f"/v1/{ns}/memories/{x['id']}", headers=H(tc, who)).json()["strength"]
    assert g("assistant", ep, "assistant") == pytest.approx(0.55 / 2, abs=1e-3)
    assert g("work", dec, "cowork") == pytest.approx(0.55)
    assert g("assistant", pinned, "assistant") == pytest.approx(0.55)
    assert g("assistant", com, "assistant") == pytest.approx(0.55)
    # after closing, the commitment starts to decay (H=14)
    tc.post(f"/v1/assistant/memories/{com['id']}/close", headers=H(tc, "assistant"))
    tc.post("/v1/_stand/clock", json={"advance_days": 14}, headers=H(tc, "admin"))
    assert g("assistant", com, "assistant") == pytest.approx(0.275, abs=1e-3)
    # min_strength filters by current strength
    items = tc.get("/v1/assistant/memories?min_strength=0.5", headers=H(tc, "assistant")).json()["items"]
    assert {i["id"] for i in items} == {pinned["id"]}


def test_used_helpful(app_client):
    """A used mark reinforces strength by 0.15 of the remainder to 1 and is written as an event in the record history."""
    tc = app_client
    m = remember(tc, kind="note", text="заметка")
    r = tc.post(f"/v1/work/memories/{m['id']}/used", headers=H(tc)).json()
    assert r["strength"] == pytest.approx(0.55 + 0.15 * 0.45)
    events = tc.get(f"/v1/work/memories/{m['id']}", headers=H(tc)).json()["events"]
    assert events[0]["type"] == "used"


# ------------------------------------------------------------------ slots and supersession

def chizhov(tc, text, speaker, ref, occurred=None, **kw):
    body = dict(kind="topic_fact", subject="meeting:chizhov", slot="meeting:2026-09-25-chizhov#time", text=text,
                evidence=[ev(ref, "group:gnezdo", speaker, quote=text)])
    if occurred:
        body["occurred_at"] = occurred
    body.update(kw)
    return remember(tc, ns="assistant", who="assistant", **body)


def test_slot_candidate_and_contested(app_client):
    """A new slot value from a third party's words becomes a candidate, not a replacement.
    Search returns the current record marked contested with the candidate id; the candidate itself is not returned."""
    tc = app_client
    c401 = chizhov(tc, "Созвон с Чижовым в чт 15:00", "person:chizhov", "tg:5/1", "2026-09-20T10:00:00+00:00")
    assert c401["state"] == "current"
    c402 = chizhov(tc, "Созвон с Чижовым в пт 16:00", "person:sinitsyn", "tg:5/2", "2026-09-21T10:00:00+00:00")
    assert c402["state"] == "candidate" and c402["slot_current_id"] == c401["id"]
    r = tc.post("/v1/assistant/recall", json={"query": "когда созвон с Чижовым"}, headers=H(tc, "assistant")).json()
    top = r["items"][0]
    assert top["id"] == c401["id"] and top["contested"] is True and top["candidate_id"] == c402["id"]
    assert all(i["id"] != c402["id"] for i in r["items"])        # the candidate is not returned by default


def test_same_claim_in_slot_reinforces(app_client):
    """C-05 of the assistant spec 12: the same person repeated the same claim (the same text in the slot), a confirmation:
    the evidence is added to the current record, strength grows, no candidate; the same message again changes nothing.
    The usual way (candidate or history) is taken by: another text, a repeat from another chat for same_chat, another
    classification or visibility, a late older claim, a repeat while a candidate is open, and rules."""
    tc = app_client

    def fact(slot, text, ref, scope="chat:501", **kw):
        body = dict(kind="person_fact", subject="person:tg501", slot=f"person:tg501:{slot}", visibility="same_chat",
                    text=text, evidence=[ev(ref, scope, "[Персона-1]", quote=text[:40])])
        body.update(kw)
        return remember(tc, ns="assistant", who="assistant", **body)

    a = fact("voice", "[Персона-1] не любит голосовые.", "tg:501:1")
    b = fact("voice", "[персона-1]  не любит  голосовые", "tg:501:2")
    assert b["id"] == a["id"] and b["reinforced"] is True and b["state"] == "current"
    assert b["evidence_count"] == 2 and b["strength"] > a["strength"]
    again = fact("voice", "[Персона-1] не любит голосовые.", "tg:501:2")            # the same message again
    assert again["id"] == a["id"] and again["reinforced"] is False and again["evidence_count"] == 2
    other_chat = fact("voice", "[Персона-1] не любит голосовые.", "tg:777:1", scope="chat:777")
    assert other_chat["id"] != a["id"] and other_chat["state"] == "candidate"        # the audience does not widen
    after_cand = fact("voice", "[Персона-1] не любит голосовые.", "tg:501:3")
    assert after_cand["id"] != a["id"]                                              # a candidate in the slot: the usual way

    w = fact("work", "[Персона-1] работает в Сбере.", "tg:501:10", occurred_at="2026-09-20T10:00:00+00:00")
    assert fact("work", "[Персона-1] просит звонить.", "tg:501:11")["state"] == "candidate"   # another text
    w2 = fact("hobby", "[Персона-1] играет в теннис.", "tg:501:20")
    assert fact("hobby", "[Персона-1] играет в теннис.", "tg:501:21", classification=0)["id"] != w2["id"]
    assert fact("hobby", "[Персона-1] играет в теннис.", "tg:-9:1", scope="chat:-9", visibility="shareable")["id"] != w2["id"]
    w3 = fact("city", "[Персона-1] живёт в Казани.", "tg:501:30", occurred_at="2026-09-20T10:00:00+00:00")
    late = fact("city", "[Персона-1] живёт в Казани.", "tg:501:31", occurred_at="2026-09-10T10:00:00+00:00")
    assert late["id"] != w3["id"] and late["state"] == "superseded"                 # a late older claim goes to history
    assert w["state"] == "current"
    r1 = remember(tc, ns="work", text="Деплой только через make release", kind="convention", slot="project:x#deploy",
                  evidence=[ev("claude-code:x")])
    r2 = remember(tc, ns="work", text="Деплой только через make release", kind="convention", slot="project:x#deploy",
                  evidence=[ev("claude-code:x2")])
    assert r2["id"] != r1["id"] and "reinforced" not in r2                          # rules take their own way


def test_late_old_goes_to_history(app_client):  # M-C2e
    """M-C2e: a late record into a slot with an earlier occurred_at goes straight to history; the current one stays."""
    tc = app_client
    cur = chizhov(tc, "Созвон в пт 16:00", "person:chizhov", "tg:5/3", "2026-09-21T10:00:00+00:00")
    old = chizhov(tc, "Созвон в чт 15:00", "person:chizhov", "tg:5/4", "2026-09-20T10:00:00+00:00")
    assert old["state"] == "superseded" and old["valid_to"] is not None
    assert tc.get(f"/v1/assistant/memories/{cur['id']}", headers=H(tc, "assistant")).json()["state"] == "current"


def test_explicit_supersede_history_revert(app_client):  # M-C2d
    """M-C2d: an explicit supersedes replaces the record, history shows both, revert brings the old one back to current.
    supersedes pointing to a non-current record gives 400."""
    tc = app_client
    a = chizhov(tc, "Созвон в чт 15:00", "person:chizhov", "tg:6/1")
    b = chizhov(tc, "Созвон перенесён на пт 16:00", "person:chizhov", "tg:6/2", supersedes=a["id"])
    assert b["state"] == "current"
    ga = tc.get(f"/v1/assistant/memories/{a['id']}", headers=H(tc, "assistant")).json()
    assert ga["state"] == "superseded" and ga["valid_to"]
    h = tc.get(f"/v1/assistant/memories/{b['id']}/history", headers=H(tc, "assistant")).json()
    assert [x["id"] for x in h["records"]] == [a["id"], b["id"]]
    assert any(t["action"] == "supersede" for t in h["transitions"])
    r = tc.post(f"/v1/assistant/memories/{b['id']}/revert-supersede", headers=H(tc, "assistant"))
    assert r.status_code == 200, r.text
    assert r.json()["current"]["id"] == a["id"] and r.json()["candidate"]["state"] == "candidate"
    # supersedes pointing to a non-current record -> 400
    assert tc.post("/v1/assistant/memories", headers=H(tc, "assistant"), json=dict(
        kind="topic_fact", text="x", supersedes=b["id"], evidence=[ev()])).status_code == 400


def test_slot_unique_constraint(app_client):  # D-2
    """D-2: the database does not allow a second current record in a slot: the unique index fires."""
    tc = app_client
    chizhov(tc, "Созвон в чт", "person:chizhov", "tg:7/1")
    b = chizhov(tc, "Созвон в пт", "person:sinitsyn", "tg:7/2")
    assert b["state"] == "candidate"
    import psycopg
    with tc.svc.pool.connection() as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("UPDATE memories SET state='current' WHERE id=%s", (b["id"],))
        conn.rollback()


def test_external_ref_idempotent(app_client):  # A-3
    """A-3: writing again with the same external_ref does not create a record, it adds evidence to the existing one."""
    tc = app_client
    a = remember(tc, ns="assistant", who="assistant", kind="commitment_mirror", text="смета Чижову", external_ref="cmt:17",
                 evidence=[ev("tg:1/1", "group:a", "owner")])
    b = remember(tc, ns="assistant", who="assistant", kind="commitment_mirror", text="смета Чижову", external_ref="cmt:17",
                 evidence=[ev("tg:1/2", "group:b", "person:chizhov")])
    assert b["id"] == a["id"] and b["idempotent"] is True and b["evidence_count"] == 2


# ------------------------------------------------------------------ archive and deletion

def test_archive_unarchive(app_client):  # M-F3
    """M-F3: an archived record disappears from the list, and after unarchiving is current again with strength 0.30."""
    tc = app_client
    m = remember(tc, kind="note", text="архивная")
    assert tc.post(f"/v1/work/memories/{m['id']}/archive", headers=H(tc)).json()["state"] == "archived"
    assert tc.get("/v1/work/memories", headers=H(tc)).json()["items"] == []
    u = tc.post(f"/v1/work/memories/{m['id']}/unarchive", headers=H(tc)).json()
    assert u["state"] == "current" and u["strength"] == pytest.approx(0.30)


def test_purge_no_traces(app_client):  # M-F1, D-7, D-8
    """M-F1, D-7, D-8: purge deletes the record with its evidence and events.
    The audit log keeps the fact of deletion but not the quote text."""
    tc = app_client
    secret = "Секретная цитата про Сидорова"
    m = remember(tc, who="forgetter", kind="note", text=secret, evidence=[ev(quote=secret)])
    tc.post(f"/v1/work/memories/{m['id']}/used", headers=H(tc, "forgetter"))
    r = tc.delete(f"/v1/work/memories/{m['id']}?mode=purge", headers=H(tc, "forgetter"))
    assert r.status_code == 200
    with tc.svc.pool.connection() as conn:
        for t in ("memories WHERE id", "evidence WHERE memory_id", "memory_events WHERE memory_id"):
            assert conn.execute(f"SELECT count(*) n FROM {t} = %s", (m["id"],)).fetchone()["n"] == 0
        dump = json.dumps([dict(r) for r in conn.execute("SELECT action, detail FROM audit_log").fetchall()], ensure_ascii=False, default=str)
        conn.rollback()
    assert "Сидорова" not in dump and "purge" in dump


def test_bulk_forget_requires_confirm(app_client):  # M-F4, S-3
    """M-F4, S-3: bulk deletion requires a filter and by default only counts the records.
    It deletes only with dry_run=false and confirm equal to the number of matching records."""
    tc = app_client
    for i in range(3):
        remember(tc, ns="assistant", who="assistant", kind="person_fact", subject="person:sidorov", text=f"Сидоров факт {i}",
                 evidence=[ev(f"tg:2/{i}", "group:a", "person:x")])
    url, hh = "/v1/assistant/forget", H(tc, "assistant")
    assert tc.post(url, json={"mode": "purge"}, headers=hh).status_code == 400            # no filter
    d = tc.post(url, json={"subject": "person:sidorov", "mode": "purge"}, headers=hh).json()
    assert d["dry_run"] and d["matched"] == 3
    assert tc.post(url, json={"subject": "person:sidorov", "mode": "purge", "dry_run": False}, headers=hh).status_code == 400
    assert tc.post(url, json={"subject": "person:sidorov", "mode": "purge", "dry_run": False, "confirm": 2}, headers=hh).status_code == 400
    assert len(tc.get("/v1/assistant/memories", headers=hh).json()["items"]) == 3
    r = tc.post(url, json={"subject": "person:sidorov", "mode": "purge", "dry_run": False, "confirm": 3}, headers=hh).json()
    assert r["applied"] == 3 and tc.get("/v1/assistant/memories", headers=hh).json()["items"] == []


# ------------------------------------------------------------------ search

def test_recall_tags_rare_token_explain_deterministic(app_client):
    """R-4: search ranks the record with a rare token first, returns the score breakdown and repeats the order.
    The tag filter works; an empty query returns a list by strength."""
    tc = app_client
    remember(tc, kind="gotcha", text="EMBEDDING_MODEL задаётся переменной окружения", tags=["project:mem"])
    remember(tc, kind="gotcha", text="Модель эмбеддингов выбирается бенчмарком", tags=["project:mem"])
    remember(tc, kind="gotcha", text="Редирект Next.js за nginx уводит на localhost", tags=["project:shop"])
    body = {"query": "EMBEDDING_MODEL", "explain": True}
    r1 = tc.post("/v1/work/recall", json=body, headers=H(tc)).json()["items"]
    assert "EMBEDDING_MODEL" in r1[0]["text"]
    assert set(r1[0]["explain"]) == {"sem", "lex", "strength", "prior", "score"}
    r2 = tc.post("/v1/work/recall", json=body, headers=H(tc)).json()["items"]
    assert [x["id"] for x in r1] == [x["id"] for x in r2]
    rt = tc.post("/v1/work/recall", json={"query": "редирект", "tags": ["project:mem"]}, headers=H(tc)).json()["items"]
    assert rt and all("project:mem" in x["tags"] for x in rt)
    # R-4: an empty query returns a list by strength
    assert len(tc.post("/v1/work/recall", json={"query": ""}, headers=H(tc)).json()["items"]) == 3


def test_recall_evidence_and_audience(app_client):  # M-E0, M-A3
    """M-E0, M-A3: search returns the record with its quote and speaker.
    A visibility=same_chat fact from "Gnezdo" is not visible with audience=chat:dacha for any query."""
    tc = app_client
    remember(tc, ns="assistant", who="assistant", kind="person_fact", subject="person:chizhov", visibility="same_chat",
             text="Чижову удобно созваниваться по утрам",
             evidence=[ev("tg:7/1", "group:gnezdo", "person:chizhov", quote="мне удобнее утром", at="2026-09-22T09:00:00+00:00")])
    hh = H(tc, "assistant")
    r = tc.post("/v1/assistant/recall", json={"query": "когда удобно Чижову"}, headers=hh).json()["items"]
    assert r and r[0]["evidence"][0]["quote"] == "мне удобнее утром" and r[0]["evidence"][0]["speaker"] == "person:chizhov"
    for q in ("Чижов", "утро", "созвон", "когда удобно Чижову"):
        assert tc.post("/v1/assistant/recall", json={"query": q, "audience": "chat:dacha"}, headers=hh).json()["items"] == []
    assert tc.post("/v1/assistant/recall", json={"query": "Чижов", "audience": "chat:gnezdo"}, headers=hh).json()["items"]


def test_recalled_top5_once_per_day(app_client):
    """The recalled_top5 event is written at most once a day, even if the record was returned three times."""
    tc = app_client
    m = remember(tc, kind="note", text="уникальная заметка про кролика")
    for _ in range(3):
        tc.post("/v1/work/recall", json={"query": "кролик"}, headers=H(tc))
    ev_ = [e for e in tc.get(f"/v1/work/memories/{m['id']}", headers=H(tc)).json()["events"] if e["type"] == "recalled_top5"]
    assert len(ev_) == 1


# ------------------------------------------------------------------ service routes

def test_status_and_stats(app_client):
    """Status with a wrong key gives 404; with the right key, database state, embedding dimension and counters.
    Namespace stats count records by kind."""
    tc = app_client
    remember(tc, kind="note", text="для статистики")
    assert tc.get("/status/wrong").status_code == 404
    s = tc.get("/status/test-status").json()
    assert s["dbOk"] is True and s["embedDim"] == 64 and s["memories"]["work"]["current"] == 1
    st = tc.get("/v1/work/stats", headers=H(tc)).json()
    assert st["counts"]["note"]["current"] == 1


def test_schema_check_rejects_wrong_dim():  # D-4
    """D-4: the service refuses to start if the embedding dimension in the database does not match the model."""
    import os
    from mem import db
    with pytest.raises(SystemExit):
        db.check_schema(os.environ["MEM_DATABASE_URL"], 999, "fake")


# ------------------------------------------------------------------ MCP (P7, M-T1)

def mcp_call(tc, ns, tool, args, who="cowork"):
    r = tc.post(f"/mcp/{ns}/", headers={**H(tc, who), "Accept": "application/json, text/event-stream",
                                        "Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    if "structuredContent" not in res and not res.get("isError"):
        res["structuredContent"] = json.loads(res["content"][0]["text"])
    return res


def test_mcp_auth(app_client):
    """MCP without a token gives 401, another namespace 403; with its own token the full set of tools is available."""
    tc = app_client
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert tc.post("/mcp/work/", json=body, headers=hdr).status_code == 401
    assert tc.post("/mcp/assistant/", json=body, headers={**hdr, **H(tc, "cowork")}).status_code == 403
    r = tc.post("/mcp/work/", json=body, headers={**hdr, **H(tc, "cowork")})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"remember", "recall", "get", "list", "history", "add_evidence", "used", "forget", "stats"} <= names


def test_mcp_parity_and_tags(app_client):  # A-1, M-T1
    """P7, A-1, M-T1: search with tags through MCP and through REST gives the same results.
    A service error reaches MCP as isError, and no record is created."""
    tc = app_client
    res = mcp_call(tc, "work", "remember", {"text": "канарейка через MCP", "kind": "note", "tags": ["canary"],
                                            "evidence": [ev()]})
    assert not res.get("isError"), res
    created = res["structuredContent"]
    remember(tc, kind="note", text="не канарейка через REST", tags=["other"])
    mres = mcp_call(tc, "work", "recall", {"query": "канарейка", "tags": ["canary"]})["structuredContent"]
    rres = tc.post("/v1/work/recall", json={"query": "канарейка", "tags": ["canary"]}, headers=H(tc)).json()
    assert [i["id"] for i in mres["items"]] == [i["id"] for i in rres["items"]] == [created["id"]]
    # service error -> isError, no record
    bad = mcp_call(tc, "work", "remember", {"text": "x", "kind": "note", "evidence": []})
    assert bad.get("isError") is True


def test_import_dry_run_then_apply(app_client):  # 06
    """06: import by default only checks the records and names the faulty ones.
    Only an admin can apply it; fields are stored as is; repeating the import is idempotent."""
    tc = app_client
    recs = [{"text": "Shop: redirect через request.url уводит на localhost", "kind": "gotcha", "subject": "project:shop",
             "tags": ["project:shop"], "importance": 3, "source": "import:legacy", "external_ref": "legacy:x1",
             "occurred_at": "2026-09-23T11:59:32+00:00",
             "evidence": [ev("legacy:x1", "dev", "agent")]},
            {"text": "без вида", "evidence": [ev()]}]
    d = tc.post("/v1/work/import", json={"records": recs}, headers=H(tc, "admin")).json()
    assert d["dry_run"] and d["problems"][0]["index"] == 1
    assert tc.post("/v1/work/import", json={"records": recs[:1]}, headers=H(tc, "cowork")).status_code == 403
    r = tc.post("/v1/work/import", json={"records": recs[:1], "dry_run": False}, headers=H(tc, "admin")).json()
    m = tc.get(f"/v1/work/memories/{r['created'][0]['id']}", headers=H(tc)).json()
    assert m["source"] == "import:legacy" and m["importance"] == 3 and m["strength"] == pytest.approx(0.75)
    r2 = tc.post("/v1/work/import", json={"records": recs[:1], "dry_run": False}, headers=H(tc, "admin")).json()
    assert r2["created"][0]["idempotent"] is True
