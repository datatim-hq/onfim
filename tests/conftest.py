import os

import pytest

INTEGRATION = bool(os.environ.get("MEM_DATABASE_URL")) and os.environ.get("MEM_EMBED_MODEL") == "fake"

TOKENS = {
    "admin": ("*", "read,write,review,forget,admin"),
    "cowork": ("work", "read,write,review"),
    "claude-code": ("work", "read,write"),
    "assistant": ("assistant", "read,write,review,forget"),
    "forgetter": ("work", "read,write,forget"),
}


def _seed_demo(svc):
    """Organization demo, person alice, their agents and namespaces work/assistant: migrations do not create them."""
    svc.ensure_service_admin()
    svc.create_tenant("demo", "Demo")
    svc.create_principal("alice", "demo", "user", clearance=3, display_name="Alice Example")
    for agent in ("cowork", "claude-code", "assistant", "forgetter"):
        svc.create_principal(agent, "demo", "agent", on_behalf_of="alice", clearance=3)
    for ns, profile in (("work", "work"), ("assistant", "assistant")):
        svc.create_namespace(ns, "demo", "personal", owner_principal="alice", profile=profile)
        svc.grant(ns, "principal", "alice", "owner", 3)
    svc.pool.close()


@pytest.fixture(scope="session")
def app_client():
    if not INTEGRATION:
        pytest.skip("integration tests run only on a stand (docker-compose.test.yml)")
    from fastapi.testclient import TestClient

    from mem.main import create_app

    _seed_demo(create_app().state.svc)   # migrations and seed rows; MCP sees namespaces created before startup
    app = create_app()
    svc = app.state.svc
    tokens = {name: svc.create_client(name, ns.split(","), rights.split(",")) for name, (ns, rights) in TOKENS.items()}
    with TestClient(app) as tc:
        tc.tokens = tokens
        tc.svc = svc
        yield tc


@pytest.fixture(autouse=True)
def _clean(request):
    if "app_client" not in request.fixturenames:
        yield
        return
    tc = request.getfixturevalue("app_client")
    import psycopg
    from mem.db import ns_scope
    with psycopg.connect(os.environ["MEM_DATABASE_URL"], autocommit=True) as conn:   # owner: TRUNCATE bypasses RLS
        conn.execute("TRUNCATE memories, evidence, memory_events, audit_log, review_items, jobs CASCADE")
    ns_scope.set("*")                   # direct test queries to the database cover all namespaces; the API uses its own scope
    tc.svc.clock.set_offset(0)
    yield
