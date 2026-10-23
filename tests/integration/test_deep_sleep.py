"""Stage 4: deep sleep with a substituted LLM (07: M-C6, M-D1...D3, M-C7, S-6, decision queue)."""
import pytest

from mem import alerts
from mem.deep_sleep import DeepSleep
from mem.llm import FakeLLM

NOW = "2026-09-24T10:00:00+00:00"


def H(tc, who="assistant"):
    return {"Authorization": f"Bearer {tc.tokens[who]}"}


def ev(ref, speaker="person:x", scope="group:gnezdo", quote=None):
    d = {"source_ref": ref, "source_scope": scope, "speaker": speaker, "observed_at": NOW}
    if quote:
        d["quote"] = quote
    return d


def put(tc, text, kind="person_fact", subject="person:shcheglova", ns="assistant", who="assistant", **kw):
    kw.setdefault("evidence", [ev(f"tg:{abs(hash(text)) % 10**8}")])
    r = tc.post(f"/v1/{ns}/memories", json={"text": text, "kind": kind, "subject": subject, **kw}, headers=H(tc, who))
    assert r.status_code == 201, r.text
    return r.json()


def get(tc, mid):
    return tc.get(f"/v1/assistant/memories/{mid}", headers=H(tc)).json()


def reviews(tc, status="open"):
    return tc.get(f"/v1/assistant/review?status={status}", headers=H(tc)).json()["items"]


def shcheglova(tc):
    a = put(tc, "Анна Щеглова отвечает только письменно", evidence=[ev("tg:s1", "person:shcheglova", quote="пишите, не звоните")])
    b = put(tc, "Анна Щеглова предпочитает звонки", evidence=[ev("tg:s2", "person:sinitsyn", quote="Щеглова любит, когда ей звонят")])
    return a, b


def contra_llm():
    return FakeLLM({"report_contradictions": lambda u: {"pairs": [{"a": 0, "b": 1, "explanation": "письменно vs звонки",
                                                                   "attribute": "канал связи"}]}
                    if "Щеглова" in u else {"pairs": []}})


# ------------------------------------------------------------------ contradictions

def test_contradiction_goes_to_owner(app_client):  # M-C6
    """M-C6: deep sleep hands a contradiction without a slot to the owner: a queue item with two quotes and a slot.
    The sleep itself replaces nothing and does not duplicate the item. The owner's decision reinforces the chosen record,
    the other goes to history under a shared slot, and a repeated decision gives 409."""
    tc = app_client
    a, b = shcheglova(tc)
    r = DeepSleep(tc.svc, llm=contra_llm()).run()
    assert r["status"] == "OK" and r["stages"]["contradictions"]["changed"] == 1
    items = reviews(tc)
    assert len(items) == 1 and items[0]["type"] == "contradiction"
    assert {x["quote"]["quote"] for x in items[0]["records"]} == {"пишите, не звоните", "Щеглова любит, когда ей звонят"}
    assert items[0]["proposal"]["suggested_slot"].startswith("person:shcheglova#")
    assert get(tc, a["id"])["state"] == get(tc, b["id"])["state"] == "current"      # nothing replaced
    DeepSleep(tc.svc, llm=contra_llm()).run()
    assert len(reviews(tc, "all")) == 1                                              # not duplicated
    rep = tc.get("/v1/assistant/sleep/latest?kind=deep", headers=H(tc)).json()
    assert rep["kind"] == "deep_sleep" and len(rep["contradictions_open"]) == 1
    # the owner picks the right record
    s_before = get(tc, a["id"])["strength"]
    d = tc.post(f"/v1/assistant/review/{items[0]['id']}/accept", json={"winner": a["id"]}, headers=H(tc))
    assert d.status_code == 200, d.text
    ga, gb = get(tc, a["id"]), get(tc, b["id"])
    assert ga["state"] == "current" and gb["state"] == "superseded" and ga["strength"] > s_before
    assert ga["slot"] == gb["slot"] and ga["slot"].startswith("person:shcheglova#")
    assert tc.post(f"/v1/assistant/review/{items[0]['id']}/accept", json={"winner": a["id"]}, headers=H(tc)).status_code == 409


def test_review_requires_review_right_and_winner(app_client):
    """A contradiction decision without a winner gives 400, the queue without the review right 403."""
    tc = app_client
    shcheglova(tc)
    DeepSleep(tc.svc, llm=contra_llm()).run()
    rid = reviews(tc)[0]["id"]
    assert tc.post(f"/v1/assistant/review/{rid}/accept", json={}, headers=H(tc)).status_code == 400
    assert tc.get("/v1/work/review", headers=H(tc, "claude-code")).status_code == 403


def test_nothing_sensitive_goes_to_llm(app_client):  # S-6
    """S-6: owner_only and level 3 records never go to the LLM, ordinary records of the same subject do."""
    tc = app_client
    shcheglova(tc)
    put(tc, "Щеглова: личное, только владельцу", visibility="owner_only")
    put(tc, "Щеглова: строго ограниченное", classification=3)
    llm = contra_llm()
    DeepSleep(tc.svc, llm=llm).run()
    sent = " ".join(x["user"] for x in llm.sent)
    assert "только владельцу" not in sent and "строго ограниченное" not in sent and "письменно" in sent


def test_invalid_llm_answer_partial(app_client):  # M-D3
    """M-D3: non-JSON from the LLM breaks only the contradictions stage: the run is PARTIAL with an alert.
    The other stages pass and the queue stays empty."""
    tc = app_client
    shcheglova(tc)
    alerts.SENT.clear()
    r = DeepSleep(tc.svc, llm=FakeLLM({"report_contradictions": "это не JSON"})).run()
    assert r["status"] == "PARTIAL" and r["stages"]["contradictions"]["status"] == "ERROR"
    assert r["stages"]["stale"]["status"] == "OK" and alerts.SENT and "PARTIAL" in alerts.SENT[-1]
    assert reviews(tc) == []


def test_cloud_model_for_sleep_is_refused(app_client, monkeypatch):  # L-4
    """L-4: a cloud model in the sleep settings (either of the two): every model stage fails with a clear error and an alert
    before anything is sent, since mem has no label dictionary and records would go out without masking. The stage without
    a model passes, the queue is empty. The setup error shows even when there are no records for the model."""
    from mem import llm as llmmod
    tc = app_client
    shcheglova(tc)
    sent = []
    monkeypatch.setattr(llmmod.AnthropicLLM, "call", lambda self, *a: sent.append(a))
    monkeypatch.setattr(llmmod.OpenAICompatLLM, "call", lambda self, *a: sent.append(a))
    monkeypatch.setenv("MEM_ANTHROPIC_API_KEY", "k")                 # a key is present: the refusal is due to the route, not the key
    monkeypatch.setenv("MEM_DEEP_MODEL_REASON", "gigachat:GigaChat-2-Max")
    monkeypatch.setenv("MEM_DEEP_MODEL_SUMMARY", "gigachat:GigaChat-2-Max")
    alerts.SENT.clear()
    r = DeepSleep(tc.svc).run()
    assert r["status"] == "PARTIAL" and r["stages"]["stale"]["status"] == "OK"
    for st in ("contradictions", "slot_suggestions", "summaries"):
        assert r["stages"][st]["status"] == "ERROR" and "not supported" in r["stages"][st]["error"], st
    assert sent == [] and reviews(tc) == [] and alerts.SENT and "PARTIAL" in alerts.SENT[-1]


def test_deep_sleep_without_model_never_calls_a_provider(app_client, monkeypatch):
    """05: no deep-sleep model configured: the model stages are DISABLED, nothing is sent to any model and no alert
    is raised, even with an Anthropic key in the settings. The stage without a model still runs; setting one model
    enables only its stage."""
    from mem import llm as llmmod
    tc = app_client
    shcheglova(tc)
    sent = []
    monkeypatch.setattr(llmmod.AnthropicLLM, "call", lambda self, *a: (sent.append(a), {"summary": "summary"})[1])
    monkeypatch.setenv("MEM_ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("MEM_DEEP_MODEL_REASON", raising=False)
    monkeypatch.delenv("MEM_DEEP_MODEL_SUMMARY", raising=False)
    alerts.SENT.clear()
    r = DeepSleep(tc.svc).run()
    assert r["status"] == "OK" and r["stages"]["stale"]["status"] == "OK"
    assert {st: r["stages"][st]["status"] for st in ("contradictions", "slot_suggestions", "summaries")} == \
        {"contradictions": "DISABLED", "slot_suggestions": "DISABLED", "summaries": "DISABLED"}
    assert sent == [] and alerts.SENT == []
    monkeypatch.setenv("MEM_DEEP_MODEL_SUMMARY", "claude-haiku-4-5-20251001")      # set explicitly: summaries only
    r = DeepSleep(tc.svc).run()
    assert r["stages"]["summaries"]["status"] == "OK", r["stages"]["summaries"]
    assert r["stages"]["contradictions"]["status"] == "DISABLED" and r["status"] == "OK"


def test_slot_suggestion_accept(app_client):
    """An accepted slot proposal links the records: the newer stays current, the older goes to history."""
    tc = app_client
    old = put(tc, "Кирилл Снегирёв — электрик", subject="person:snegirev", occurred_at="2026-09-01T10:00:00+00:00")
    new = put(tc, "Кирилл Снегирёв теперь прораб", subject="person:snegirev", occurred_at="2026-09-20T10:00:00+00:00")
    llm = FakeLLM({"suggest_slots": lambda u: {"groups": [{"attribute": "роль", "indices": [0, 1]}]}
                   if "Снегирёв" in u else {"groups": []}})
    DeepSleep(tc.svc, llm=llm).run()
    it = [i for i in reviews(tc) if i["type"] == "slot_suggestion"][0]
    assert it["proposal"]["slot"] == "person:snegirev#роль"
    assert tc.post(f"/v1/assistant/review/{it['id']}/accept", json={}, headers=H(tc)).status_code == 200
    assert get(tc, new["id"])["state"] == "current" and get(tc, old["id"])["state"] == "superseded"
    assert get(tc, new["id"])["slot"] == "person:snegirev#роль"


def test_bad_indices_skip_item_not_stage(app_client):
    """The model returned a group with invalid numbers and the stage used to fail for all subjects. Now the invalid item
    is skipped, the valid ones are processed and the sleep ends OK."""
    tc = app_client
    put(tc, "Кирилл Снегирёв — электрик", subject="person:snegirev", occurred_at="2026-09-01T10:00:00+00:00")
    put(tc, "Кирилл Снегирёв теперь прораб", subject="person:snegirev", occurred_at="2026-09-20T10:00:00+00:00")
    shcheglova(tc)
    llm = FakeLLM({
        "suggest_slots": lambda u: {"groups": [{"attribute": "мусор", "indices": [0]},
                                               {"attribute": "мусор", "indices": [0, 7]},
                                               {"attribute": "роль", "indices": [0, 1]}]}
        if "Снегирёв" in u else {"groups": []},
        "report_contradictions": lambda u: {"pairs": [{"a": 0, "b": 0, "explanation": "сама с собой"},
                                                      {"a": 0, "b": 9, "explanation": "нет такой"},
                                                      {"a": 0, "b": 1, "explanation": "звонки и письма"}]}
        if "Щеглова" in u else {"pairs": []}})
    alerts.SENT.clear()
    r = DeepSleep(tc.svc, llm=llm).run()
    assert r["status"] == "OK", r
    assert r["stages"]["slot_suggestions"]["status"] == "OK" and r["stages"]["contradictions"]["status"] == "OK"
    types = sorted(i["type"] for i in reviews(tc))
    assert types == ["contradiction", "slot_suggestion"]                   # valid items of both subjects are processed
    assert r["stages"]["slot_suggestions"]["details"]["skipped_invalid"] == 2
    assert r["stages"]["contradictions"]["details"]["skipped_invalid"] == 2
    assert not any("PARTIAL" in m for m in alerts.SENT)
    assert any("items skipped: 4" in m for m in alerts.SENT)        # the skip is visible to the owner
    rep = tc.get("/v1/assistant/sleep/latest?kind=deep", headers=H(tc)).json()
    assert rep["model_items_skipped"] == 4


# ------------------------------------------------------------------ "stale?"

def test_stale_gotcha(app_client):
    """A weakened gotcha goes to the queue as "stale?".
    The answer "still relevant" reinforces it, and it does not come back to the queue at the next sleep."""
    tc = app_client
    g = put(tc, "Грабля: деплой через FTP", kind="gotcha", subject="project:x", ns="work", who="cowork", importance=1,
            evidence=[{"source_ref": "dev:g1", "source_scope": "dev", "speaker": "agent", "observed_at": NOW}])
    tc.svc.clock.advance(100 * 86400)          # 0.35 -> ~0.24 < 0.25
    DeepSleep(tc.svc, llm=FakeLLM()).run()
    items = tc.get("/v1/work/review", headers=H(tc, "cowork")).json()["items"]
    assert len(items) == 1 and items[0]["type"] == "stale"
    before = tc.get(f"/v1/work/memories/{g['id']}", headers=H(tc, "cowork")).json()["strength"]
    tc.post(f"/v1/work/review/{items[0]['id']}/reject", json={}, headers=H(tc, "cowork"))   # "still relevant"
    after = tc.get(f"/v1/work/memories/{g['id']}", headers=H(tc, "cowork")).json()
    assert after["state"] == "current" and after["strength"] > before
    DeepSleep(tc.svc, llm=FakeLLM()).run()
    assert tc.get("/v1/work/review", headers=H(tc, "cowork")).json()["items"] == []


# ------------------------------------------------------------------ episode summaries (G2)

DROZDOV = ["15.09.2026: поставщик Игорь Дроздов прислал счёт на два дня позже обещанного.",
           "17.09.2026: Игорь Дроздов привёз доски на день позже согласованного срока.",
           "19.09.2026: Игорь Дроздов снова задержал документы на три дня против обещания.",
           "22.09.2026: Игорь Дроздов опять прислал акт позже назначенной даты."]


def episodes(tc, texts, subject="supplier:drozdov", month="2026-05"):
    return [put(tc, t, kind="episode", subject=subject, occurred_at=f"{month}-{10 + i:02d}T10:00:00+00:00",
                evidence=[ev(f"tg:ep{i}{subject}", quote=t[:60])]) for i, t in enumerate(texts)]


def test_summary_passes_g2(app_client):  # M-D1
    """M-D1: an episode summary that passes G2 is created as an insight with the evidence of all sources.
    The sources stay current with a summarized_into event; a repeated sleep makes no second summary."""
    tc = app_client
    eps = episodes(tc, DROZDOV)
    summary = "Игорь Дроздов в сентябре систематически опаздывал: счёт 15.09, доски 17.09, документы 19.09, акт 22.09."
    llm = FakeLLM({"write_summary": {"summary": summary}})
    r = DeepSleep(tc.svc, llm=llm).run()
    assert r["status"] == "OK", r
    applied = r["stages"]["summaries"]["details"]["applied"]
    assert len(applied) == 1 and applied[0]["id"]
    s = get(tc, applied[0]["id"])
    assert s["kind"] == "insight" and s["source"] == "sleep:deep" and s["text"] == summary and s["evidence_count"] == 4
    for e in eps:
        g = get(tc, e["id"])
        assert g["state"] == "current" and g["events"][0]["type"] == "summarized_into"
    DeepSleep(tc.svc, llm=llm).run()                                   # does not summarize again
    assert len([m for m in tc.get("/v1/assistant/memories?kind=insight", headers=H(tc)).json()["items"]]) == 1


def test_summary_distortion_rejected(app_client):  # M-D2
    """M-D2: a summary with a distorted date is rejected by G2a and goes to the owner as a merge_suggestion.
    The reason shows in the report; if the owner accepts it anyway, the summary is created."""
    tc = app_client
    eps = episodes(tc, ["Глеб обещал Зябликову чертёж веранды до 30.09.2026.",
                        "Зябликов напомнил про чертёж веранды, срок прежний — 30.09."], subject="person:zyablikov")
    llm = FakeLLM({"write_summary": {"summary": "Глеб обещал Зябликову чертёж веранды до 1 октября."}})
    r = DeepSleep(tc.svc, llm=llm).run()
    rej = r["stages"]["summaries"]["details"]["rejected"]
    assert len(rej) == 1 and rej[0]["g2_failed"] == ["a"]
    assert tc.get("/v1/assistant/memories?kind=insight", headers=H(tc)).json()["items"] == []
    it = [i for i in reviews(tc) if i["type"] == "merge_suggestion"][0]
    assert "1 октября" in it["proposal"]["summary"]
    rep = tc.get("/v1/assistant/sleep/latest?kind=deep", headers=H(tc)).json()
    assert rep["summaries_rejected"][0]["reasons"][0].startswith("a date")
    # the owner accepts anyway (for example, to fix it themselves): the summary is created
    d = tc.post(f"/v1/assistant/review/{it['id']}/accept", json={}, headers=H(tc)).json()
    assert get(tc, d["summary_id"])["kind"] == "insight"
    assert all(get(tc, e["id"])["events"][0]["type"] == "summarized_into" for e in eps)


def test_commitments_never_summarized(app_client):  # M-C7, G2d
    """M-C7, G2d: commitment mirrors are never summarized: no write_summary request even goes to the LLM."""
    tc = app_client
    for i, t in enumerate(["смету по бане", "смету по забору", "смету по крыше"]):
        put(tc, f"Глеб обещал Петру Зябликову канареечную {t} до 01.10.2026.", kind="commitment_mirror",
            subject="person:zyablikov", occurred_at=f"2026-05-1{i}T10:00:00+00:00")
    llm = FakeLLM()
    DeepSleep(tc.svc, llm=llm).run()
    assert not [x for x in llm.sent if x["tool"] == "write_summary"]


def test_no_slots_for_episodes(app_client):
    """Episodes get no slot proposals: no suggest_slots request goes to the LLM and the queue is empty."""
    tc = app_client
    episodes(tc, DROZDOV, month="2026-09")
    llm = FakeLLM({"suggest_slots": {"groups": [{"attribute": "задержка", "indices": [0, 1]}]}})
    DeepSleep(tc.svc, llm=llm).run()
    assert not [x for x in llm.sent if x["tool"] == "suggest_slots"] and reviews(tc) == []


def test_deep_dry_run_creates_nothing(app_client):
    """A dry run of the deep sleep counts the summary but does not create it."""
    tc = app_client
    episodes(tc, DROZDOV)
    summary = "Игорь Дроздов опаздывал: 15.09, 17.09, 19.09, 22.09."
    r = DeepSleep(tc.svc, llm=FakeLLM({"write_summary": {"summary": summary}})).run(dry_run=True)
    assert r["stages"]["summaries"]["changed"] == 1
    assert tc.get("/v1/assistant/memories?kind=insight", headers=H(tc)).json()["items"] == []
