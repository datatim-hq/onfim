"""Stage 2: light sleep on the canaries with accelerated time (07: M-C*, M-F*, M-S*, M-J*)."""
import json
import statistics
from pathlib import Path

import pytest

from mem import alerts
from mem.sleep import LightSleep

NOW = "2026-09-24T10:00:00+00:00"
CANARIES = {c["t"]: c for c in json.loads((Path(__file__).parent.parent / "fixtures" / "canaries.json")
                                          .read_text(encoding="utf-8"))["canaries"]}
SLOT = "meeting:2026-09-25-chizhov#time"


def H(tc, who="assistant", **extra):
    return {"Authorization": f"Bearer {tc.tokens[who]}", **extra}


def ev(ref, scope="group:gnezdo", speaker="person:x", quote=None):
    d = {"source_ref": ref, "source_scope": scope, "speaker": speaker, "observed_at": NOW}
    if quote:
        d["quote"] = quote
    return d


def put(tc, cid=None, text=None, kind="person_fact", evidence=None, ns="assistant", who="assistant", **kw):
    body = {"text": text or CANARIES[cid]["raw"], "kind": kind, "evidence": evidence or [ev(f"tg:{cid}")], **kw}
    r = tc.post(f"/v1/{ns}/memories", json=body, headers=H(tc, who))
    assert r.status_code == 201, r.text
    return r.json()


def get(tc, mid, ns="assistant", who="assistant"):
    return tc.get(f"/v1/{ns}/memories/{mid}", headers=H(tc, who)).json()


def nights(tc, n, step_days=1.0, **kw):
    last = None
    for _ in range(n):
        tc.svc.clock.advance(step_days * 86400)
        last = LightSleep(tc.svc, **kw).run()
        assert last["status"] == "OK", last
    return last


def chizhov_pair(tc, speaker2, ref2="tg:c402"):
    c401 = put(tc, "c401", kind="topic_fact", slot=SLOT, subject="meeting:chizhov",
               evidence=[ev("tg:c401", speaker="person:chizhov", quote="давай в чт в 15")],
               occurred_at="2026-09-20T10:00:00+00:00")
    c402 = put(tc, "c402", kind="topic_fact", slot=SLOT, subject="meeting:chizhov",
               evidence=[ev(ref2, speaker=speaker2, quote="давай не в чт, а в пт в 16")],
               occurred_at="2026-09-21T10:00:00+00:00")
    assert c402["state"] == "candidate"
    return c401, c402


# ------------------------------------------------------------------ supersession (G1)

def test_g1_same_speaker(app_client):  # M-C2a
    """M-C2a: if the speaker changed the time themselves, the light sleep makes the candidate current and the old record history.
    The report shows the ground G1b-same-speaker and both quotes."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:chizhov")
    nights(tc, 1)
    assert get(tc, c402["id"])["state"] == "current"
    old = get(tc, c401["id"])
    assert old["state"] == "superseded" and old["valid_to"]
    rep = tc.get("/v1/assistant/sleep/latest", headers=H(tc)).json()
    r = rep["replaced"][0]
    assert r["reason"] == "G1b-same-speaker"
    assert r["old"]["quote"]["quote"] == "давай в чт в 15" and r["new"]["quote"]["quote"] == "давай не в чт, а в пт в 16"


def test_g1_third_party_review_then_expire(app_client):  # M-C2b
    """M-C2b: a replacement from a third party's words is not applied; after 14 days a contradiction appears.
    The contradiction carries the quotes of both sides; without a decision in 30 days the candidate is archived
    and the queue item gets status expired."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:sinitsyn")
    nights(tc, 1)
    assert get(tc, c402["id"])["state"] == "candidate"
    top = tc.post("/v1/assistant/recall", json={"query": "созвон с Чижовым"}, headers=H(tc)).json()["items"][0]
    assert top["id"] == c401["id"] and top["contested"] is True
    nights(tc, 13)
    rep = tc.get("/v1/assistant/sleep/latest", headers=H(tc)).json()
    assert len(rep["contradictions_open"]) == 1
    sides = rep["contradictions_open"][0]["sides"]
    assert {s["id"] for s in sides} == {c401["id"], c402["id"]} and all(s["quote"]["quote"] for s in sides)
    nights(tc, 16)   # 30 days without a decision
    assert get(tc, c402["id"])["state"] == "archived"
    assert get(tc, c401["id"])["state"] == "current"
    with tc.svc.pool.connection() as conn:
        st = conn.execute("SELECT status FROM review_items").fetchone()["status"]
        conn.rollback()
    assert st == "expired"


def test_g1_second_independent_source(app_client):  # M-C2c
    """M-C2c: a second independent piece of evidence promotes the candidate with ground G1b-independent."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:sinitsyn")
    r = tc.post(f"/v1/assistant/memories/{c402['id']}/evidence", headers=H(tc),
                json=ev("tg:ivolgina/1", scope="private:ivolgina", speaker="person:ivolgina"))
    assert r.json()["independent"] is True
    nights(tc, 1)
    assert get(tc, c402["id"])["state"] == "current"
    assert tc.get("/v1/assistant/sleep/latest", headers=H(tc)).json()["replaced"][0]["reason"] == "G1b-independent"


def test_g1_owner_confirm(app_client):
    """G1b: the owner's confirmation promotes a candidate from a third party's words at the next sleep."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:sinitsyn")
    assert tc.post(f"/v1/assistant/memories/{c402['id']}/confirm", headers=H(tc)).status_code == 200
    nights(tc, 1)
    assert get(tc, c402["id"])["state"] == "current"


def test_g1c_new_evidence_for_current_blocks(app_client):
    """G1c: new evidence for the current record stops the replacement, even from the same speaker."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:chizhov")
    tc.post(f"/v1/assistant/memories/{c401['id']}/evidence", headers=H(tc),
            json=ev("tg:c401b", scope="private:zyablikov", speaker="person:zyablikov"))
    nights(tc, 1)
    assert get(tc, c402["id"])["state"] == "candidate"


def test_slot_free_promotes(app_client):
    """G1: if the slot's current record was archived, the candidate becomes current without confirmation."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:sinitsyn")
    tc.post(f"/v1/assistant/memories/{c401['id']}/archive", headers=H(tc))
    nights(tc, 1)
    assert get(tc, c402["id"])["state"] == "current"


# ------------------------------------------------------------------ duplicates

def test_dedup_exact(app_client):  # M-C1
    """M-C1: an exact duplicate differing in case and spaces merges with the original.
    The evidence moves to the original, strength grows by the repeat weight, the duplicate is hidden from the list."""
    tc = app_client
    a = put(tc, "c301", subject="person:ivolgina", evidence=[ev("tg:c301", speaker="person:ivolgina")])
    b = put(tc, text="  " + CANARIES["c301"]["raw"].upper().replace("Ё", "Е") + " ", subject="person:ivolgina",
            evidence=[ev("tg:c301-again", speaker="person:ivolgina")])
    s0 = get(tc, a["id"])["strength"]
    nights(tc, 1)
    ga = get(tc, a["id"])
    assert ga["evidence_count"] == 2
    assert ga["strength"] == pytest.approx(s0 * 2 ** (-1 / 365) + 0.05 * (1 - s0 * 2 ** (-1 / 365)), abs=2e-3)
    assert get(tc, b["id"])["duplicate_of"] == a["id"]
    ids = [i["id"] for i in tc.get("/v1/assistant/memories", headers=H(tc)).json()["items"]]
    assert a["id"] in ids and b["id"] not in ids


def test_dedup_respects_audience_and_slot(app_client):  # M-C1
    """M-C1: the nightly merge of duplicates does not widen the audience or mix slots: same_chat records
    from different chats, same_chat and shareable, records of different slots stay separate; a duplicate from the same chat
    merges. Merging runs before promotion: a duplicate candidate of the same text does not replace the current record."""
    tc = app_client
    t = "Иволгина не любит голосовые"
    a = put(tc, text=t, subject="person:x1", visibility="same_chat", evidence=[ev("tg:a1", "chat:111")])
    b = put(tc, text=t, subject="person:x1", visibility="same_chat", evidence=[ev("tg:b1", "chat:222")])
    c = put(tc, text=t, subject="person:x1", visibility="shareable", evidence=[ev("tg:c1", "chat:333")])
    d = put(tc, text=t, subject="person:x1", visibility="same_chat", evidence=[ev("tg:a2", "chat:111")])
    e = put(tc, text=t, subject="person:x2", slot="person:x2:voice", evidence=[ev("tg:e1")])
    f = put(tc, text=t, subject="person:x2", slot="person:x2:calls", evidence=[ev("tg:f1")])
    # slot: current X, candidate Y, then X again: with an open candidate it becomes a second candidate
    x = put(tc, text="Звонить после 18", subject="person:x3", slot="person:x3:time", evidence=[ev("tg:x1", speaker="person:x3")])
    y = put(tc, text="Звонить до 11", subject="person:x3", slot="person:x3:time", evidence=[ev("tg:y1", speaker="person:x3")])
    x2 = put(tc, text="звонить  после 18", subject="person:x3", slot="person:x3:time", evidence=[ev("tg:x2", speaker="person:x3")])
    assert y["state"] == "candidate" and x2["state"] == "candidate"
    nights(tc, 1)
    assert get(tc, d["id"])["duplicate_of"] == a["id"]                        # the same chat: merged
    for m in (b, c, f):
        assert get(tc, m["id"])["duplicate_of"] is None
    assert get(tc, e["id"])["duplicate_of"] is None
    assert get(tc, x2["id"])["duplicate_of"] == x["id"]
    gx = get(tc, x["id"])
    assert gx["state"] == "current" and gx["evidence_count"] == 2              # the repeat reinforced, it did not replace


def test_three_estimates_not_merged(app_client):  # M-C7 (light sleep)
    """M-C7: three estimates and a drawing for one person stay four current records; the light sleep does not merge them."""
    tc = app_client
    ids = [put(tc, c, kind="commitment_mirror", subject="person:zyablikov")["id"] for c in ("c701", "c702", "c703", "c107")]
    nights(tc, 3)
    assert all(get(tc, i)["state"] == "current" and get(tc, i)["duplicate_of"] is None for i in ids)


# ------------------------------------------------------------------ forgetting (G3) and strength

def test_forget_episode_about_day_83(app_client):  # M-F2
    """M-F2: an episode without reinforcement is archived on day 83-85; a decision and a pinned record stay."""
    tc = app_client
    ep = put(tc, "c105", kind="episode", subject="chat:sinitsyn")
    dec = put(tc, text="Решение: mem на Postgres", kind="decision", ns="work", who="cowork",
              evidence=[ev("dev:1", scope="dev", speaker="agent")])
    pinned = put(tc, "c952", kind="episode", subject="project:skvorechnik")
    tc.post(f"/v1/assistant/memories/{pinned['id']}/pin", headers=H(tc))
    archived_on = None
    for day in range(1, 91):
        nights(tc, 1)
        if archived_on is None and get(tc, ep["id"])["state"] == "archived":
            archived_on = day
    assert archived_on is not None and 83 <= archived_on <= 85
    assert get(tc, dec["id"], ns="work", who="cowork")["state"] == "current"
    assert get(tc, pinned["id"])["state"] == "current"
    rep = tc.get("/v1/assistant/sleep/latest", headers=H(tc)).json()
    assert rep["status"] == "OK"


def test_used_keeps_alive(app_client):  # M-S4 (topic fact, H=180)
    """M-S4: a fact marked used every 20 days keeps strength above 0.5 for the whole model year; without events it drops below 0.2."""
    tc = app_client
    kept = put(tc, "c901", kind="topic_fact", subject="person:sinitsyn")
    dropped = put(tc, "c903", kind="topic_fact", subject="person:sinitsyn2")
    for _ in range(18):          # 360 model days, "used" every 20 days
        tc.post(f"/v1/assistant/memories/{kept['id']}/used", headers=H(tc))
        nights(tc, 1, step_days=20)
    k, d = get(tc, kept["id"]), get(tc, dropped["id"])
    assert k["strength"] > 0.5 and d["strength"] < 0.2
    assert k["state"] == "current"


def test_used_recently_blocks_forget(app_client):  # G3 d
    """G3d: a weak episode is not archived until 30 days have passed since the last used; after that it is."""
    tc = app_client
    ep = put(tc, "c106", kind="episode", subject="chat:gnezdo")
    tc.svc.clock.advance(100 * 86400)
    tc.post(f"/v1/assistant/memories/{ep['id']}/used", headers=H(tc))   # strength 0.55*2^-3.3 -> ~0.21 after used
    tc.svc.clock.advance(60 * 86400)                                   # used more than 30 days ago, strength < 0.08
    nights(tc, 1)
    assert get(tc, ep["id"])["state"] == "archived"
    ep2 = put(tc, "c203", kind="episode", subject="chat:klestov")
    tc.svc.clock.advance(120 * 86400)
    tc.post(f"/v1/assistant/memories/{ep2['id']}/used", headers=H(tc))
    tc.svc.clock.advance(10 * 86400)
    with tc.svc.pool.connection() as conn:   # lower the strength by hand to check exactly condition d
        conn.execute("UPDATE memories SET strength=0.05 WHERE id=%s", (ep2["id"],))
        conn.commit()
    nights(tc, 1)
    assert get(tc, ep2["id"])["state"] == "current"


def test_no_equalization_42_canaries(app_client):  # M-S1, M-S5
    """M-S1, M-S5: over 60 nights the strength of 42 canaries does not level out and does not grow without events.
    Confirmed facts are stronger than single ones; no record exceeds 0.9."""
    tc = app_client
    kinds = {"c1": "person_fact", "c2": "topic_fact", "c3": "person_fact", "c4": "topic_fact", "c5": "person_fact",
             "c6": "episode", "c7": "commitment_mirror", "c8": "episode", "c9": "person_fact"}
    ids = {}
    for cid, c in CANARIES.items():
        imp = 1 if c["expect"] == "forget" else (3 if c["expect"] in ("keep", "never_forget") else 2)
        ids[cid] = put(tc, cid, kind=kinds[cid[:2]], subject=f"canary:{cid}", importance=imp)["id"]
    for i, cid in enumerate(("c501", "c502", "c503")):         # three independent confirmations
        for j in range(2):
            tc.post(f"/v1/assistant/memories/{ids[cid]}/evidence", headers=H(tc),
                    json=ev(f"tg:{cid}/{j}", scope=f"group:g{j}", speaker=f"person:p{j}"))
    before = {cid: get(tc, i)["strength"] for cid, i in ids.items()}
    nights(tc, 60)
    after = {cid: get(tc, i) for cid, i in ids.items()}
    alive = [a["strength"] for a in after.values() if a["state"] == "current"]
    assert statistics.pstdev(alive) >= 0.1
    assert all(after[c]["strength"] <= before[c] + 1e-9 for c in ids)          # nothing grew without events
    assert after["c501"]["strength"] > after["c101"]["strength"]              # confirmations > a single fact
    assert all(a["strength"] <= 0.9 for a in after.values())                  # M-S5


# ------------------------------------------------------------------ retention rules

def test_retention_rules(app_client):
    """History older than 180 days is archived, the assistant archive older than a year is deleted, work has no auto-deletion."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:chizhov")
    nights(tc, 1)                                   # c401 -> superseded
    nights(tc, 1, step_days=181)                    # r-history-archive: superseded > 180 days -> archived
    assert get(tc, c401["id"])["state"] == "archived"
    nights(tc, 1, step_days=366)                    # r-assistant-purge: archived > 365 days -> purge
    assert tc.get(f"/v1/assistant/memories/{c401['id']}", headers=H(tc)).status_code == 404
    w = put(tc, text="рабочая заметка", kind="note", ns="work", who="cowork", evidence=[ev("dev:9", "dev", "agent")])
    tc.post(f"/v1/work/memories/{w['id']}/archive", headers=H(tc, "cowork"))
    nights(tc, 1, step_days=400)
    assert get(tc, w["id"], ns="work", who="cowork")["state"] == "archived"   # no auto-purge in work


# ------------------------------------------------------------------ honesty, idempotency, dry_run

def test_honest_status_and_alerts(app_client):  # M-J1
    """M-J1: an error in promote gives status FAILED, in dedup_exact PARTIAL, and every failure sends an alert.
    The other stages still run."""
    tc = app_client
    alerts.SENT.clear()
    r = LightSleep(tc.svc, fail_stage="promote").run()
    assert r["status"] == "FAILED" and r["stages"]["promote"]["status"] == "ERROR"
    assert r["stages"]["decay"]["status"] == "OK"          # the other stages still ran
    r2 = LightSleep(tc.svc, fail_stage="dedup_exact").run()
    assert r2["status"] == "PARTIAL"
    assert len(alerts.SENT) == 2 and "FAILED" in alerts.SENT[0]


def test_idempotent_second_run(app_client):  # M-J2
    """M-J2: a second light sleep run in a row changes nothing in any stage."""
    tc = app_client
    chizhov_pair(tc, "person:chizhov")
    put(tc, "c105", kind="episode", subject="chat:sinitsyn")
    tc.svc.clock.advance(100 * 86400)
    first = LightSleep(tc.svc).run()
    assert sum(s["changed"] for s in first["stages"].values()) > 0
    second = LightSleep(tc.svc).run()
    assert all(s["changed"] == 0 for s in second["stages"].values()), second


def test_dry_run_changes_nothing(app_client):
    """A dry run of the sleep counts the changes but does not apply them; a normal run then promotes the candidate."""
    tc = app_client
    c401, c402 = chizhov_pair(tc, "person:chizhov")
    d = LightSleep(tc.svc).run(dry_run=True)
    assert d["stages"]["promote"]["changed"] == 1 and get(tc, c402["id"])["state"] == "candidate"
    LightSleep(tc.svc).run()
    assert get(tc, c402["id"])["state"] == "current"


def test_disabled_stage(app_client):  # L-6
    """L-6: the forget stage disabled through meta does not run and shows as DISABLED in the report."""
    tc = app_client
    ep = put(tc, "c105", kind="episode", subject="chat:sinitsyn")
    with tc.svc.pool.connection() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES ('sleep_disabled_stages', 'forget') "
                     "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value")
        conn.commit()
    try:
        tc.svc.clock.advance(200 * 86400)
        r = LightSleep(tc.svc).run()
        assert r["stages"]["forget"]["status"] == "DISABLED" and get(tc, ep["id"])["state"] == "current"
    finally:
        with tc.svc.pool.connection() as conn:
            conn.execute("DELETE FROM meta WHERE key='sleep_disabled_stages'")
            conn.commit()


def test_sleep_endpoints(app_client):
    """A manual sleep run is available only to admin from localhost and is a dry run by default; the namespace client sees the report."""
    tc = app_client
    r = tc.post("/v1/_admin/sleep/run", json={}, headers=H(tc, "admin"))
    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert tc.post("/v1/_admin/sleep/run", json={}, headers=H(tc, "admin", **{"X-Real-IP": "1.2.3.4"})).status_code == 403
    assert tc.post("/v1/_admin/sleep/run", json={}, headers=H(tc, "cowork")).status_code == 403
    rep = tc.get("/v1/work/sleep/latest", headers=H(tc, "cowork")).json()
    assert rep["status"] == "OK" and "replaced" in rep and "forgotten" in rep
