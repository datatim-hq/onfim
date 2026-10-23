"""08: creating principals and namespaces without silently switching the organization; checking a token with check-client."""
import io

import pytest

from mem import cli
from mem.errors import BadRequest, Conflict


@pytest.fixture(scope="module")
def two(app_client):
    """Two organizations: the first has person bob with a personal namespace (through init), the second is empty."""
    svc = app_client.svc
    first = cli.init(svc, "pa-one", "pa-bob", "Bob Example")
    svc.create_tenant("pa-two", "Second")
    svc.create_principal("pa-owner2", "pa-two", "user", display_name="Owner Two")
    return svc, first


def _row(svc, table, rid):
    with svc.pool.connection() as conn:
        r = conn.execute(f"SELECT * FROM {table} WHERE id=%s", (rid,)).fetchone()
        conn.rollback()
    return r


def test_same_id_in_other_tenant_is_conflict(two):
    """08: a principal with an id that already exists in another organization is a Conflict with both organizations in the
    text, not a silent clearance update; the existing principal does not change."""
    svc, _ = two
    before = _row(svc, "principals", "pa-bob")
    with pytest.raises(Conflict) as e:
        svc.create_principal("pa-bob", "pa-two", "user", clearance=3)
    assert "pa-one" in e.value.message and "pa-two" in e.value.message
    assert _row(svc, "principals", "pa-bob") == before


def test_init_in_second_tenant_does_not_reach_first(two, app_client):
    """08: init of a second organization with the same person id fails with Conflict and issues no token;
    the token of the first install still works only in its own namespace."""
    svc, first = two
    with pytest.raises(Conflict):
        cli.init(svc, "pa-two", "pa-bob", "Someone Else", ns="pa-bob.second", agent="pa-bob-agent2")
    assert _row(svc, "namespaces", "pa-bob.second") is None
    assert _row(svc, "clients", "pa-bob-agent2") is None
    h = {"Authorization": f"Bearer {first['token']}"}
    assert app_client.get("/v1/pa-bob.work/stats", headers=h).status_code == 200


def test_other_owner_or_kind_is_conflict(two):
    """08: the same id with another on_behalf_of or another kind is a Conflict; kind and owner do not change."""
    svc, _ = two
    svc.create_principal("pa-user2", "pa-two", "user")
    svc.create_principal("pa-agent-x", "pa-two", "agent", on_behalf_of="pa-owner2")
    with pytest.raises(Conflict):
        svc.create_principal("pa-agent-x", "pa-two", "agent", on_behalf_of="pa-user2")
    with pytest.raises(Conflict):
        svc.create_principal("pa-user2", "pa-two", "service")
    assert _row(svc, "principals", "pa-agent-x")["on_behalf_of"] == "pa-owner2"
    assert _row(svc, "principals", "pa-user2")["kind"] == "user"


def test_repeat_with_same_identity_is_safe(two):
    """08: repeating with the same organization, kind and owner passes and updates clearance and name
    (re-running an install does not fail)."""
    svc, _ = two
    svc.create_principal("pa-agent-r", "pa-two", "agent", on_behalf_of="pa-owner2", clearance=1)
    svc.create_principal("pa-agent-r", "pa-two", "agent", on_behalf_of="pa-owner2", clearance=2, display_name="Agent")
    r = _row(svc, "principals", "pa-agent-r")
    assert (r["tenant_id"], r["clearance"], r["display_name"]) == ("pa-two", 2, "Agent")


def test_on_behalf_of_must_exist_in_same_tenant(two):
    """08: an agent on behalf of a person from another organization or a missing person is a BadRequest; no principal is created."""
    svc, _ = two
    with pytest.raises(BadRequest):
        svc.create_principal("pa-agent-bad", "pa-two", "agent", on_behalf_of="pa-bob")
    with pytest.raises(BadRequest):
        svc.create_principal("pa-agent-bad", "pa-two", "agent", on_behalf_of="pa-nobody")
    assert _row(svc, "principals", "pa-agent-bad") is None


def test_namespace_of_other_tenant_or_owner_is_conflict(two):
    """08: a namespace with an id that already belongs to another organization or owner is a Conflict
    (otherwise the next grant would open it to someone else); repeating with the same parameters passes."""
    svc, _ = two
    with pytest.raises(Conflict) as e:
        svc.create_namespace("pa-bob.work", "pa-two", "personal", owner_principal="pa-owner2")
    assert "pa-one" in e.value.message
    with pytest.raises(Conflict):
        svc.create_namespace("pa-bob.work", "pa-one", "personal", owner_principal="pa-bob-agent")
    assert _row(svc, "namespaces", "pa-bob.work")["owner_principal"] == "pa-bob"
    svc.create_namespace("pa-bob.work", "pa-one", "personal", owner_principal="pa-bob")


def test_cli_error_is_message_and_exit_code(two, capsys):
    """08: a failed mem.cli command prints a message to stderr with exit code 1, not a traceback; an install script stops."""
    code = cli.main(["init", "--tenant", "pa-two", "--person", "pa-bob"])
    assert code == 1
    err = capsys.readouterr().err
    assert "mem.cli init:" in err and "pa-one" in err and "Traceback" not in err


def test_check_client_reports_rights_and_exit_code(two, capsys, monkeypatch):
    """08: check-client with a token from stdin: rights in the namespace and exit code 0; a token of another organization:
    "no access" and exit code 1; an unknown token: exit code 1."""
    svc, first = two
    monkeypatch.setattr("sys.stdin", io.StringIO(first["token"] + "\n"))
    assert cli.main(["check-client", "pa-bob.work", "--need", "read,write,review"]) == 0
    assert "tenant pa-one" in capsys.readouterr().out
    svc.create_principal("pa-agent2", "pa-two", "agent", on_behalf_of="pa-owner2")
    other = svc.create_client("pa-agent2", ["pa-bob.work"], ["read", "write"], principal_id="pa-agent2")
    monkeypatch.setattr("sys.stdin", io.StringIO(other))
    assert cli.main(["check-client", "pa-bob.work", "--need", "read"]) == 1
    out = capsys.readouterr()
    assert "no access" in out.out and "read" in out.err
    monkeypatch.setattr("sys.stdin", io.StringIO("not-a-token"))
    assert cli.main(["check-client", "pa-bob.work"]) == 1


def test_forget_by_source_ref(app_client):
    """05, S-3: forget everything taken from one source fragment by the evidence `source_ref` (including records where
    it is only one of several evidence items); other records stay; dry run first, then confirm with the exact count;
    a list instead of a string is 400. Review items that mention a purged record go with it."""
    tc = app_client
    H = {"Authorization": f"Bearer {tc.tokens['assistant']}"}

    def put(text, refs):
        ev = [{"source_ref": r, "source_scope": "chat:500", "observed_at": "2026-10-12T10:00:00+00:00", "speaker": "Ivan"}
              for r in refs]
        r = tc.post("/v1/assistant/memories", json={"kind": "person_fact", "text": text, "subject": "person:tg500",
                                                   "evidence": ev}, headers=H)
        assert r.status_code == 201, r.text
        return r.json()["id"]
    leak = put("A fact from the leaked message", ["tg:500:7"])
    both = put("A fact from two messages", ["tg:500:6", "tg:500:7"])
    keep = put("Another fact", ["tg:500:8"])
    body = {"source_ref": "tg:500:7", "mode": "purge"}
    dry = tc.post("/v1/assistant/forget", json=body, headers=H).json()
    assert dry["matched"] == 2 and dry["dry_run"]
    assert tc.post("/v1/assistant/forget", json={**body, "dry_run": False, "confirm": 1}, headers=H).status_code == 400
    done = tc.post("/v1/assistant/forget", json={**body, "dry_run": False, "confirm": 2}, headers=H).json()
    assert done["applied"] == 2
    assert tc.get(f"/v1/assistant/memories/{leak}", headers=H).status_code == 404
    assert tc.get(f"/v1/assistant/memories/{both}", headers=H).status_code == 404
    assert tc.get(f"/v1/assistant/memories/{keep}", headers=H).status_code == 200
    assert tc.post("/v1/assistant/forget", json={"source_ref": ["x"], "mode": "purge"}, headers=H).status_code == 400
    # a review item that mentions a purged record (and holds its quotes) is deleted with it
    other = put("One more fact from the leaked message", ["tg:500:9"])
    with tc.svc.pool.connection() as conn:
        conn.execute("INSERT INTO review_items(namespace, created_at, type, memory_ids, evidence_quotes) VALUES "
                     "('assistant', now(), 'contradiction', %s::uuid[], '[\"quote\"]')", ([other, keep],))
        conn.commit()
    dry = tc.post("/v1/assistant/forget", json={"source_ref": "tg:500:9", "mode": "purge"}, headers=H).json()
    tc.post("/v1/assistant/forget", json={"source_ref": "tg:500:9", "mode": "purge", "dry_run": False,
                                          "confirm": dry["matched"]}, headers=H)
    with tc.svc.pool.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM review_items WHERE %s::uuid = ANY(memory_ids)",
                            (other,)).fetchone()["n"] == 0
        conn.rollback()
