"""09: team memory: delegation, speaker, trust, promotion between layers, a member leaving, aliases.
Organization globex is a fictional sales department: a curator, managers, a temporary employee."""
import os

import psycopg
import pytest

NOW = "2026-10-02T10:00:00+00:00"
TEAM, MGR = "gx.sales", "gx.sales.managers"


def ev(ref, who=None, scope="chat:-100", speaker=None, quote=None, **kw):
    d = {"source_ref": ref, "source_scope": scope, "observed_at": NOW, "speaker": speaker or who or "кто-то"}
    if who:
        d["speaker_principal"] = who
    if quote:
        d["quote"] = quote
    d.update(kw)
    return d


@pytest.fixture(scope="module")
def gx(app_client):
    svc = app_client.svc
    svc.create_tenant("globex", "Globex")
    svc.create_tenant("other-co", "Другая компания")
    svc.create_group("gx", "globex", "Globex", kind="org_unit")
    svc.create_group("gx/sales", "globex", "Отдел продаж", parent="gx")
    svc.create_group("gx/sales/managers", "globex", "Без руководства", parent="gx/sales")
    svc.create_group("other", "other-co", "Чужие", kind="org_unit")
    for pid in ("gx-bob", "gx-bob2", "gx-carol", "gx-dave", "gx-erin"):
        svc.create_principal(pid, "globex", "user", clearance=1)
    svc.create_principal("gx-agent", "globex", "service")
    svc.create_principal("ot-user", "other-co", "user")
    svc.add_member("other", "ot-user")
    for pid in ("gx-bob", "gx-bob2", "gx-carol", "gx-dave"):
        svc.add_member("gx/sales/managers", pid)
    svc.add_member("gx/sales", "gx-erin", role="curator")
    svc.create_namespace(TEAM, "globex", "group", owner_group="gx/sales", profile="team")
    svc.create_namespace(MGR, "globex", "group", owner_group="gx/sales/managers", profile="team")
    svc.grant(TEAM, "group", "gx/sales", "contributor", 1)
    svc.grant(TEAM, "principal", "gx-erin", "curator", 1)
    svc.grant(MGR, "group", "gx/sales/managers", "contributor", 1)
    svc.grant(MGR, "principal", "gx-bob", "curator", 1)
    for who in ("bob", "dave", "erin"):
        svc.create_namespace(f"gx.u.{who}", "globex", "personal", owner_principal=f"gx-{who}", profile="assistant")
        svc.grant(f"gx.u.{who}", "principal", f"gx-{who}", "owner", 1)
    return {"agent": svc.create_client("tok-gx-agent", ["*"], ["read", "write", "review", "forget"],
                                       principal_id="gx-agent", delegate_group="gx"),
            "plain": app_client.tokens["cowork"]}


@pytest.fixture(autouse=True)
def _aliases():
    if os.environ.get("MEM_DATABASE_URL"):
        with psycopg.connect(os.environ["MEM_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("DELETE FROM subject_aliases")
    yield


def H(tok, obo=None):
    h = {"Authorization": f"Bearer {tok}"}
    if obo:
        h["X-Mem-On-Behalf-Of"] = obo
    return h


def put(tc, gx, obo, ns, text, kind="topic_fact", evidence=None, expect=201, **kw):
    body = {"text": text, "kind": kind, "evidence": evidence or [ev(f"tg:{abs(hash(text))}", obo)], **kw}
    r = tc.post(f"/v1/{ns}/memories", json=body, headers=H(gx["agent"], obo))
    assert r.status_code == expect, r.text
    return r.json()


# ---------------------------------------------------------------- delegation (TM-1...TM-6)

def test_delegation_rules(app_client, gx):
    """TM-1...TM-6: an agent acts only on behalf of a member or a group of its own organization.
    Without the X-Mem-On-Behalf-Of header 400, other and unknown subjects 403, and a plain client may not send it."""
    tc, a = app_client, gx["agent"]
    assert tc.get(f"/v1/{TEAM}/stats", headers=H(a)).status_code == 400                    # no header
    assert tc.get(f"/v1/{TEAM}/stats", headers=H(a, "gx-bob")).status_code == 200
    for bad in ("nobody", "ot-user", "gx-agent", "group:other", "group:nope"):            # other and unknown
        assert tc.get(f"/v1/{TEAM}/stats", headers=H(a, bad)).status_code == 403, bad
    assert tc.get("/v1/work/stats", headers=H(gx["plain"], "alice")).status_code == 400    # not allowed for a plain client
    assert tc.get("/v1/work/stats", headers=H(a, "gx-bob")).status_code == 403            # another organization


def test_layers_isolation_matrix(app_client, gx):
    """TM-4, TM-23, TM-S7: a member sees their personal layer and the layers of their groups, but not colleagues' personal ones.
    The manager does not see personal layers or the chat without management, acting for a group has no personal layers,
    and scopes narrows search to group layers."""
    tc, a = app_client, gx["agent"]
    put(tc, gx, "gx-bob", "gx.u.bob", "Воронову удобнее звонить после 18:00")
    put(tc, gx, "gx-dave", TEAM, "Бронь по Реке держат пять дней")
    put(tc, gx, "gx-dave", MGR, "Руководитель требует отчёты по пятницам")

    def code(obo, ns):
        return tc.get(f"/v1/{ns}/memories", headers=H(a, obo)).status_code

    assert [code("gx-bob", n) for n in ("gx.u.bob", TEAM, MGR)] == [200, 200, 200]
    assert code("gx-bob", "gx.u.dave") == 403                       # a colleague's personal layer
    assert code("gx-erin", "gx.u.bob") == 403                       # the manager and personal layers (TM-S7)
    assert code("gx-erin", TEAM) == 200 and code("gx-erin", MGR) == 403      # the chat without management
    assert [code("group:gx/sales", n) for n in (TEAM, "gx.u.bob", MGR)] == [200, 403, 403]   # TM-4
    assert [code("group:gx/sales/managers", n) for n in (MGR, TEAM, "gx.u.bob")] == [200, 200, 403]

    def layers(obo, **kw):
        r = tc.post("/v1/recall", json={"query": "бронь звонить отчёты", **kw}, headers=H(a, obo))
        assert r.status_code == 200, r.text
        return sorted(r.json()["namespaces"])

    assert layers("gx-bob") == sorted(["gx.u.bob", TEAM, MGR])
    assert layers("gx-erin") == sorted(["gx.u.erin", TEAM])
    assert layers("group:gx/sales") == [TEAM]                          # an answer in the team chat: no personal layer
    assert layers("gx-bob", scopes=["group"]) == sorted([TEAM, MGR])   # TM-23
    assert tc.post("/v1/recall", json={"query": "x", "namespaces": ["gx.u.bob"]},
                   headers=H(a, "group:gx/sales")).status_code == 403
    assert tc.post("/v1/recall", json={"query": "x", "scopes": ["чужое"]}, headers=H(a, "gx-bob")).status_code == 400


# ---------------------------------------------------------------- speaker and trust (TM-7...TM-11)

def test_speaker_trust_and_independence(app_client, gx):
    """TM-7...TM-11, TM-S6: a member's words are trusted, an outside participant's are not.
    The string "owner" gives no trust in a team layer; independence counts by person, not by chat.
    A speaker from another organization gives 400; in a personal layer the owner's words are trusted with a strength bonus."""
    tc, a = app_client, gx["agent"]
    m = put(tc, gx, "group:gx/sales", TEAM, "Скидка 7 % до конца месяца",
            evidence=[ev("tg:1", None, speaker="Игорь Седов")])
    assert m["trust"] == "untrusted"                                    # outside participant (TM-S6)
    m = put(tc, gx, "group:gx/sales", TEAM, "Бронь по Реке — пять дней", evidence=[ev("tg:2", "gx-dave")])
    assert m["trust"] == "trusted" and m["evidence"][0]["speaker_principal"] == "gx-dave"
    assert m["evidence"][0]["independent_key"] == "p:gx-dave"
    add = lambda e: tc.post(f"/v1/{TEAM}/memories/{m['id']}/evidence", json=e, headers=H(a, "group:gx/sales")).json()
    assert add(ev("tg:3", "gx-dave", scope="chat:-200"))["independent"] is False      # the same person, another chat
    assert add(ev("tg:4", "gx-bob2"))["independent"] is True                           # another member
    put(tc, gx, "group:gx/sales", TEAM, "x", evidence=[ev("tg:5", "ot-user")], expect=400)   # another organization
    # "owner" as a string gives no trust in a team layer
    m = put(tc, gx, "group:gx/sales", TEAM, "Якобы от владельца", evidence=[ev("tg:6", None, speaker="owner")])
    assert m["trust"] == "untrusted"
    # in a personal layer the owner as speaker is trusted and gets the strength bonus
    m = put(tc, gx, "gx-bob", "gx.u.bob", "Аксёнова просит писать, а не звонить", evidence=[ev("tg:7", "gx-bob")])
    assert m["trust"] == "trusted" and m["strength"] == 0.65


def test_team_rule_needs_curator(app_client, gx):
    """TM-11: a rule from a member waits for the layer's curator, while a rule from the curator applies at once.
    Only the curator sees the queue item."""
    tc, a = app_client, gx["agent"]
    m = put(tc, gx, "group:gx/sales", TEAM, "Подборки клиентам отправляем только через CRM", kind="convention",
            evidence=[ev("tg:10", "gx-bob2")])
    assert m["state"] == "candidate" and m["trust"] == "trusted"        # from a member: waits for the curator (TM-11)
    q = tc.get("/v1/review", headers=H(a, "gx-erin")).json()["items"]
    item = next(i for i in q if i["type"] == "rule_confirm")
    assert item["layer"]["namespace"] == TEAM and item["proposed_by"] == "gx-bob2"
    assert tc.get(f"/v1/{TEAM}/review", headers=H(a, "gx-bob2")).status_code == 403    # not the curator
    assert tc.get("/v1/review", headers=H(a, "gx-bob2")).json()["items"] == []
    r = tc.post(f"/v1/{TEAM}/review/{item['id']}/accept", headers=H(a, "gx-erin"))
    assert r.status_code == 200, r.text
    assert tc.get(f"/v1/{TEAM}/memories/{m['id']}", headers=H(a, "gx-erin")).json()["state"] == "current"
    m2 = put(tc, gx, "group:gx/sales", TEAM, "Бронь продлеваем только с согласия руководителя", kind="convention",
             evidence=[ev("tg:11", "gx-erin")])
    assert m2["state"] == "current"                                     # the curator said it: applies at once


def test_team_rule_with_slot_waits_for_curator_overnight(app_client, gx):
    """TM-11: a member's rule with a slot does not take effect by itself overnight when the slot is empty; it waits for
    the curator (the hold used to look only at untrusted rules, and the light sleep promoted it as "the slot is free")."""
    from mem.sleep import LightSleep
    tc, a = app_client, gx["agent"]
    m = put(tc, gx, "group:gx/sales", TEAM, "Скидку больше 3 % согласуем с руководителем", kind="convention",
            slot="team:sales#discount", evidence=[ev("tg:12", "gx-bob2")])
    assert m["state"] == "candidate" and m["trust"] == "trusted"
    tc.svc.clock.advance(86400)
    assert LightSleep(tc.svc).run()["status"] == "OK"
    assert tc.get(f"/v1/{TEAM}/memories/{m['id']}", headers=H(a, "gx-erin")).json()["state"] == "candidate"
    item = next(i for i in tc.get("/v1/review", headers=H(a, "gx-erin")).json()["items"]
                if i["type"] == "rule_confirm" and m["id"] in [r["id"] for r in i["records"]])
    assert tc.post(f"/v1/{TEAM}/review/{item['id']}/accept", headers=H(a, "gx-erin")).status_code == 200
    assert tc.get(f"/v1/{TEAM}/memories/{m['id']}", headers=H(a, "gx-erin")).json()["state"] == "current"


def _open_rule_items(tc, a, mid):
    return [i for i in tc.get("/v1/review", headers=H(a, "gx-erin")).json()["items"]
            if i["type"] == "rule_confirm" and mid in [r["id"] for r in i["records"]]]


def test_team_rule_into_occupied_slot_waits_and_replaces_on_accept(app_client, gx):
    """TM-11: a member's rule into an occupied slot also waits for the curator (a rule_confirm item); gate G1 does not
    promote it at night; after acceptance it replaces the current one, with one current record in the slot (D-2)."""
    from mem.sleep import LightSleep
    tc, a = app_client, gx["agent"]
    slot = "team:sales#deposit"
    cur = put(tc, gx, "group:gx/sales", TEAM, "Задаток берём только по договору", kind="convention", slot=slot,
              evidence=[ev("tg:30", "gx-erin")])
    assert cur["state"] == "current"
    m = put(tc, gx, "group:gx/sales", TEAM, "Задаток берём по договору или по расписке", kind="convention",
            slot=slot, evidence=[ev("tg:31", "gx-bob2")])
    assert m["state"] == "candidate" and _open_rule_items(tc, a, m["id"])
    tc.svc.clock.advance(86400)
    assert LightSleep(tc.svc).run()["status"] == "OK"
    get = lambda i: tc.get(f"/v1/{TEAM}/memories/{i}", headers=H(a, "gx-erin")).json()
    assert get(m["id"])["state"] == "candidate" and get(cur["id"])["state"] == "current"
    item = _open_rule_items(tc, a, m["id"])[0]
    assert tc.post(f"/v1/{TEAM}/review/{item['id']}/accept", headers=H(a, "gx-erin")).status_code == 200
    assert get(m["id"])["state"] == "current" and get(cur["id"])["state"] == "superseded"
    items = tc.get(f"/v1/{TEAM}/memories", params={"slot": slot}, headers=H(a, "gx-erin")).json()["items"]
    assert [i["id"] for i in items] == [m["id"]]


def test_team_rule_duplicate_of_curator_rule_is_merged_and_item_closed(app_client, gx):
    """TM-11: a member proposed a rule (waiting for the curator), then the curator said the same; at night the duplicate
    merges into the curator's current rule, the duplicate's confirmation item closes, one current record in the slot."""
    from mem.sleep import LightSleep
    tc, a = app_client, gx["agent"]
    slot = "team:sales#keys"
    m = put(tc, gx, "group:gx/sales", TEAM, "Ключи от объекта выдаём под роспись", kind="convention", slot=slot,
            evidence=[ev("tg:40", "gx-bob2")])
    assert m["state"] == "candidate" and _open_rule_items(tc, a, m["id"])
    c = put(tc, gx, "group:gx/sales", TEAM, "Ключи от объекта выдаём под роспись", kind="convention", slot=slot,
            evidence=[ev("tg:41", "gx-erin")])
    assert c["state"] == "current"
    tc.svc.clock.advance(86400)
    assert LightSleep(tc.svc).run()["status"] == "OK"
    assert not _open_rule_items(tc, a, m["id"]) and not _open_rule_items(tc, a, c["id"])
    got = tc.get(f"/v1/{TEAM}/memories/{c['id']}", headers=H(a, "gx-erin")).json()
    assert got["state"] == "current" and got["evidence_count"] == 2


def test_team_rule_explicit_supersedes_waits_for_curator(app_client, gx):
    """TM-11: a member cannot replace the curator's rule with an explicit supersedes: their rule becomes a
    candidate with a confirmation item and the current one stays; after the curator accepts, the replacement happens (also for
    a rule without a slot)."""
    tc, a = app_client, gx["agent"]
    get = lambda i: tc.get(f"/v1/{TEAM}/memories/{i}", headers=H(a, "gx-erin")).json()
    for slot in ("team:sales#viewing", None):
        kw = {"slot": slot} if slot else {}
        old = put(tc, gx, "group:gx/sales", TEAM, f"Показ подтверждаем за сутки {slot}", kind="convention",
                  evidence=[ev(f"tg:60{slot}", "gx-erin")], **kw)
        new = put(tc, gx, "group:gx/sales", TEAM, f"Показ подтверждаем за два часа {slot}", kind="convention",
                  supersedes=old["id"], evidence=[ev(f"tg:61{slot}", "gx-bob2")])
        assert new["state"] == "candidate" and get(old["id"])["state"] == "current"
        item = _open_rule_items(tc, a, new["id"])[0]
        assert tc.post(f"/v1/{TEAM}/review/{item['id']}/accept", headers=H(a, "gx-erin")).status_code == 200
        assert get(new["id"])["state"] == "current" and get(old["id"])["state"] == "superseded"


def test_expired_rule_candidate_closes_its_item(app_client, gx):
    """TM-11: a rule candidate the curator never confirmed is archived after 30 days, and its confirmation item
    closes the same night (previously only contradiction items were closed)."""
    from mem.sleep import LightSleep
    tc, a = app_client, gx["agent"]
    m = put(tc, gx, "group:gx/sales", TEAM, "Показы по воскресеньям не проводим", kind="convention",
            slot="team:sales#sundays", evidence=[ev("tg:50", "gx-bob2")])
    assert _open_rule_items(tc, a, m["id"])
    tc.svc.clock.advance(31 * 86400)
    assert LightSleep(tc.svc).run()["status"] == "OK"
    assert not _open_rule_items(tc, a, m["id"])


# ---------------------------------------------------------------- promotion between layers (TM-13...TM-19)

def test_promotion_through_curator(app_client, gx):
    """TM-13...TM-19: promoting a personal record to the team layer goes through the curator; the source record does not change.
    A repeated promotion before the decision gives 409, after a rejection it is allowed; without share_quotes quotes are not copied."""
    tc, a = app_client, gx["agent"]
    src = put(tc, gx, "gx-bob", "gx.u.bob", "С 10 октября цены на модель X3 вырастут на 4 %",
              evidence=[ev("tg:20", None, speaker="Игорь Седов", quote="с десятого плюс четыре процента")])
    url = f"/v1/gx.u.bob/memories/{src['id']}/promote"
    assert tc.post(url, json={"to": TEAM}, headers=H(a, "gx-dave")).status_code == 403    # someone else's personal layer
    assert tc.post(url, json={"to": "gx.u.bob"}, headers=H(a, "gx-bob")).status_code == 400
    assert tc.post(url, json={"to": "work"}, headers=H(a, "gx-bob")).status_code == 403    # another organization
    r = tc.post(url, json={"to": TEAM, "note": "от поставщика"}, headers=H(a, "gx-bob"))
    assert r.status_code == 200 and r.json()["status"] == "open", r.text
    rid = r.json()["review_id"]
    assert tc.post(url, json={"to": TEAM}, headers=H(a, "gx-bob")).status_code == 409      # TM-18
    team = lambda: [m["text"] for m in tc.get(f"/v1/{TEAM}/memories?state=all", headers=H(a, "gx-erin")).json()["items"]]
    assert src["text"] not in team()                                                        # empty before the decision
    item = next(i for i in tc.get("/v1/review", headers=H(a, "gx-erin")).json()["items"] if i["id"] == rid)
    assert item["type"] == "promotion" and item["proposed_by"] == "gx-bob"
    assert item["proposal"]["text"] == src["text"] and item["proposal"]["source_ns"] == "gx.u.bob"
    assert tc.post(f"/v1/{TEAM}/review/{rid}/accept", headers=H(a, "gx-bob")).status_code == 403   # not the curator
    r = tc.post(f"/v1/{TEAM}/review/{rid}/accept", headers=H(a, "gx-erin"))
    assert r.status_code == 200, r.text
    new = tc.get(f"/v1/{TEAM}/memories/{r.json()['memory_id']}", headers=H(a, "gx-erin")).json()
    assert new["text"] == src["text"] and new["promoted_from"] == src["id"] and new["source"] == "promote:gx.u.bob"
    assert new["evidence"][0]["quote"] == "с десятого плюс четыре процента" and new["trust"] == "untrusted"
    again = tc.get(f"/v1/gx.u.bob/memories/{src['id']}", headers=H(a, "gx-bob")).json()
    assert (again["state"], again["text"], again["evidence_count"]) == ("current", src["text"], 1)   # the source is intact

    # rejection: nothing in the team layer; repeating after rejection is possible; no quotes
    src2 = put(tc, gx, "gx-bob", "gx.u.bob", "Поставщик готов давать рассрочку на доставку",
               evidence=[ev("tg:21", "gx-bob", quote="рассрочка на доставку есть")])
    url2 = f"/v1/gx.u.bob/memories/{src2['id']}/promote"
    rid2 = tc.post(url2, json={"to": TEAM}, headers=H(a, "gx-bob")).json()["review_id"]
    assert tc.post(f"/v1/{TEAM}/review/{rid2}/reject", headers=H(a, "gx-erin")).status_code == 200
    assert src2["text"] not in team()
    rid3 = tc.post(url2, json={"to": TEAM, "share_quotes": False}, headers=H(a, "gx-bob")).json()["review_id"]
    r = tc.post(f"/v1/{TEAM}/review/{rid3}/accept", headers=H(a, "gx-erin"))
    new = tc.get(f"/v1/{TEAM}/memories/{r.json()['memory_id']}", headers=H(a, "gx-erin")).json()
    assert new["evidence"][0]["quote"] is None and new["evidence"][0]["speaker_principal"] == "gx-bob"
    assert new["trust"] == "trusted"                                    # the speaker is a team member


def test_promotion_auto_and_rule_by_curator(app_client, gx):
    """TM-19: in a layer with promotion_auto for topic_fact the promotion applies at once, without the curator."""
    tc, a, svc = app_client, gx["agent"], app_client.svc
    with psycopg.connect(os.environ["MEM_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("UPDATE namespaces SET settings = '{\"promotion_auto\": [\"topic_fact\"]}' WHERE id=%s", (MGR,))
    try:
        src = put(tc, gx, "gx-dave", "gx.u.dave", "Ключи от сорок пятой лежат у секретаря")
        r = tc.post(f"/v1/gx.u.dave/memories/{src['id']}/promote", json={"to": MGR}, headers=H(a, "gx-dave"))
        assert r.status_code == 200 and r.json()["status"] == "accepted", r.text       # TM-19: without the curator
        got = tc.get(f"/v1/{MGR}/memories/{r.json()['memory_id']}", headers=H(a, "gx-carol")).json()
        assert got["promoted_from"] == src["id"]
    finally:
        with psycopg.connect(os.environ["MEM_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("UPDATE namespaces SET settings = '{}' WHERE id=%s", (MGR,))
    assert svc is not None


# ---------------------------------------------------------------- a member leaving (TM-26...TM-28)

def test_offboard(app_client, gx):
    """TM-26...TM-28: a member who left loses access at once, their personal layer is closed to everyone, their words in the team stay.
    When the retention term ends, the light sleep deletes the personal layer."""
    tc, a, svc = app_client, gx["agent"], app_client.svc
    svc.create_principal("gx-temp", "globex", "user", clearance=1)
    svc.add_member("gx/sales/managers", "gx-temp")
    svc.create_namespace("gx.u.temp", "globex", "personal", owner_principal="gx-temp", profile="assistant")
    svc.grant("gx.u.temp", "principal", "gx-temp", "owner", 1)
    put(tc, gx, "gx-temp", "gx.u.temp", "Личная заметка уходящего")
    team = put(tc, gx, "group:gx/sales", TEAM, "Показы в Парк-Хаусе только по записи", evidence=[ev("tg:30", "gx-temp")])
    out = svc.offboard("gx-temp", purge_after_days=30)
    assert out["groups_left"] == ["gx/sales/managers"] and out["personal_namespaces"] == ["gx.u.temp"]
    assert tc.get("/v1/gx.u.temp/memories", headers=H(a, "gx-temp")).status_code == 403    # at once
    assert tc.get("/v1/gx.u.temp/memories", headers=H(a, "gx-erin")).status_code == 403  # and for everyone
    kept = tc.get(f"/v1/{TEAM}/memories/{team['id']}", headers=H(a, "gx-erin")).json()
    assert kept["evidence"][0]["speaker_principal"] == "gx-temp"                            # their words stay
    # deletion by term, done by the light sleep
    tc.post("/v1/_stand/clock", json={"advance_days": 31}, headers=H(tc.tokens["admin"]))
    r = tc.post("/v1/_admin/sleep/run", json={"kind": "light", "dry_run": False}, headers=H(tc.tokens["admin"]))
    assert r.status_code == 200, r.text
    with psycopg.connect(os.environ["MEM_DATABASE_URL"]) as conn:
        assert conn.execute("SELECT count(*) FROM memories WHERE namespace='gx.u.temp'").fetchone()[0] == 0
        assert conn.execute("SELECT settings ? 'purged_at' FROM namespaces WHERE id='gx.u.temp'").fetchone()[0]


# ---------------------------------------------------------------- external key, aliases, CRM mirror (TM-29...TM-33)

def test_crm_mirror_aliases_and_forget_subject(app_client, gx):
    """TM-29...TM-33: an open CRM deal mirror is trusted and does not decay; after closing it decays.
    An alias links subjects in both directions; "forget a subject" requires confirm with the number of records."""
    tc, a, svc = app_client, gx["agent"], app_client.svc
    deal = put(tc, gx, "gx-bob", "gx.u.bob", "Сделка: заказ 145, модель X3 — демонстрация проведена", kind="crm_mirror",
               subject="crm:contact:77", external_ref="crm:deal:d-1042",
               evidence=[ev("crm:deal:d-1042@17", None, scope="crm", speaker="crm", independent_key="crm:deal:d-1042")])
    assert deal["trust"] == "trusted"
    put(tc, gx, "gx-bob", "gx.u.bob", "Клиент просит звонить после 18:00", kind="person_fact", subject="person:tg123")
    find = lambda subj: len(tc.get(f"/v1/gx.u.bob/memories?subject={subj}", headers=H(a, "gx-bob")).json()["items"])
    assert find("person:tg123") == 1 and find("crm:contact:77") == 1
    r = tc.post("/v1/gx.u.bob/aliases", json={"alias": "person:tg123", "canonical": "crm:contact:77"}, headers=H(a, "gx-bob"))
    assert r.status_code == 200, r.text
    assert find("person:tg123") == 2 and find("crm:contact:77") == 2            # TM-31: both directions
    # an open mirror does not decay; after closing it does
    tc.post("/v1/_stand/clock", json={"advance_days": 28}, headers=H(tc.tokens["admin"]))
    assert tc.get(f"/v1/gx.u.bob/memories/{deal['id']}", headers=H(a, "gx-bob")).json()["strength"] == deal["strength"]
    assert tc.post(f"/v1/gx.u.bob/memories/{deal['id']}/close", headers=H(a, "gx-bob")).status_code == 200
    tc.post("/v1/_stand/clock", json={"advance_days": 14}, headers=H(tc.tokens["admin"]))
    assert tc.get(f"/v1/gx.u.bob/memories/{deal['id']}", headers=H(a, "gx-bob")).json()["strength"] < deal["strength"]
    # "forget a subject" across the organization
    rep = svc.forget_subject("globex", "person:tg123", dry_run=True)
    assert rep["matched"] == 2 and rep["by_namespace"] == {"gx.u.bob": 2}
    with pytest.raises(Exception):
        svc.forget_subject("globex", "person:tg123", dry_run=False, confirm=1)
    assert svc.forget_subject("globex", "person:tg123", dry_run=False, confirm=2)["purged"] == 2
    assert find("crm:contact:77") == 0
