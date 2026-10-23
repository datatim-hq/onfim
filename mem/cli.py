"""Admin commands (08). Examples:
  python -m mem.cli init --tenant acme --person alice --name "Alice" --agent alice-cc
  python -m mem.cli create-tenant acme "ACME"
  python -m mem.cli create-principal ivanov --tenant acme --kind user --clearance 1
  python -m mem.cli create-principal ivanov-cc --tenant acme --kind agent --on-behalf-of ivanov
  python -m mem.cli create-group acme/pmo --tenant acme --name PMO --parent acme --kind org_unit
  python -m mem.cli add-member acme/pmo ivanov --role member
  python -m mem.cli create-namespace acme.pmo --tenant acme --scope group --owner-group acme/pmo --classification 1
  python -m mem.cli grant acme.pmo group acme/pmo contributor --max-class 1
  python -m mem.cli create-client --id ivanov-cc --principal ivanov-cc --ns acme.pmo --rights read,write
  python -m mem.cli check-client acme.pmo --need read,write < token_file
A failed command prints a message to stderr and exits with code 1 (an install script stops there instead of going on).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from mem import db
from mem.clock import Clock
from mem.config.settings import Settings
from mem.errors import MemError
from mem.service import MemService


class _NoEmbed:
    model_id = "n/a"
    dim = 0


def init(svc: MemService, tenant: str, person: str, name: str = "", ns: str | None = None,
         agent: str | None = None, profile: str = "work") -> dict:
    """First run: organization, person, their personal namespace and an agent with a token. Safe to repeat:
    nothing existing changes, the agent token is reissued."""
    ns = ns or f"{person}.{profile}"
    agent = agent or f"{person}-agent"
    svc.create_tenant(tenant, name or tenant)
    svc.create_principal(person, tenant, "user", clearance=3, display_name=name or person)
    svc.create_principal(agent, tenant, "agent", on_behalf_of=person, clearance=3)
    svc.create_namespace(ns, tenant, "personal", owner_principal=person, profile=profile)
    svc.grant(ns, "principal", person, "owner", 3)
    token = svc.create_client(agent, [ns], ["read", "write", "review"], principal_id=agent)
    return {"tenant": tenant, "person": person, "namespace": ns, "agent": agent, "token": token}


def main(argv: list[str] | None = None) -> int:
    from mem.db import ns_scope
    ns_scope.set("*")                          # administration from the server covers all namespaces (protection item 6)
    ap = argparse.ArgumentParser(prog="mem.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-client", help="create/reissue a token (prints the token once)")
    c.add_argument("--id", required=True)
    c.add_argument("--principal", help="principal (default: same as id)")
    c.add_argument("--ns", required=True, help="ceiling: comma-separated namespaces or *")
    c.add_argument("--rights", required=True, help="ceiling: read,write,review,forget,manage,admin")
    c.add_argument("--delegate-group", help="09: the client acts on behalf of members of this group and the group itself")
    ck = sub.add_parser("check-client", help="rights of a token (from stdin) in a namespace; exit code 1 if anything from --need is missing")
    ck.add_argument("ns"); ck.add_argument("--need", default="read")
    i = sub.add_parser("init", help="first run: organization, person, personal namespace, agent with a token")
    i.add_argument("--tenant", required=True); i.add_argument("--person", required=True); i.add_argument("--name", default="")
    i.add_argument("--ns", help="namespace (default <person>.work)"); i.add_argument("--agent")
    i.add_argument("--profile", default="work", choices=["work", "assistant", "team"])
    sub.add_parser("list-clients")
    t = sub.add_parser("create-tenant"); t.add_argument("id"); t.add_argument("name")
    p = sub.add_parser("create-principal"); p.add_argument("id"); p.add_argument("--tenant", required=True)
    p.add_argument("--kind", default="user", choices=["user", "agent", "service"])
    p.add_argument("--on-behalf-of"); p.add_argument("--clearance", type=int, default=1); p.add_argument("--name", default="")
    g = sub.add_parser("create-group"); g.add_argument("id"); g.add_argument("--tenant", required=True)
    g.add_argument("--name", required=True); g.add_argument("--parent")
    g.add_argument("--kind", default="team", choices=["org_unit", "team", "project", "role"])
    m = sub.add_parser("add-member"); m.add_argument("group"); m.add_argument("principal")
    m.add_argument("--role", default="member", choices=["member", "curator"])
    n = sub.add_parser("create-namespace"); n.add_argument("id"); n.add_argument("--tenant", required=True)
    n.add_argument("--scope", required=True, choices=["personal", "group", "tenant"])
    n.add_argument("--owner-principal"); n.add_argument("--owner-group")
    n.add_argument("--classification", type=int, default=1); n.add_argument("--profile", default="work", choices=["work", "assistant", "team"])
    n.add_argument("--description", default="")
    gr = sub.add_parser("grant"); gr.add_argument("ns"); gr.add_argument("subject_type", choices=["principal", "group", "tenant"])
    gr.add_argument("subject_id"); gr.add_argument("role", choices=["reader", "contributor", "curator", "owner"])
    gr.add_argument("--max-class", type=int, default=1); gr.add_argument("--expires", help="ISO end date")
    rv = sub.add_parser("revoke"); rv.add_argument("ns"); rv.add_argument("subject_type"); rv.add_argument("subject_id")
    ob = sub.add_parser("offboard", help="09: a member leaves: access ends, the personal layer waits for deletion")
    ob.add_argument("principal"); ob.add_argument("--purge-after-days", type=int, default=30)
    pn = sub.add_parser("purge-namespace", help="09: erase all records of a namespace (the personal layer of a leaver)")
    pn.add_argument("ns"); pn.add_argument("--yes", action="store_true")
    fs = sub.add_parser("forget-subject", help="09: forget a subject in all namespaces of the organization")
    fs.add_argument("tenant"); fs.add_argument("subject"); fs.add_argument("--confirm", type=int)
    args = ap.parse_args(argv)

    s = Settings.load()
    pool = db.make_pool(s.database_url, max_size=2)
    svc = MemService(pool, _NoEmbed(), Clock())  # type: ignore[arg-type]
    try:
        return _run(svc, pool, args)
    except MemError as e:
        print(f"mem.cli {args.cmd}: {e.message}", file=sys.stderr)
        return 1
    finally:
        pool.close()


def _run(svc: MemService, pool, a) -> int:
    if a.cmd == "check-client":
        r = svc.check_access(sys.stdin.read().strip(), a.ns)
        missing = sorted(set(a.need.split(",")) - set(r["rights"]))
        print(f"client {r['client']} (principal {r['principal']}, tenant {r['tenant']}) in {r['namespace']}: "
              f"{','.join(r['rights']) or 'no access'}; classification up to {r['max_classification']}")
        if missing:
            print(f"missing rights: {','.join(missing)}", file=sys.stderr)
            return 1
    elif a.cmd == "create-client":
        print(svc.create_client(a.id, a.ns.split(","), a.rights.split(","), principal_id=a.principal,
                                delegate_group=a.delegate_group))
    elif a.cmd == "init":
        r = init(svc, a.tenant, a.person, a.name, a.ns, a.agent, a.profile)
        print(f"namespace {r['namespace']}, agent {r['agent']} (on behalf of {r['person']})")
        print(f"token (shown once): {r['token']}")
        print(f"MCP: <base-url>/mcp/{r['namespace']}/  header  Authorization: Bearer <token>")
        print("restart the api container so MCP picks up the new namespace: docker compose restart api")
    elif a.cmd == "list-clients":
        with pool.connection() as conn:
            for r in conn.execute("SELECT id, principal_id, namespaces, rights, last_seen_at FROM clients ORDER BY id"):
                print(r["id"], r["principal_id"], ",".join(r["namespaces"]), ",".join(r["rights"]), r["last_seen_at"])
    elif a.cmd == "create-tenant":
        svc.create_tenant(a.id, a.name)
    elif a.cmd == "create-principal":
        svc.create_principal(a.id, a.tenant, a.kind, a.on_behalf_of, a.clearance, a.name)
    elif a.cmd == "create-group":
        svc.create_group(a.id, a.tenant, a.name, a.parent, a.kind)
    elif a.cmd == "add-member":
        svc.add_member(a.group, a.principal, a.role)
    elif a.cmd == "create-namespace":
        svc.create_namespace(a.id, a.tenant, a.scope, a.owner_principal, a.owner_group, a.classification,
                             a.description, a.profile)
    elif a.cmd == "grant":
        svc.grant(a.ns, a.subject_type, a.subject_id, a.role, a.max_class,
                  datetime.fromisoformat(a.expires) if a.expires else None)
    elif a.cmd == "revoke":
        svc.revoke(a.ns, a.subject_type, a.subject_id)
    elif a.cmd == "offboard":
        print(svc.offboard(a.principal, a.purge_after_days))
    elif a.cmd == "purge-namespace":
        if not a.yes:
            print("add --yes: this cannot be undone"); return 2
        print(svc.purge_namespace(a.ns))
    elif a.cmd == "forget-subject":
        print(svc.forget_subject(a.tenant, a.subject, dry_run=a.confirm is None, confirm=a.confirm))
    return 0


if __name__ == "__main__":
    sys.exit(main())
