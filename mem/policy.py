"""Access decision (spec 08). resolve() is a pure function over facts already loaded;
load_facts() is one database query. Records are filtered by classification in SQL by the service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

ROLE_RANK = {"reader": 1, "contributor": 2, "curator": 3, "owner": 4}
ROLE_RIGHTS = {
    "reader": {"read"},
    "contributor": {"read", "write"},
    "curator": {"read", "write", "review", "forget"},
    "owner": {"read", "write", "review", "forget", "manage"},
}
NO_ACCESS: tuple[frozenset, int] = (frozenset(), -1)


@dataclass(frozen=True)
class Grant:
    subject_type: str          # principal | group | tenant
    subject_id: str
    role: str
    max_classification: int
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Facts:
    ns_exists: bool
    ns_tenant: str | None
    subject_tenant: str        # organization of the one we act for
    subject_id: str            # the person (for an agent, on_behalf_of)
    subject_clearance: int
    subject_active: bool
    subject_groups: frozenset[str]   # groups including nesting (all ancestors)
    grants: tuple[Grant, ...]


def resolve(f: Facts, now: datetime, ceiling_ns: list[str], ceiling_rights: set[str],
            ns: str, local_admin: bool) -> tuple[frozenset, int]:
    """-> (effective rights, maximum classification level) or NO_ACCESS."""
    if local_admin:   # operations from localhost: everything, in all organizations
        return frozenset(ceiling_rights | {"read", "write", "review", "forget", "manage", "admin"}), 3
    if not f.ns_exists or f.ns_tenant != f.subject_tenant or not f.subject_active:
        return NO_ACCESS
    if not (ns in ceiling_ns or "*" in ceiling_ns):
        return NO_ACCESS
    best_role, best_class = None, -1
    for g in f.grants:
        if g.expires_at is not None and g.expires_at <= now:
            continue
        hit = ((g.subject_type == "principal" and g.subject_id == f.subject_id)
               or (g.subject_type == "group" and g.subject_id in f.subject_groups)
               or (g.subject_type == "tenant" and g.subject_id == f.subject_tenant))
        if not hit:
            continue
        if best_role is None or ROLE_RANK[g.role] > ROLE_RANK[best_role]:
            best_role = g.role
        best_class = max(best_class, g.max_classification)
    if best_role is None:
        return NO_ACCESS
    rights = frozenset(ROLE_RIGHTS[best_role] & ceiling_rights)
    if not rights:
        return NO_ACCESS
    return rights, min(best_class, f.subject_clearance)


FACTS_SQL = """
WITH RECURSIVE direct AS (
  SELECT gm.group_id FROM group_members gm WHERE gm.principal_id = %(subject)s
  UNION
  SELECT %(group)s::text WHERE %(group)s::text IS NOT NULL      -- 09, TM-4: acting on behalf of the group itself
), up AS (
  SELECT g.id, g.parent_id FROM groups g JOIN direct d ON d.group_id = g.id
  UNION
  SELECT p.id, p.parent_id FROM groups p JOIN up ON up.parent_id = p.id
)
SELECT
  (SELECT tenant_id FROM namespaces WHERE id = %(ns)s) AS ns_tenant,
  EXISTS (SELECT 1 FROM namespaces WHERE id = %(ns)s) AS ns_exists,
  (SELECT coalesce(array_agg(id), '{}') FROM up) AS groups,
  (SELECT coalesce(json_agg(json_build_object('t', subject_type, 'id', subject_id, 'role', role,
          'mc', max_classification, 'exp', expires_at)), '[]'::json)
     FROM namespace_grants WHERE namespace = %(ns)s) AS grants
"""


def load_facts(conn, ns: str, subject: dict) -> Facts:
    r = conn.execute(FACTS_SQL, {"ns": ns, "subject": subject["id"], "group": subject.get("group")}).fetchone()
    grants = tuple(
        Grant(g["t"], g["id"], g["role"], int(g["mc"]),
              datetime.fromisoformat(g["exp"]) if g["exp"] else None)
        for g in r["grants"])
    return Facts(bool(r["ns_exists"]), r["ns_tenant"], subject["tenant_id"], subject["id"],
                 int(subject["clearance"]), bool(subject["active"]), frozenset(r["groups"] or []), grants)
