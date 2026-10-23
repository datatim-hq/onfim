"""Protection item 6 (AC-6): the database itself isolates namespaces; even a query bypassing the service checks sees nothing else."""
from mem.db import scope

NOW = "2026-09-24T10:00:00+00:00"


def H(tc, who):
    return {"Authorization": f"Bearer {tc.tokens[who]}"}


def put(tc, ns, who, text):
    body = {"text": text, "kind": "note" if ns == "work" else "topic_fact",
            "evidence": [{"source_ref": "x:1", "source_scope": "dev", "speaker": "agent", "observed_at": NOW}]}
    r = tc.post(f"/v1/{ns}/memories", json=body, headers=H(tc, who))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def count(tc, sql="SELECT namespace, count(*) n FROM memories GROUP BY 1 ORDER BY 1"):
    with tc.svc.pool.connection() as conn:
        rows = conn.execute(sql).fetchall()
        conn.rollback()
    return {r["namespace"]: r["n"] for r in rows}


def test_db_level_isolation(app_client):
    """AC-6: the database itself limits rows to the namespaces in the request scope; without a scope nothing is visible.
    Changing a record of another namespace or planting a copy there is impossible even with direct SQL."""
    tc = app_client
    put(tc, "work", "cowork", "рабочая заметка")
    put(tc, "assistant", "assistant", "факт агента")
    with scope("*"):
        assert count(tc) == {"assistant": 1, "work": 1}
    with scope("assistant"):                                      # even a "raw" query sees only its own namespace
        assert count(tc) == {"assistant": 1}
        assert count(tc, "SELECT m.namespace, count(*) n FROM evidence e JOIN memories m ON m.id=e.memory_id GROUP BY 1") \
            == {"assistant": 1}
    with scope(""):                                            # no scope set -> no rows
        assert count(tc) == {}
    with scope("assistant"):                                      # the database refuses a write into another namespace
        import psycopg
        with tc.svc.pool.connection() as conn:
            try:
                conn.execute("UPDATE memories SET text='взлом' WHERE namespace='work'")
                changed = conn.execute("SELECT count(*) n FROM memories WHERE text='взлом'").fetchone()["n"]
            finally:
                conn.rollback()
        assert changed == 0
        with tc.svc.pool.connection() as conn:           # a copy of an own record into another namespace: refused by the policy
            try:
                cols = ", ".join(r["column_name"] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='memories' "
                    "AND is_generated='NEVER' ORDER BY ordinal_position").fetchall())
                conn.execute(f"CREATE TEMP TABLE t AS SELECT {cols} FROM memories LIMIT 1")
                conn.execute("UPDATE t SET id=gen_random_uuid(), namespace='work', text='подброшено'")
                conn.execute(f"INSERT INTO memories ({cols}) SELECT {cols} FROM t")
                inserted = True
            except psycopg.errors.InsufficientPrivilege:
                inserted = False
            finally:
                conn.rollback()
        assert inserted is False
    with scope("*"):
        assert count(tc, "SELECT namespace, count(*) n FROM memories WHERE text IN ('взлом','подброшено') GROUP BY 1") == {}


def test_api_scope_follows_token(app_client):
    """AC-6: the assistant token gets no records of namespace work, neither by list nor by search."""
    tc = app_client
    put(tc, "work", "cowork", "секрет рабочего пространства")
    r = tc.get("/v1/work/memories", headers=H(tc, "assistant"))
    assert r.status_code in (403, 404)                         # the service's rights check
    r = tc.post("/v1/assistant/recall", json={"query": "секрет рабочего пространства"}, headers=H(tc, "assistant"))
    assert all("секрет" not in i["text"] for i in r.json()["items"])
