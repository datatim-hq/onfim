"""Service layer of mem (A-1): all business logic lives here; REST and MCP are thin adapters.

Every state transition writes audit_log in the same transaction (L-1). audit_log and logs never contain
record texts or quotes (S-5, D-8).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from mem import lifecycle as LC
from mem import policy
from mem import safety
from mem import search as S
from mem.clock import Clock
from mem.config import lifecycle as C
from mem.embedder import SafeEmbedder
from mem.errors import BadRequest, Conflict, Forbidden, NotFound, Unauthorized

log = logging.getLogger("mem.service")

RIGHTS = {"read", "write", "review", "forget", "manage", "admin"}
VISIBILITY = {"owner_only", "same_chat", "shareable"}
STATES = {"current", "candidate", "superseded", "archived"}
SIGNALS = {"used": C.W_USED, "helpful": C.W_HELPFUL}

MEM_COLUMNS = (
    "m.id, m.namespace, m.kind, m.text, m.subject, m.slot, m.tags, m.importance, m.source, m.visibility, "
    "m.occurred_at, m.created_at, m.state, m.valid_from, m.valid_to, m.supersedes_id, m.duplicate_of, "
    "m.pinned, m.archived_at, m.archived_by, m.prev_state, m.strength, m.strength_at, m.evidence_count, "
    "m.last_reinforced_at, m.external_ref, m.external_closed_at, m.classification, m.promoted_from, "
    "m.trust, m.flags, "
    "k.half_life_days, k.rank_weight, k.forgettable"
)
EFF = LC.EFFECTIVE_STRENGTH_SQL


# ---------------------------------------------------------------- clients and rights

@dataclass
class Client:
    """Token + principal. The token's namespaces/rights are a ceiling (08); access is computed by policy.resolve."""
    id: str
    namespaces: list[str]
    rights: list[str]
    is_local: bool = False       # the request came directly to 127.0.0.1, not through nginx
    principal_id: str = ""
    on_behalf_of: str | None = None
    subject: dict = field(default_factory=dict)   # person/service/group whose grants count
    delegated: bool = False      # 09: a team agent service acting on behalf of a member or a group
    access: dict = field(default_factory=dict)    # cache ns -> (rights, max_classification)

    def __str__(self) -> str:
        return f"client:{self.id}"

    @property
    def local_admin(self) -> bool:
        return "admin" in self.rights and self.is_local

    def can(self, ns: str | None, right: str) -> bool:
        if right == "admin":
            return self.local_admin
        if ns is None:
            return right in self.rights
        rights, _ = self.access.get(ns, policy.NO_ACCESS)
        return right in rights


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- utilities

def _dt(v: Any, name: str) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        d = v
    else:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError as e:
            raise BadRequest(f"field {name}: invalid date format, ISO 8601 expected") from e
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _uuid(v: Any) -> uuid.UUID:
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except ValueError as e:
        raise NotFound("record not found") from e


def _row_to_memory(r: dict, eff: float | None = None) -> dict:
    return {
        "id": str(r["id"]),
        "namespace": r["namespace"],
        "kind": r["kind"],
        "text": r["text"],
        "subject": r["subject"],
        "slot": r["slot"],
        "tags": list(r["tags"] or []),
        "importance": r["importance"],
        "source": r["source"],
        "visibility": r["visibility"],
        "occurred_at": _iso(r["occurred_at"]),
        "created_at": _iso(r["created_at"]),
        "state": r["state"],
        "valid_from": _iso(r["valid_from"]),
        "valid_to": _iso(r["valid_to"]),
        "supersedes_id": str(r["supersedes_id"]) if r["supersedes_id"] else None,
        "duplicate_of": str(r["duplicate_of"]) if r["duplicate_of"] else None,
        "pinned": r["pinned"],
        "archived_at": _iso(r["archived_at"]),
        "archived_by": r["archived_by"],
        "strength": round(float(eff if eff is not None else r["strength"]), 4),
        "evidence_count": r["evidence_count"],
        "last_reinforced_at": _iso(r["last_reinforced_at"]),
        "external_ref": r["external_ref"],
        "external_closed_at": _iso(r["external_closed_at"]),
        "classification": r["classification"],
        "promoted_from": str(r["promoted_from"]) if r["promoted_from"] else None,
        "trust": r.get("trust", "trusted"),                 # 1.6.1: the client shows untrusted records as data
        "flags": list(r.get("flags") or []),                # 1.6.2: what was cut out/flagged on write
    }


def _evidence_to_dict(e: dict) -> dict:
    return {
        "source_ref": e["source_ref"], "source_scope": e["source_scope"], "speaker": e["speaker"],
        "quote": e["quote"], "observed_at": _iso(e["observed_at"]), "independent_key": e["independent_key"],
        "speaker_principal": e.get("speaker_principal"),
    }


# A subject and all its aliases in the namespace's organization (09, TM-31). Parameters: %(subject)s, %(ns)s.
_CANON_SQL = ("SELECT %(subject)s UNION SELECT a.canonical FROM subject_aliases a JOIN namespaces n "
              "ON n.tenant_id = a.tenant_id WHERE n.id = %(ns)s AND a.alias = %(subject)s")
SUBJECT_EQ_SQL = (f"{_CANON_SQL} UNION SELECT a2.alias FROM subject_aliases a2 JOIN namespaces n2 "
                  f"ON n2.tenant_id = a2.tenant_id WHERE n2.id = %(ns)s AND a2.canonical IN ({_CANON_SQL})")


@dataclass
class EvidenceIn:
    source_ref: str
    source_scope: str
    observed_at: datetime
    speaker: str | None = None
    quote: str | None = None
    independent_key: str = ""
    speaker_principal: str | None = None     # 09, TM-7: the speaker is a member of the organization

    @staticmethod
    def parse(d: Any) -> "EvidenceIn":
        if not isinstance(d, dict):
            raise BadRequest("evidence: object expected")
        ref, scope = d.get("source_ref"), d.get("source_scope")
        if not ref or not scope:
            raise BadRequest("evidence: source_ref and source_scope are required")
        obs = _dt(d.get("observed_at"), "evidence.observed_at")
        if obs is None:
            raise BadRequest("evidence: observed_at is required")
        quote = d.get("quote")
        if quote is not None:
            if not isinstance(quote, str):
                raise BadRequest("evidence.quote: string expected")
            secret = safety.find_secret(quote)
            if secret:
                raise BadRequest(f"the quote looks like a {secret}; keys and tokens are never stored in memory (1.6.3)")
            quote, _ = safety.sanitize(quote)
        if quote is not None and len(quote) > C.QUOTE_MAX:
            raise BadRequest(f"evidence.quote is longer than {C.QUOTE_MAX} characters")
        speaker = d.get("speaker") or None
        fwd = d.get("forwarded_from") or None          # 1.6.4: for a forward, the source is the original author, not the forwarder
        sp = d.get("speaker_principal") or None
        if sp is not None and not isinstance(sp, str):
            raise BadRequest("evidence.speaker_principal: string expected")
        # 09, TM-8: a member is one source whichever chat they said it in; a forward counts by its original author
        key = d.get("independent_key") or (f"fwd:{fwd}" if fwd else f"p:{sp}" if sp else f"{speaker or 'unknown'}@{scope}")
        return EvidenceIn(str(ref), str(scope), obs, speaker, quote, str(key), sp)


# ---------------------------------------------------------------- service

def chat_of(scope: str | None) -> str:
    """Chat of the evidence scope ("chat:111" -> "111"; like split_part(scope, ':', 2) in SQL); empty without a chat."""
    parts = (scope or "").split(":")
    return parts[1] if len(parts) > 1 else ""


def _same_claim(a: str | None, b: str | None) -> bool:
    """The same claim: texts equal ignoring case, yo/ye, extra spaces and a trailing period."""
    def n(t):
        t = re.sub(r"\s+", " ", (t or "").strip().lower().replace("ё", "е"))
        return re.sub(r"[\s.!…]+$", "", t)
    return bool(n(a)) and n(a) == n(b)


class MemService:
    def __init__(self, pool: ConnectionPool, embedder: SafeEmbedder, clock: Clock):
        self.pool = pool
        self.emb = embedder
        self.clock = clock

    # ---------- rights

    def _access(self, client: Client, ns: str) -> tuple[frozenset, int]:
        if ns not in client.access:
            with self.pool.connection() as conn:
                facts = policy.load_facts(conn, ns, client.subject)
                conn.rollback()
            client.access[ns] = policy.resolve(facts, self.clock.now(), client.namespaces, set(client.rights),
                                               ns, client.local_admin)
        return client.access[ns]

    def _set_scope(self, client: Client) -> None:
        """Protection item 6: the namespace scope of this request's connections is only the namespaces the client can reach."""
        from mem.db import ns_scope
        if "*" in (client.namespaces or []) and not client.delegated:
            ns_scope.set("*")
            return
        with self.pool.connection() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM namespaces").fetchall()]
            conn.rollback()
        ns_scope.set(",".join(sorted(n for n in ids if self._access(client, n)[0])))

    def _require(self, client: Client, ns: str | None, right: str) -> int:
        """Checks a right (08). Returns the maximum classification level available in ns."""
        self._set_scope(client)
        if ns is None:
            if not client.can(None, right):
                raise Forbidden(f"client {client.id} has no {right} right")
            return 3
        rights, maxc = self._access(client, ns)
        if not rights:
            raise Forbidden(f"no access to namespace {ns}")
        if right not in rights:
            raise Forbidden(f"client {client.id} has no {right} right in namespace {ns}")
        return maxc

    def _ns_exists(self, cur, ns: str) -> None:
        if not cur.execute("SELECT 1 FROM namespaces WHERE id=%s", (ns,)).fetchone():
            raise NotFound(f"namespace {ns} does not exist")

    # ---------- clients

    def authenticate(self, token: str | None, is_local: bool, on_behalf: str | None = None) -> Client:
        if not token:
            raise Unauthorized("Authorization: Bearer <token> header required")
        with self.pool.connection() as conn:
            r = conn.execute(
                "SELECT c.id, c.namespaces, c.rights, c.last_seen_at, c.delegate_group, "
                "p.id AS pid, p.on_behalf_of, p.active AS pactive, "
                "s.id AS sid, s.tenant_id AS stenant, s.clearance AS sclear, s.active AS sactive "
                "FROM clients c JOIN principals p ON p.id = c.principal_id "
                "JOIN principals s ON s.id = coalesce(p.on_behalf_of, p.id) WHERE c.token_hash=%s",
                (hash_token(token),)).fetchone()
            if not r or not r["pactive"]:
                raise Unauthorized("unknown token")
            now = datetime.now(timezone.utc)
            if r["last_seen_at"] is None or now - r["last_seen_at"] > timedelta(minutes=5):
                conn.execute("UPDATE clients SET last_seen_at=%s WHERE id=%s", (now, r["id"]))
                conn.commit()
            if r["delegate_group"]:                       # 09, TM-1...TM-5
                if not on_behalf:
                    raise BadRequest("delegated client: the X-Mem-On-Behalf-Of header is required "
                                     "(<principal> or group:<group>)")
                subject = self._delegated_subject(conn, r["delegate_group"], on_behalf.strip())
                conn.rollback()
                return Client(r["id"], list(r["namespaces"]), list(r["rights"]), is_local, r["pid"], subject["id"],
                              subject, delegated=True)
            if on_behalf:
                raise BadRequest("not a delegated client: the X-Mem-On-Behalf-Of header is not accepted")
        subject = {"id": r["sid"], "tenant_id": r["stenant"], "clearance": r["sclear"], "active": r["sactive"]}
        return Client(r["id"], list(r["namespaces"]), list(r["rights"]), is_local, r["pid"], r["on_behalf_of"], subject)

    _ANCESTORS_SQL = ("WITH RECURSIVE up AS (SELECT id, parent_id FROM groups WHERE id = ANY(%s) "
                      "UNION SELECT g.id, g.parent_id FROM groups g JOIN up ON up.parent_id = g.id) SELECT id FROM up")

    def _delegated_subject(self, conn, delegate_group: str, on_behalf: str) -> dict:
        """Whom a delegated client acts for: a member of the group (TM-2) or the group itself (TM-4).
        Every refusal is the same 403, so as not to reveal who exists."""
        deny = Forbidden("cannot act on behalf of this principal")
        g = conn.execute("SELECT tenant_id FROM groups WHERE id=%s", (delegate_group,)).fetchone()
        if not g:
            raise deny
        if on_behalf.startswith("group:"):
            gid = on_behalf[6:]
            anc = {x["id"] for x in conn.execute(self._ANCESTORS_SQL, ([gid],)).fetchall()}
            if delegate_group not in anc:                 # the delegation group itself or its child
                raise deny
            return {"id": on_behalf, "tenant_id": g["tenant_id"], "clearance": 3, "active": True, "group": gid}
        p = conn.execute("SELECT id, tenant_id, clearance, active, kind FROM principals WHERE id=%s", (on_behalf,)).fetchone()
        if not p or not p["active"] or p["kind"] != "user" or p["tenant_id"] != g["tenant_id"]:
            raise deny
        mine = [x["group_id"] for x in conn.execute("SELECT group_id FROM group_members WHERE principal_id=%s",
                                                    (on_behalf,)).fetchall()]
        anc = {x["id"] for x in conn.execute(self._ANCESTORS_SQL, (mine,)).fetchall()} if mine else set()
        if delegate_group not in anc:
            raise deny
        return {"id": p["id"], "tenant_id": p["tenant_id"], "clearance": p["clearance"], "active": True}

    def create_client(self, client_id: str, namespaces: list[str], rights: list[str],
                      token: str | None = None, principal_id: str | None = None,
                      delegate_group: str | None = None) -> str:
        """Client token. The token's rights and namespaces are a ceiling; access comes from the principal's grant (08)."""
        bad = set(rights) - RIGHTS
        if bad:
            raise BadRequest(f"unknown rights: {sorted(bad)}")
        principal_id = principal_id or client_id
        token = token or secrets.token_urlsafe(32)
        with self.pool.connection() as conn:
            if not conn.execute("SELECT 1 FROM principals WHERE id=%s", (principal_id,)).fetchone():
                raise BadRequest(f"principal {principal_id} does not exist (mem.cli create-principal)")
            if delegate_group and not conn.execute("SELECT 1 FROM groups WHERE id=%s", (delegate_group,)).fetchone():
                raise BadRequest(f"group {delegate_group} does not exist")
            conn.execute(
                "INSERT INTO clients(id, token_hash, namespaces, rights, principal_id, delegate_group) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET token_hash=EXCLUDED.token_hash, namespaces=EXCLUDED.namespaces, "
                "rights=EXCLUDED.rights, principal_id=EXCLUDED.principal_id, delegate_group=EXCLUDED.delegate_group",
                (client_id, hash_token(token), namespaces, rights, principal_id, delegate_group))
            conn.commit()
        return token

    def check_access(self, token: str, ns: str) -> dict:
        """What a token can do in a namespace, as a request from outside (not localhost) would see it. For installers: check
        an issued token before starting the agent instead of finding a 403 in its log."""
        c = self.authenticate(token, is_local=False)
        rights, maxc = self._access(c, ns)
        return {"client": c.id, "principal": c.principal_id, "on_behalf_of": c.on_behalf_of,
                "tenant": c.subject.get("tenant_id"), "namespace": ns, "rights": sorted(rights),
                "max_classification": maxc}

    # ---------- organization administration (08); called from the CLI and tests, admin right

    def _admin_exec(self, action: str, sql: str, params: tuple, ns: str | None = None, detail: dict | None = None):
        with self.pool.connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            self._audit(cur, "admin", action, ns, detail=detail or {})
            conn.commit()

    def ensure_service_admin(self) -> None:
        """Service principal admin (organization system) for the MEM_ADMIN_TOKEN token; migrations do not create it."""
        with self.pool.connection() as conn:
            if conn.execute("SELECT 1 FROM principals WHERE id='admin'").fetchone():
                return
        self.create_tenant("system", "System")
        self.create_principal("admin", "system", "service", clearance=3, display_name="Operations (localhost)")

    def create_tenant(self, tenant_id: str, name: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO tenants(id, name) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING", (tenant_id, name))
            conn.commit()

    def create_principal(self, pid: str, tenant: str, kind: str = "user", on_behalf_of: str | None = None,
                         clearance: int = 1, display_name: str = "") -> None:
        if kind not in ("user", "agent", "service"):
            raise BadRequest("kind: user | agent | service")
        if pid.startswith("group:"):
            raise BadRequest("a principal id cannot start with \"group:\"")
        # Repeating with the same organization, kind and on_behalf_of is safe (updates clearance and name). The same id
        # with another organization or owner is an error: a silent update would leave the principal in the old
        # organization, and a token issued to the "new" one would work with its namespaces.
        with self.pool.connection() as conn:
            cur = conn.cursor()
            if on_behalf_of:
                boss = cur.execute("SELECT tenant_id FROM principals WHERE id=%s", (on_behalf_of,)).fetchone()
                if not boss:
                    raise BadRequest(f"principal {on_behalf_of} (on_behalf_of) does not exist")
                if boss["tenant_id"] != tenant:
                    raise BadRequest(f"the agent and {on_behalf_of} are in different organizations ({tenant} and {boss['tenant_id']})")
            done = cur.execute(
                "INSERT INTO principals(id, tenant_id, kind, display_name, on_behalf_of, clearance) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET clearance=EXCLUDED.clearance, "
                "display_name=EXCLUDED.display_name WHERE principals.tenant_id=EXCLUDED.tenant_id "
                "AND principals.kind=EXCLUDED.kind AND principals.on_behalf_of IS NOT DISTINCT FROM EXCLUDED.on_behalf_of "
                "RETURNING id", (pid, tenant, kind, display_name or pid, on_behalf_of, clearance)).fetchone()
            if not done:
                old = cur.execute("SELECT tenant_id, kind, on_behalf_of FROM principals WHERE id=%s", (pid,)).fetchone()
                conn.rollback()
                raise Conflict(f"principal {pid} already exists: organization {old['tenant_id']}, kind {old['kind']}, "
                               f"on behalf of {old['on_behalf_of'] or '-'}; requested: {tenant}, {kind}, "
                               f"{on_behalf_of or '-'}. Choose another id.")
            self._audit(cur, "admin", "principal_create", None,
                        detail={"principal": pid, "kind": kind, "tenant": tenant, "clearance": clearance})
            conn.commit()

    def create_group(self, gid: str, tenant: str, name: str, parent: str | None = None, kind: str = "team") -> None:
        self._admin_exec("group_add", "INSERT INTO groups(id, tenant_id, parent_id, name, kind) VALUES (%s,%s,%s,%s,%s) "
                         "ON CONFLICT (id) DO NOTHING", (gid, tenant, parent, name, kind), detail={"group": gid})

    def add_member(self, gid: str, pid: str, role: str = "member") -> None:
        self._admin_exec("group_add", "INSERT INTO group_members(group_id, principal_id, role) VALUES (%s,%s,%s) "
                         "ON CONFLICT (group_id, principal_id) DO UPDATE SET role=EXCLUDED.role",
                         (gid, pid, role), detail={"group": gid, "principal": pid, "role": role})

    def create_namespace(self, ns: str, tenant: str, scope: str, owner_principal: str | None = None,
                         owner_group: str | None = None, default_classification: int = 1, description: str = "",
                         profile: str = "work") -> None:
        """A new namespace becomes available over MCP after the service restarts."""
        import re as _re
        if not _re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", ns):
            raise BadRequest("namespace id: lowercase Latin letters, digits, '.', '_', '-' (no '/': it is part of the URL)")
        # Repeating with the same organization, scope and owner changes nothing. The same id with another organization
        # or owner is an error: otherwise the next grant would open someone else's namespace to the new owner.
        with self.pool.connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO namespaces(id, description, tenant_id, scope, owner_principal, owner_group, "
                        "default_classification, profile) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING "
                        "RETURNING id",
                        (ns, description, tenant, scope, owner_principal, owner_group, default_classification, profile))
            if not cur.fetchone():
                old = cur.execute("SELECT tenant_id, scope, owner_principal, owner_group FROM namespaces WHERE id=%s",
                                  (ns,)).fetchone()
                conn.rollback()
                if (old["tenant_id"], old["scope"], old["owner_principal"], old["owner_group"]) != \
                        (tenant, scope, owner_principal, owner_group):
                    raise Conflict(f"namespace {ns} already exists: organization {old['tenant_id']}, scope {old['scope']}, "
                                   f"owner {old['owner_principal'] or old['owner_group'] or '-'}. Choose another id.")
                return
            self._audit(cur, "admin", "namespace_create", ns, detail={"scope": scope})
            conn.commit()

    def grant(self, ns: str, subject_type: str, subject_id: str, role: str, max_classification: int = 1,
              expires_at: datetime | None = None, by: str = "admin") -> None:
        if role not in policy.ROLE_RANK:
            raise BadRequest(f"role: {sorted(policy.ROLE_RANK)}")
        self._admin_exec("grant",
                         "INSERT INTO namespace_grants(namespace, subject_type, subject_id, role, max_classification, "
                         "granted_by, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (namespace, subject_type, "
                         "subject_id) DO UPDATE SET role=EXCLUDED.role, max_classification=EXCLUDED.max_classification, "
                         "expires_at=EXCLUDED.expires_at, granted_by=EXCLUDED.granted_by",
                         (ns, subject_type, subject_id, role, max_classification, by, expires_at), ns=ns,
                         detail={"subject": f"{subject_type}:{subject_id}", "role": role, "max_classification": max_classification})

    def revoke(self, ns: str, subject_type: str, subject_id: str) -> None:
        self._admin_exec("revoke", "DELETE FROM namespace_grants WHERE namespace=%s AND subject_type=%s AND subject_id=%s",
                         (ns, subject_type, subject_id), ns=ns, detail={"subject": f"{subject_type}:{subject_id}"})

    # ---------- internal

    def _audit(self, cur, actor, action: str, ns: str | None, memory_id=None, rule_id=None,
               detail: dict | None = None) -> None:
        pid = getattr(actor, "principal_id", None)
        obo = getattr(actor, "on_behalf_of", None)
        cur.execute(
            "INSERT INTO audit_log(at, actor, action, namespace, memory_id, rule_id, detail, principal_id, on_behalf_of) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (self.clock.now(), str(actor), action, ns, memory_id, rule_id, Jsonb(detail or {}), pid, obo))

    def _log_access(self, client: Client, ns: str, rows: list) -> None:
        """AC-2: reading records of level >= 2 goes to access_log (ids only)."""
        ids = [r["id"] for r in rows if r["classification"] >= 2]
        if not ids:
            return
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO access_log(at, principal_id, on_behalf_of, namespace, memory_ids) "
                         "VALUES (%s,%s,%s,%s,%s)", (self.clock.now(), client.principal_id, client.on_behalf_of, ns, ids))
            conn.commit()

    def _load(self, cur, ns: str, mid, lock: bool = False, maxc: int = 3) -> dict:
        """A record above the clearance (classification > maxc) does not exist for the caller -> 404 (08)."""
        now = self.clock.now()
        q = (f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
             "WHERE m.id=%(id)s AND m.namespace=%(ns)s AND m.classification <= %(maxc)s"
             + (" FOR UPDATE OF m" if lock else ""))
        r = cur.execute(q, {"id": _uuid(mid), "ns": ns, "now": now, "maxc": maxc}).fetchone()
        if not r:
            raise NotFound("record not found")
        return r

    def _event(self, cur, mid, type_: str, delta: float, actor: str) -> None:
        cur.execute("INSERT INTO memory_events(memory_id, at, type, delta, actor) VALUES (%s,%s,%s,%s,%s)",
                    (mid, self.clock.now(), type_, delta, str(actor)))

    def _set_strength(self, cur, mid, s: float, reinforced: bool) -> None:
        now = self.clock.now()
        if reinforced:
            cur.execute("UPDATE memories SET strength=%s, strength_at=%s, last_reinforced_at=%s WHERE id=%s",
                        (s, now, now, mid))
        else:
            cur.execute("UPDATE memories SET strength=%s, strength_at=%s WHERE id=%s", (s, now, mid))

    def _insert_evidence(self, cur, mid, ev: EvidenceIn, created_at=None) -> None:
        """created_at is when the evidence arrived; when duplicates merge at night, the original time (otherwise moved
        evidence would look fresh and block gate G1 for other candidates of the slot)."""
        cur.execute(
            "INSERT INTO evidence(memory_id, source_ref, source_scope, speaker, quote, observed_at, "
            "independent_key, created_at, speaker_principal) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (mid, ev.source_ref, ev.source_scope, ev.speaker, ev.quote, ev.observed_at, ev.independent_key,
             created_at or self.clock.now(), ev.speaker_principal))

    # ---------- 09: trust and authors in a team

    def _ns_meta(self, cur, ns: str) -> dict:
        r = cur.execute("SELECT id, tenant_id, scope, owner_principal, owner_group, profile, settings, "
                        "default_classification FROM namespaces WHERE id=%s", (ns,)).fetchone()
        if not r:
            raise NotFound(f"namespace {ns} does not exist")
        return r

    def _check_speakers(self, cur, nsm: dict, evs: list) -> None:
        """TM-7: the speaker must be a member of the namespace's organization."""
        ids = sorted({e.speaker_principal for e in evs if e.speaker_principal})
        if not ids:
            return
        ok = {r["id"] for r in cur.execute("SELECT id FROM principals WHERE id = ANY(%s) AND tenant_id=%s",
                                           (ids, nsm["tenant_id"])).fetchall()}
        if set(ids) - ok:
            raise BadRequest("evidence.speaker_principal: no such member in the namespace's organization")

    def _is_member(self, cur, pid: str, nsm: dict) -> bool:
        """A principal is "own" for a namespace: a member of the owner group (with nesting) or of the organization (scope=tenant)."""
        p = cur.execute("SELECT tenant_id, active FROM principals WHERE id=%s", (pid,)).fetchone()
        if not p or p["tenant_id"] != nsm["tenant_id"]:
            return False
        if nsm["scope"] == "tenant":
            return True
        if not nsm["owner_group"]:
            return False
        mine = [x["group_id"] for x in cur.execute("SELECT group_id FROM group_members WHERE principal_id=%s",
                                                   (pid,)).fetchall()]
        if not mine:
            return False
        return any(x["id"] == nsm["owner_group"] for x in cur.execute(self._ANCESTORS_SQL, (mine,)).fetchall())

    def _ev_trusted(self, cur, nsm: dict, ev: EvidenceIn) -> bool:
        """1.6.1 + 09 (TM-9, TM-10). Personal layer: the owner's words and the trusted client's own work.
        Team layer: words of a member of the owner group; "owner" as a string means nothing here."""
        if nsm["scope"] == "personal":
            if ev.speaker_principal and ev.speaker_principal == nsm["owner_principal"]:
                return True
            return safety.evidence_trusted(ev.source_scope, ev.source_ref, ev.speaker, C.OWNER_SPEAKER)
        if ev.speaker_principal:
            return self._is_member(cur, ev.speaker_principal, nsm)
        return safety.evidence_trusted(ev.source_scope, ev.source_ref, None, C.OWNER_SPEAKER)

    def _principal_can(self, conn, ns: str, pid: str, right: str) -> bool:
        """Whether the principal has a right in the namespace (without the token ceiling), for the "the curator said" rule (TM-11)."""
        p = conn.execute("SELECT id, tenant_id, clearance, active FROM principals WHERE id=%s", (pid,)).fetchone()
        if not p:
            return False
        facts = policy.load_facts(conn, ns, {"id": p["id"], "tenant_id": p["tenant_id"], "clearance": p["clearance"],
                                             "active": p["active"]})
        rights, _ = policy.resolve(facts, self.clock.now(), ["*"], set(RIGHTS), ns, False)
        return right in rights

    def _add_evidence_tx(self, cur, row: dict, ev: EvidenceIn, actor: str, created_at=None) -> dict:
        mid = row["id"]
        existing = cur.execute("SELECT source_ref, independent_key, quote FROM evidence WHERE memory_id=%s",
                               (mid,)).fetchall()
        if any(e["source_ref"] == ev.source_ref for e in existing):
            return {"changed": False, "reason": "repeated source_ref, ignored"}
        independent = ev.independent_key not in {e["independent_key"] for e in existing} \
            and not any(safety.kin(ev.quote, e["quote"]) for e in existing)          # 1.6.4
        w = C.W_EVIDENCE_INDEPENDENT if independent else C.W_EVIDENCE_REPEAT
        before = float(row["eff"])
        after = LC.reinforce(before, w)
        nsm = self._ns_meta(cur, row["namespace"])
        self._check_speakers(cur, nsm, [ev])
        self._insert_evidence(cur, mid, ev, created_at)
        cur.execute("UPDATE memories SET evidence_count = evidence_count + 1 WHERE id=%s", (mid,))
        if self._ev_trusted(cur, nsm, ev):
            cur.execute("UPDATE memories SET trust='trusted' WHERE id=%s", (mid,))
        self._set_strength(cur, mid, after, reinforced=True)
        self._event(cur, mid, "evidence", after - before, actor)
        self._audit(cur, actor, "add_evidence", row["namespace"], mid,
                    detail={"independent": independent, "delta": round(after - before, 4)})
        return {"changed": True, "independent": independent, "strength_before": round(before, 4),
                "strength": round(after, 4)}

    # ---------- remember

    def remember(self, client: Client, ns: str, p: dict, *, _source: str | None = None,
                 _promoted_from=None, _curator_ok: bool = False) -> dict:
        """_source/_promoted_from/_curator_ok are only for the internal call when a promotion is accepted (09, TM-15)."""
        maxc = self._require(client, ns, "write")
        text = p.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BadRequest("a non-empty text is required")
        if len(text) > C.TEXT_MAX:
            raise BadRequest(f"text is longer than {C.TEXT_MAX} characters")
        secret = safety.find_secret(text)
        if secret:
            raise BadRequest(f"the text looks like a {secret}; keys and tokens are never stored in memory (1.6.3)")
        text, flags = safety.sanitize(text)
        if not text.strip():
            raise BadRequest("the text is empty after hidden characters were removed")
        kind = p.get("kind")
        if not kind:
            raise BadRequest("kind is required: the service does not classify records itself (P1)")
        evs_raw = p.get("evidence")
        if isinstance(evs_raw, dict):
            evs_raw = [evs_raw]
        if not evs_raw:
            raise BadRequest("at least one evidence item is required (D-5)")
        evs = [EvidenceIn.parse(e) for e in evs_raw]
        importance = p.get("importance", 2)
        if importance not in (1, 2, 3):
            raise BadRequest("importance must be 1, 2 or 3")
        visibility = p.get("visibility") or "shareable"
        if visibility not in VISIBILITY:
            raise BadRequest(f"visibility: one of {sorted(VISIBILITY)}")
        tags = p.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise BadRequest("tags: list of strings expected")
        subject, slot = p.get("subject") or None, p.get("slot") or None
        occurred_at = _dt(p.get("occurred_at"), "occurred_at")
        external_ref = p.get("external_ref") or None
        supersedes = p.get("supersedes") or None
        classification = p.get("classification")
        if classification is not None and classification not in (0, 1, 2, 3):
            raise BadRequest("classification: 0 (public), 1 (internal), 2 (confidential), 3 (restricted)")
        if classification is not None and classification > maxc:
            raise BadRequest(f"classification {classification} is above your clearance ({maxc}) in namespace {ns}")
        actor = client

        with self.pool.connection() as conn:
            cur = conn.cursor()
            if classification is None:
                classification = min(maxc, cur.execute("SELECT default_classification d FROM namespaces WHERE id=%s",
                                                       (ns,)).fetchone()["d"])
            k = cur.execute("SELECT * FROM kinds WHERE id=%s", (kind,)).fetchone()
            if not k:
                raise BadRequest(f"unknown kind: {kind}")
            nsm = self._ns_meta(cur, ns)
            profile = nsm["profile"]
            if profile not in k["profiles"]:
                raise BadRequest(f"kind {kind} is not allowed in namespace {ns} (profile {profile})")
            self._check_speakers(cur, nsm, evs)

            if external_ref:  # A-3: idempotency by external_ref
                ex = cur.execute("SELECT id FROM memories WHERE namespace=%s AND external_ref=%s",
                                 (ns, external_ref)).fetchone()
                if ex:
                    self._load(cur, ns, ex["id"], lock=True, maxc=maxc)
                    results = [self._add_evidence_tx(cur, self._load(cur, ns, ex["id"], maxc=maxc), ev, actor) for ev in evs]
                    conn.commit()
                    out = self.get(client, ns, str(ex["id"]))
                    out["idempotent"] = True
                    out["evidence_results"] = results
                    return out

            if (slot and not supersedes and not external_ref and not _promoted_from and not _curator_ok
                    and kind not in safety.RULE_KINDS):
                # Repeating the same claim in a slot confirms the current record instead of adding a second candidate:
                # otherwise the duplicate would replace the original at night and the reinforcement would be lost. Narrow:
                # same kind, subject, visibility and classification, the text matches (case, yo/ye, spaces, trailing period);
                # only the current record and only if the slot has no candidate (otherwise reinforcement would affect
                # gate G1); not rules (they have their own confirmation queue); same_chat only from the same chat (the
                # audience does not widen); a late older claim (occurred_at before the record) goes the usual way, to history.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{ns}#{slot}",))
                rows = cur.execute(
                    "SELECT id, text, state, visibility, classification, kind, subject, duplicate_of, "
                    "coalesce(occurred_at, created_at) AS t FROM memories "
                    "WHERE namespace=%s AND slot=%s AND state IN ('current','candidate') FOR UPDATE",
                    (ns, slot)).fetchall()
                cur_row = next((r for r in rows if r["state"] == "current"), None)
                chats = {chat_of(e.source_scope) for e in evs}
                hit = None
                if (cur_row and not any(r["state"] == "candidate" for r in rows) and cur_row["duplicate_of"] is None
                        and (cur_row["kind"], cur_row["subject"], cur_row["visibility"], cur_row["classification"])
                        == (kind, subject, visibility, classification)
                        and (occurred_at is None or occurred_at >= cur_row["t"])
                        and _same_claim(cur_row["text"], text)):
                    hit = cur_row
                    if visibility == "same_chat":
                        have = {r["c"] for r in cur.execute(
                            "SELECT DISTINCT split_part(source_scope, ':', 2) AS c FROM evidence WHERE memory_id=%s",
                            (hit["id"],)).fetchall()}
                        if "" in chats or not chats <= have:      # a scope without a chat: the audience is unknown
                            hit = None
                if hit:
                    results = [self._add_evidence_tx(cur, self._load(cur, ns, hit["id"], maxc=maxc), ev, actor) for ev in evs]
                    conn.commit()
                    out = self.get(client, ns, str(hit["id"]))
                    out["reinforced"] = any(r.get("changed") for r in results)
                    out["evidence_results"] = results
                    return out

            vec = self.emb.passage(text)   # before the write transaction; an error -> 503, nothing created
            now = self.clock.now()
            mid = uuid.uuid4()
            state, valid_from, valid_to, supersedes_id, slot_current = "current", now, None, None, None
            held_target = None

            trust = "trusted" if any(self._ev_trusted(cur, nsm, e) for e in evs) else "untrusted"
            team = nsm["profile"] == "team" and nsm["scope"] != "personal"
            # 09, TM-11: a team rule applies at once only if the curator said it (or the curator accepts a promotion)
            by_curator = _curator_ok or (team and any(
                e.speaker_principal and self._principal_can(conn, ns, e.speaker_principal, "review") for e in evs))
            needs_confirm = (kind in safety.RULE_KINDS and not _curator_ok
                             and (trust == "untrusted" or (team and not by_curator)))

            if supersedes:
                target = self._load(cur, ns, supersedes, lock=True, maxc=maxc)
                if target["state"] != "current":
                    raise BadRequest("supersedes: only the current record can be superseded")
                slot = slot or target["slot"]
                if slot and slot != target["slot"]:
                    occ = cur.execute("SELECT id FROM memories WHERE namespace=%s AND slot=%s AND state='current'",
                                      (ns, slot)).fetchone()
                    if occ:
                        raise Conflict(f"slot {slot} is held by record {occ['id']}, but supersedes points to another one")
                if needs_confirm:
                    # a rule waiting for confirmation does not replace the current one through an explicit supersedes:
                    # it becomes a candidate with a rule_confirm item, replaced on acceptance (otherwise a rule bypassed the owner and curator)
                    state, held_target = "candidate", target["id"]
                else:
                    cur.execute("UPDATE memories SET state='superseded', valid_to=%s WHERE id=%s", (now, target["id"]))
                    supersedes_id = target["id"]
            elif slot:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{ns}#{slot}",))
                occ = cur.execute(
                    "SELECT id, occurred_at, created_at, valid_from, classification FROM memories "
                    "WHERE namespace=%s AND slot=%s AND state='current' FOR UPDATE", (ns, slot)).fetchone()
                if occ:
                    slot_current = str(occ["id"]) if occ["classification"] <= maxc else "hidden"
                    c_time = occurred_at or now
                    k_time = occ["occurred_at"] or occ["created_at"]
                    if c_time < k_time:   # a late older claim (05 §2) -> straight to history
                        state, valid_from, valid_to = "superseded", c_time, occ["valid_from"]
                    else:
                        state = "candidate"

            # also into an occupied slot: a rule candidate without a confirmation item would pass gate G1 at night
            # without the owner or curator
            rule_hold = needs_confirm and state in ("current", "candidate") and not supersedes_id
            if rule_hold:           # 1.6.1: a rule from an external source does not become current without the owner
                state = "candidate"
            owner = nsm["scope"] == "personal" and any(
                e.speaker == C.OWNER_SPEAKER or (e.speaker_principal and e.speaker_principal == nsm["owner_principal"])
                for e in evs)
            s = LC.initial_strength(importance, owner)
            seen_refs: set[str] = set()
            seen_keys: set[str] = set()
            uniq: list[EvidenceIn] = []
            extra_events: list[float] = []
            for ev in evs:
                if ev.source_ref in seen_refs:
                    continue
                if uniq:   # the first evidence sets s0, the following ones reinforce
                    repeat = ev.independent_key in seen_keys or any(safety.kin(ev.quote, u.quote) for u in uniq)
                    w = C.W_EVIDENCE_REPEAT if repeat else C.W_EVIDENCE_INDEPENDENT
                    s2 = LC.reinforce(s, w)
                    extra_events.append(s2 - s)
                    s = s2
                seen_refs.add(ev.source_ref)
                seen_keys.add(ev.independent_key)
                uniq.append(ev)

            cur.execute(
                "INSERT INTO memories(id, namespace, kind, text, subject, slot, tags, importance, source, visibility, "
                "occurred_at, created_at, state, valid_from, valid_to, supersedes_id, strength, strength_at, "
                "evidence_count, last_reinforced_at, embedding, external_ref, classification, trust, flags, "
                "promoted_from) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (mid, ns, kind, text, subject, slot, tags, importance,
                 _source or (p.get("source") if client.local_admin and p.get("source") else client.id),
                 visibility, occurred_at, now,
                 state, valid_from, valid_to, supersedes_id, s, now, len(uniq), now, vec, external_ref, classification,
                 trust, flags, _promoted_from))
            for ev in uniq:
                self._insert_evidence(cur, mid, ev)
            if rule_hold:
                why = (f"\"{kind}\" from an untrusted source: becomes a rule only after the owner confirms it"
                       if trust == "untrusted" else
                       f"\"{kind}\" from a team member: becomes a team rule after the curator confirms it")
                cur.execute("INSERT INTO review_items(namespace, created_at, type, memory_ids, proposal, evidence_quotes, "
                            "proposed_by) VALUES (%s,%s,'rule_confirm',%s,%s,%s,%s)",
                            (ns, now, [mid], Jsonb({"explanation": why, **({"supersedes": str(held_target)}
                                                                            if held_target else {})}),
                             Jsonb([{"memory_id": str(mid), "speaker": e.speaker, "quote": e.quote,
                                     "source_ref": e.source_ref} for e in uniq]),
                             next((e.speaker_principal for e in uniq if e.speaker_principal), None)
                             or client.subject.get("id")))
            for d in extra_events:
                self._event(cur, mid, "evidence", d, actor)
            self._audit(cur, actor, "remember", ns, mid,
                        detail={"kind": kind, "state": state, "slot": slot, "evidence": len(uniq)})
            if supersedes_id:
                self._audit(cur, actor, "supersede", ns, supersedes_id,
                            detail={"successor": str(mid), "reason": "explicit"})
            conn.commit()

        out = self.get(client, ns, str(mid))
        if slot_current:
            out["slot_current_id"] = slot_current
        return out

    # ---------- evidence and signals

    def add_evidence(self, client: Client, ns: str, mid: str, ev_raw: dict) -> dict:
        maxc = self._require(client, ns, "write")
        ev = EvidenceIn.parse(ev_raw)
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, lock=True, maxc=maxc)
            res = self._add_evidence_tx(cur, row, ev, client)
            conn.commit()
        return {"id": str(row["id"]), **res}

    def signal(self, client: Client, ns: str, mid: str, kind: str) -> dict:
        if kind == "owner_confirm":
            maxc = self._require(client, ns, "review")
            w = C.W_OWNER_CONFIRM
        elif kind in SIGNALS:
            maxc = self._require(client, ns, "write")
            w = SIGNALS[kind]
        else:
            raise BadRequest(f"unknown signal {kind}")
        actor = client
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, lock=True, maxc=maxc)
            before = float(row["eff"])
            after = LC.reinforce(before, w)
            if kind == "owner_confirm":
                # the owner's confirmation is also evidence from the owner (for gate G1), without a second increment
                now = self.clock.now()
                self._insert_evidence(cur, row["id"], EvidenceIn(
                    f"owner:confirm:{now.isoformat()}", "owner", now, C.OWNER_SPEAKER, None, f"{C.OWNER_SPEAKER}@owner"))
                cur.execute("UPDATE memories SET evidence_count = evidence_count + 1, trust='trusted' WHERE id=%s",
                            (row["id"],))
            self._set_strength(cur, row["id"], after, reinforced=True)
            self._event(cur, row["id"], kind, after - before, actor)
            self._audit(cur, actor, "reinforce", ns, row["id"], detail={"event": kind, "delta": round(after - before, 4)})
            conn.commit()
        return {"id": mid, "event": kind, "strength_before": round(before, 4), "strength": round(after, 4)}

    def _reinforce_top5(self, ns: str, ids: list[str]) -> None:
        if not ids:
            return
        try:
            now = self.clock.now()
            with self.pool.connection() as conn:
                cur = conn.cursor()
                for mid in ids:
                    recent = cur.execute(
                        "SELECT 1 FROM memory_events WHERE memory_id=%s AND type='recalled_top5' AND at > %s",
                        (mid, now - timedelta(seconds=C.RECALLED_TOP5_MIN_INTERVAL_S))).fetchone()
                    if recent:
                        continue
                    row = self._load(cur, ns, mid, lock=True)
                    before = float(row["eff"])
                    after = LC.reinforce(before, C.W_RECALLED_TOP5)
                    self._set_strength(cur, row["id"], after, reinforced=False)
                    self._event(cur, row["id"], "recalled_top5", after - before, "service:recall")
                conn.commit()
        except Exception:  # noqa: BLE001 — results do not depend on top-5 accounting (04 item 5)
            log.exception("recalled_top5 reinforcement failed")

    def set_pin(self, client: Client, ns: str, mid: str, pinned: bool) -> dict:
        maxc = self._require(client, ns, "review")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, lock=True, maxc=maxc)
            # record the strength at the moment the decay mode changes
            cur.execute("UPDATE memories SET pinned=%s, strength=%s, strength_at=%s WHERE id=%s",
                        (pinned, float(row["eff"]), self.clock.now(), row["id"]))
            self._audit(cur, client, "pin" if pinned else "unpin", ns, row["id"])
            conn.commit()
        return self.get(client, ns, mid)

    def close(self, client: Client, ns: str, mid: str) -> dict:
        maxc = self._require(client, ns, "write")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, lock=True, maxc=maxc)
            if row["kind"] not in LC.MIRROR_KINDS:
                raise BadRequest("close applies only to mirrors: commitment_mirror, crm_mirror")
            if row["external_closed_at"] is None:
                now = self.clock.now()
                cur.execute("UPDATE memories SET external_closed_at=%s, strength=%s, strength_at=%s WHERE id=%s",
                            (now, float(row["eff"]), now, row["id"]))
                self._audit(cur, client, "close", ns, row["id"])
            conn.commit()
        return self.get(client, ns, mid)

    # ---------- archive, revert, delete

    def archive(self, client: Client, ns: str, mid: str) -> dict:
        maxc = self._require(client, ns, "write")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, lock=True, maxc=maxc)
            if row["state"] != "archived":
                self._archive_tx(cur, row, client, client)
            conn.commit()
        return self.get(client, ns, mid)

    def _archive_tx(self, cur, row: dict, actor: str, by: str, rule_id: str | None = None) -> None:
        now = self.clock.now()
        cur.execute("UPDATE memories SET prev_state=state, state='archived', archived_at=%s, archived_by=%s, "
                    "strength=%s, strength_at=%s WHERE id=%s", (now, str(by), float(row["eff"]), now, row["id"]))
        self._audit(cur, actor, "archive", row["namespace"], row["id"], rule_id=rule_id,
                    detail={"from": row["state"], "by": str(by)})

    def unarchive(self, client: Client, ns: str, mid: str) -> dict:
        maxc = self._require(client, ns, "write")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, lock=True, maxc=maxc)
            if row["state"] != "archived":
                raise BadRequest("the record is not archived")
            target = row["prev_state"] or "current"
            if target == "current" and row["slot"]:
                occ = cur.execute("SELECT id FROM memories WHERE namespace=%s AND slot=%s AND state='current'",
                                  (ns, row["slot"])).fetchone()
                if occ:
                    target = "candidate"
            now = self.clock.now()
            cur.execute("UPDATE memories SET state=%s, prev_state=NULL, archived_at=NULL, archived_by=NULL, "
                        "strength=%s, strength_at=%s, last_reinforced_at=%s WHERE id=%s",
                        (target, C.UNARCHIVE_STRENGTH, now, now, row["id"]))
            self._audit(cur, client, "unarchive", ns, row["id"], detail={"to": target})
            conn.commit()
        return self.get(client, ns, mid)

    def revert_supersede(self, client: Client, ns: str, mid: str) -> dict:
        maxc = self._require(client, ns, "review")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            new = self._load(cur, ns, mid, lock=True, maxc=maxc)
            if new["state"] != "current" or not new["supersedes_id"]:
                raise BadRequest("only a current record that superseded another can be reverted")
            old = self._load(cur, ns, new["supersedes_id"], lock=True, maxc=maxc)
            if old["state"] != "superseded":
                raise BadRequest("the superseded record is no longer in history; revert is impossible")
            if self.clock.now() - new["valid_from"] > timedelta(days=C.REVERT_WINDOW_DAYS):
                raise BadRequest(f"revert is possible within {C.REVERT_WINDOW_DAYS} days")
            cur.execute("UPDATE memories SET state='candidate', supersedes_id=NULL WHERE id=%s", (new["id"],))
            cur.execute("UPDATE memories SET state='current', valid_to=NULL WHERE id=%s", (old["id"],))
            self._audit(cur, client, "revert_supersede", ns, old["id"],
                        detail={"reverted_successor": str(new["id"])})
            conn.commit()
        return {"current": self.get(client, ns, str(old["id"])), "candidate": self.get(client, ns, mid)}

    def purge(self, client: Client, ns: str, mid: str) -> dict:
        maxc = self._require(client, ns, "forget")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, lock=True, maxc=maxc)
            self._purge_tx(cur, [row["id"]], ns, client)
            conn.commit()
        return {"purged": 1, "id": mid}

    def _purge_tx(self, cur, ids: list, ns: str, actor: str, rule_id: str | None = None) -> None:
        # review items that mention these records go too: they hold quotes and proposed text, and have no foreign key
        cur.execute("DELETE FROM review_items WHERE memory_ids && %s::uuid[]", (list(ids),))
        cur.execute("DELETE FROM memories WHERE id = ANY(%s)", (list(ids),))
        for i in ids:
            self._audit(cur, actor, "purge", ns, i, rule_id=rule_id)

    def forget(self, client: Client, ns: str, p: dict) -> dict:
        maxc = self._require(client, ns, "forget")
        mode = p.get("mode", "archive")
        if mode not in ("archive", "purge"):
            raise BadRequest("mode: archive or purge")
        dry_run = p.get("dry_run", True) is not False
        filt = {k: p.get(k) for k in ("subject", "slot", "tags", "kind", "source_ref") if p.get(k)}
        if not filt:
            raise BadRequest("at least one filter is required: subject, slot, tags, kind, source_ref (S-3)")
        where, params = ["m.namespace=%(ns)s", "m.classification <= %(maxc)s"], {"ns": ns, "now": self.clock.now(), "maxc": maxc}
        for k in ("subject", "slot", "kind"):
            if k in filt:
                where.append(f"m.{k}=%({k})s")
                params[k] = filt[k]
        if "tags" in filt:
            where.append("m.tags @> %(tags)s")
            params["tags"] = list(filt["tags"])
        if "source_ref" in filt:        # one source fragment: every record that has it among its evidence
            if not isinstance(filt["source_ref"], str):
                raise BadRequest("source_ref: string expected")
            where.append("EXISTS (SELECT 1 FROM evidence e WHERE e.memory_id = m.id AND e.source_ref = %(source_ref)s)")
            params["source_ref"] = filt["source_ref"]
        if mode == "archive":
            where.append("m.state <> 'archived'")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            rows = cur.execute(f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
                               f"WHERE {' AND '.join(where)} ORDER BY m.created_at FOR UPDATE OF m", params).fetchall()
            n = len(rows)
            result = {"mode": mode, "matched": n, "dry_run": dry_run,
                      "sample": [{"id": str(r["id"]), "kind": r["kind"], "subject": r["subject"]} for r in rows[:10]]}
            if dry_run:
                conn.rollback()
                return result
            if p.get("confirm") != n:
                raise BadRequest(f"confirm must equal the number of matching records ({n})")
            actor = client
            if mode == "purge":
                self._purge_tx(cur, [r["id"] for r in rows], ns, actor)
            else:
                for r in rows:
                    self._archive_tx(cur, r, actor, actor)
            conn.commit()
        result["applied"] = n
        return result

    # ---------- read

    def get(self, client: Client, ns: str, mid: str) -> dict:
        maxc = self._require(client, ns, "read")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, maxc=maxc)
            ev = cur.execute("SELECT * FROM evidence WHERE memory_id=%s ORDER BY observed_at, id", (row["id"],)).fetchall()
            evs = cur.execute("SELECT at, type, delta, actor FROM memory_events WHERE memory_id=%s "
                              "ORDER BY at DESC, id DESC LIMIT 20", (row["id"],)).fetchall()
            conn.rollback()
        self._log_access(client, ns, [row])
        out = _row_to_memory(row, row["eff"])
        out["evidence"] = [_evidence_to_dict(e) for e in ev]
        out["events"] = [{"at": _iso(e["at"]), "type": e["type"], "delta": round(e["delta"], 4), "actor": e["actor"]}
                         for e in evs]
        return out

    def _filters(self, ns: str, p: dict, now: datetime, maxc: int) -> tuple[list[str], dict]:
        where = ["m.namespace=%(ns)s", "m.duplicate_of IS NULL", "m.classification <= %(maxc)s"]
        params = {"ns": ns, "now": now, "maxc": maxc}
        as_of = _dt(p.get("as_of"), "as_of")
        state = p.get("state")
        if as_of:
            where.append("m.state IN ('current','superseded') AND m.valid_from <= %(as_of)s "
                         "AND (m.valid_to IS NULL OR m.valid_to > %(as_of)s) AND m.created_at <= %(as_of)s")
            params["as_of"] = as_of
        elif state == "all":
            pass
        elif state:
            states = state if isinstance(state, list) else [state]
            if set(states) - STATES:
                raise BadRequest(f"state: one of {sorted(STATES)} or all")
            where.append("m.state = ANY(%(states)s)")
            params["states"] = states
        else:
            allowed = ["current"]
            if p.get("include_candidates"):
                allowed.append("candidate")
            if p.get("include_archived"):
                allowed.append("archived")
            where.append("m.state = ANY(%(states)s)")
            params["states"] = allowed
        for k in ("kind", "subject", "slot"):
            v = p.get(k)
            if v:
                if isinstance(v, list):
                    where.append(f"m.{k} = ANY(%({k})s)")
                elif k == "subject":           # 09, TM-31: subject aliases in both directions
                    where.append(f"m.subject IN ({SUBJECT_EQ_SQL})")
                else:
                    where.append(f"m.{k} = %({k})s")
                params[k] = v
        tags = p.get("tags")
        if tags:
            if isinstance(tags, str):
                tags = [t for t in tags.split(",") if t]
            mode = p.get("tags_mode", "any")
            if mode not in ("any", "all"):
                raise BadRequest("tags_mode: any or all")
            where.append("m.tags && %(tags)s" if mode == "any" else "m.tags @> %(tags)s")
            params["tags"] = list(tags)
        ca, cb = _dt(p.get("created_after"), "created_after"), _dt(p.get("created_before"), "created_before")
        if ca:
            where.append("m.created_at >= %(ca)s"); params["ca"] = ca
        if cb:
            where.append("m.created_at < %(cb)s"); params["cb"] = cb
        audience = p.get("audience")
        if audience:
            if not str(audience).startswith("chat:") or len(str(audience)) <= 5:
                raise BadRequest("audience: format chat:<id>")
            params["aud"] = str(audience)[5:]
            where.append("(m.visibility = 'shareable' OR (m.visibility = 'same_chat' AND EXISTS ("
                         "SELECT 1 FROM evidence e WHERE e.memory_id = m.id "
                         "AND split_part(e.source_scope, ':', 2) = %(aud)s)))")
        ms = p.get("min_strength")
        if ms is not None:
            where.append(f"({EFF}) >= %(min_strength)s")
            params["min_strength"] = float(ms)
        return where, params

    def list(self, client: Client, ns: str, p: dict) -> dict:
        maxc = self._require(client, ns, "read")
        now = self.clock.now()
        where, params = self._filters(ns, p, now, maxc)
        limit = max(1, min(int(p.get("limit", 20)), 200))
        offset = 0
        if p.get("cursor"):
            try:
                offset = int(base64.urlsafe_b64decode(str(p["cursor"]).encode()).decode())
            except Exception as e:  # noqa: BLE001
                raise BadRequest("invalid cursor") from e
        order = p.get("sort", "created_at")
        order_sql = {"strength": f"({EFF}) DESC, m.created_at DESC, m.id",
                     "created_at": "m.created_at DESC, m.id"}.get(order)
        if not order_sql:
            raise BadRequest("sort: strength or created_at")
        params.update(limit=limit + 1, offset=offset)
        with self.pool.connection() as conn:
            rows = conn.execute(f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
                                f"WHERE {' AND '.join(where)} ORDER BY {order_sql} "
                                "LIMIT %(limit)s OFFSET %(offset)s", params).fetchall()
            ev = self._evidence_for(conn, [r["id"] for r in rows[:limit]], 3)
            conn.rollback()
        items = []
        for r in rows[:limit]:
            m = _row_to_memory(r, r["eff"])
            m["evidence"] = ev.get(r["id"], [])
            items.append(m)
        self._log_access(client, ns, rows[:limit])
        nxt = None
        if len(rows) > limit:
            nxt = base64.urlsafe_b64encode(str(offset + limit).encode()).decode()
        return {"items": items, "next_cursor": nxt}

    def _evidence_for(self, conn, ids: list, per: int) -> dict:
        if not ids:
            return {}
        rows = conn.execute(
            "SELECT * FROM (SELECT e.*, row_number() OVER (PARTITION BY memory_id ORDER BY observed_at DESC, id DESC) rn "
            "FROM evidence e WHERE memory_id = ANY(%s)) x WHERE rn <= %s ORDER BY memory_id, observed_at DESC",
            (list(ids), per)).fetchall()
        out: dict = {}
        for e in rows:
            out.setdefault(e["memory_id"], []).append(_evidence_to_dict(e))
        return out

    def recall(self, client: Client, ns: str, p: dict) -> dict:
        maxc = self._require(client, ns, "read")
        query = (p.get("query") or "").strip()
        limit = max(1, min(int(p.get("limit", 10)), 50))
        if not query:   # R-4
            res = self.list(client, ns, {**p, "sort": "strength", "limit": limit, "cursor": None})
            return {"items": res["items"], "query": ""}
        now = self.clock.now()
        where, params = self._filters(ns, p, now, maxc)
        qv = self.emb.query(query)
        params.update(qv=qv, q=query, per=S.CANDIDATES_PER_CHANNEL)
        w = " AND ".join(where)
        sql = f"""
        WITH vec AS (
          SELECT m.id FROM memories m JOIN kinds k ON k.id=m.kind WHERE {w}
          ORDER BY m.embedding <=> %(qv)s LIMIT %(per)s),
        lex AS (
          SELECT m.id FROM memories m JOIN kinds k ON k.id=m.kind WHERE {w}
            AND m.fts @@ (websearch_to_tsquery('russian', %(q)s) || websearch_to_tsquery('english', %(q)s))
          ORDER BY greatest(ts_rank_cd(m.fts, websearch_to_tsquery('russian', %(q)s)),
                            ts_rank_cd(m.fts, websearch_to_tsquery('english', %(q)s))) DESC LIMIT %(per)s)
        SELECT {MEM_COLUMNS}, ({EFF}) AS eff,
               1 - (m.embedding <=> %(qv)s) AS sem,
               CASE WHEN m.fts @@ (websearch_to_tsquery('russian', %(q)s) || websearch_to_tsquery('english', %(q)s))
                    THEN greatest(ts_rank_cd(m.fts, websearch_to_tsquery('russian', %(q)s)),
                                  ts_rank_cd(m.fts, websearch_to_tsquery('english', %(q)s)))
                    ELSE 0 END AS lex
        FROM memories m JOIN kinds k ON k.id=m.kind
        WHERE m.id IN (SELECT id FROM vec UNION SELECT id FROM lex)
        """
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            by_id = {str(r["id"]): r for r in rows}
            ranked = S.score([S.Candidate(str(r["id"]), float(r["sem"]), float(r["lex"]), float(r["eff"]),
                                          float(r["rank_weight"]), int(r["importance"]),
                                          r["created_at"].timestamp()) for r in rows])[:limit]
            top_ids = [by_id[x["id"]]["id"] for x in ranked]
            ev = self._evidence_for(conn, top_ids, 3)
            slots = [by_id[x["id"]]["slot"] for x in ranked if by_id[x["id"]]["slot"] and by_id[x["id"]]["state"] == "current"]
            contested = {}
            if slots:
                for c in conn.execute("SELECT slot, id FROM memories WHERE namespace=%s AND state='candidate' "
                                      "AND slot = ANY(%s) AND classification <= %s ORDER BY created_at DESC",
                                      (ns, slots, maxc)).fetchall():
                    contested.setdefault(c["slot"], str(c["id"]))
            conn.rollback()
        items = []
        for x in ranked:
            r = by_id[x["id"]]
            m = _row_to_memory(r, r["eff"])
            m["evidence"] = ev.get(r["id"], [])
            m["score"] = x["score"]
            if r["state"] == "current" and r["slot"] in contested:
                m["contested"] = True
                m["candidate_id"] = contested[r["slot"]]
            else:
                m["contested"] = False
            if p.get("explain"):
                m["explain"] = {k: x[k] for k in ("sem", "lex", "strength", "prior", "score")}
            items.append(m)
        self._log_access(client, ns, [by_id[x["id"]] for x in ranked])
        self._reinforce_top5(ns, [i["id"] for i in items[:5]])
        return {"items": items, "query": query}

    LAYER_WEIGHT = {"personal": 1.0, "group": 0.95, "tenant": 0.9}

    def recall_layers(self, client: Client, p: dict) -> dict:
        """Search across several memory layers (08): each reachable namespace gets its own check of rights and
        clearance, then results merge by score weighted by layer (personal > group > organization)."""
        wanted = p.get("namespaces")
        self._set_scope(client)
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT id, scope FROM namespaces ORDER BY id").fetchall()
            conn.rollback()
        scopes = {r["id"]: r["scope"] for r in rows}
        want_scopes = p.get("scopes")
        if want_scopes is not None:                      # 09, TM-23
            if isinstance(want_scopes, str):
                want_scopes = [x for x in want_scopes.split(",") if x]
            if not want_scopes or set(want_scopes) - {"personal", "group", "tenant"}:
                raise BadRequest("scopes: a subset of personal, group, tenant")
        candidates = [ns for ns in (wanted or scopes) if ns in scopes
                      and (want_scopes is None or scopes[ns] in want_scopes)]
        limit = max(1, min(int(p.get("limit", 10)), 50))
        merged, searched = [], []
        for ns in candidates:
            rights, _ = self._access(client, ns)
            if "read" not in rights:
                if wanted:
                    raise Forbidden(f"no access to namespace {ns}")
                continue
            res = self.recall(client, ns, {k: v for k, v in {**p, "limit": limit}.items()
                                           if k not in ("namespaces", "scopes")})
            w = self.LAYER_WEIGHT.get(scopes[ns], 0.9)
            for it in res["items"]:
                it["layer"] = {"namespace": ns, "scope": scopes[ns]}
                it["score"] = round(it.get("score", 0) * w, 6)
                merged.append(it)
            searched.append(ns)
        merged.sort(key=lambda x: (-x["score"], x["created_at"], x["id"]))
        return {"items": merged[:limit], "query": p.get("query", ""), "namespaces": searched}

    def history(self, client: Client, ns: str, mid: str) -> dict:
        maxc = self._require(client, ns, "read")
        now = self.clock.now()
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, maxc=maxc)
            if row["slot"]:
                rows = cur.execute(f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
                                   "WHERE m.namespace=%(ns)s AND m.slot=%(slot)s AND m.classification <= %(maxc)s "
                                   "ORDER BY m.valid_from, m.created_at", {"ns": ns, "slot": row["slot"], "now": now, "maxc": maxc}).fetchall()
            else:
                chain = {row["id"]: row}
                frontier = [row]
                while frontier:
                    r = frontier.pop()
                    nb = []
                    if r["supersedes_id"]:
                        nb += cur.execute(f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
                                          "WHERE m.id=%(id)s AND m.classification <= %(maxc)s",
                                          {"id": r["supersedes_id"], "now": now, "maxc": maxc}).fetchall()
                    nb += cur.execute(f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
                                      "WHERE m.supersedes_id=%(id)s AND m.classification <= %(maxc)s",
                                      {"id": r["id"], "now": now, "maxc": maxc}).fetchall()
                    for n in nb:
                        if n["id"] not in chain:
                            chain[n["id"]] = n
                            frontier.append(n)
                rows = sorted(chain.values(), key=lambda r: (r["valid_from"], r["created_at"]))
            ids = [r["id"] for r in rows]
            audits = cur.execute("SELECT at, actor, action, memory_id, detail FROM audit_log WHERE memory_id = ANY(%s) "
                                 "AND action IN ('supersede','promote','revert_supersede','archive','unarchive') "
                                 "ORDER BY at, id", (ids,)).fetchall()
            conn.rollback()
        return {
            "slot": row["slot"],
            "records": [_row_to_memory(r, r["eff"]) for r in rows],
            "transitions": [{"at": _iso(a["at"]), "actor": a["actor"], "action": a["action"],
                             "memory_id": str(a["memory_id"]), "detail": a["detail"]} for a in audits],
        }

    # ---------- import (06, F-10)

    def import_records(self, client: Client, ns: str, records: list[dict], dry_run: bool = True) -> dict:
        """Import of labelled records (admin, localhost). dry_run only checks the fields and writes nothing.
        The record's source field is kept (for example import:legacy); repeating by external_ref is idempotent."""
        self._require(client, ns, "admin")
        if not isinstance(records, list) or not records:
                raise BadRequest("records: a non-empty list expected")
        problems = []
        for i, r in enumerate(records):
            try:
                if not r.get("text") or not r.get("kind"):
                    raise BadRequest("text and kind are required")
                evs = r.get("evidence") or []
                if not evs:
                    raise BadRequest("evidence is required")
                [EvidenceIn.parse(e) for e in evs]
                if r.get("importance", 2) not in (1, 2, 3):
                    raise BadRequest("importance 1..3")
            except BadRequest as e:
                problems.append({"index": i, "error": e.message})
        if problems or dry_run:
            return {"dry_run": dry_run, "records": len(records), "problems": problems}
        created = []
        for r in records:
            m = self.remember(client, ns, r)
            created.append({"id": m["id"], "state": m["state"], "idempotent": m.get("idempotent", False)})
        return {"dry_run": False, "records": len(records), "created": created}

    # ---------- the owner's decision queue (05, 03 "Cleanup and decision queue")

    def review_list(self, client: Client, ns: str, status: str = "open") -> dict:
        maxc = self._require(client, ns, "review")
        with self.pool.connection() as conn:
            items = conn.execute("SELECT * FROM review_items WHERE namespace=%s AND (%s = 'all' OR status=%s) "
                                 "ORDER BY created_at, id", (ns, status, status)).fetchall()
            out = []
            for it in items:
                recs = conn.execute("SELECT m.id, m.kind, m.text, m.slot, m.state, m.classification FROM memories m "
                                    "WHERE m.id = ANY(%s)", (list(it["memory_ids"]),)).fetchall()
                if it["type"] == "promotion":      # 09, TM-14: the record does not exist yet; the content is in the proposal
                    if int((it["proposal"] or {}).get("classification", 0)) > maxc:
                        continue
                elif any(r["classification"] > maxc for r in recs) or not recs:
                    continue          # an item with a record above the clearance is not shown at all (08)
                quotes = {q.get("memory_id"): q for q in (it["evidence_quotes"] or [])}
                out.append({"id": it["id"], "type": it["type"], "status": it["status"], "created_at": _iso(it["created_at"]),
                            "namespace": ns, "proposed_by": it.get("proposed_by"),
                            "proposal": it["proposal"],
                            "records": [{"id": str(r["id"]), "kind": r["kind"], "text": r["text"], "slot": r["slot"],
                                         "state": r["state"], "quote": quotes.get(str(r["id"]))} for r in recs]})
            conn.rollback()
        return {"items": out}

    def review_decide(self, client: Client, ns: str, rid: int, decision: str, winner: str | None = None) -> dict:
        """accept/reject of a queue item. The owner's decision is a confirmation (owner_confirm) of the chosen record."""
        maxc = self._require(client, ns, "review")
        if decision not in ("accept", "reject"):
            raise BadRequest("decision: accept | reject")
        now = self.clock.now()
        with self.pool.connection() as conn:
            cur = conn.cursor()
            it = cur.execute("SELECT * FROM review_items WHERE id=%s AND namespace=%s FOR UPDATE", (rid, ns)).fetchone()
            if not it:
                raise NotFound("queue item not found")
            if it["status"] != "open":
                raise Conflict(f"the item is already closed: {it['status']}")
            rows = {str(m): self._load(cur, ns, m, lock=True, maxc=maxc) for m in it["memory_ids"]}
            result: dict = {"id": rid, "type": it["type"], "decision": decision}
            if decision == "accept":
                t = it["type"]
                if t == "contradiction":
                    if winner not in rows:
                        raise BadRequest("for a contradiction, pass winner: the id of the right record")
                    w = rows[winner]
                    for mid, loser in rows.items():
                        if mid == winner or loser["state"] not in ("current", "candidate"):
                            continue
                        self._supersede_tx(cur, w, loser, "review", client)
                    slot = (it["proposal"] or {}).get("suggested_slot")
                    if slot and not any(r["slot"] for r in rows.values()):
                        cur.execute("UPDATE memories SET slot=%s WHERE id = ANY(%s)", (slot, list(it["memory_ids"])))
                    self._owner_confirm_tx(cur, w, client)
                    result["winner"] = winner
                elif t == "slot_suggestion":
                    slot = it["proposal"]["slot"]
                    ordered = sorted(rows.values(), key=lambda r: (r["occurred_at"] or r["created_at"]))
                    newest = ordered[-1]
                    for r in ordered[:-1]:
                        if r["state"] == "current":
                            self._supersede_tx(cur, newest, r, "review:slot", client)
                    occ = cur.execute("SELECT id FROM memories WHERE namespace=%s AND slot=%s AND state='current' "
                                      "AND NOT (id = ANY(%s))", (ns, slot, list(it["memory_ids"]))).fetchone()
                    if occ:
                        raise Conflict(f"slot {slot} already has a current record {occ['id']}")
                    cur.execute("UPDATE memories SET slot=%s WHERE id = ANY(%s)", (slot, list(it["memory_ids"])))
                    result["slot"] = slot
                elif t == "stale":
                    for r in rows.values():
                        if r["state"] != "archived":
                            self._archive_tx(cur, r, client, str(client), rule_id="review:stale")
                elif t == "merge_suggestion":
                    # the owner accepted a summary that failed G2 (for example, checked the wording themselves)
                    evid, seen = [], set()
                    for m in it["memory_ids"]:
                        for e in cur.execute("SELECT * FROM evidence WHERE memory_id=%s ORDER BY id", (m,)).fetchall():
                            if e["source_ref"] not in seen:
                                seen.add(e["source_ref"])
                                d = _evidence_to_dict(e)
                                evid.append({k: v for k, v in d.items() if v is not None})
                    first = next(iter(rows.values()))
                    rec = self.remember(client, ns, {
                        "text": it["proposal"]["summary"], "kind": "insight", "subject": first["subject"],
                        "evidence": evid, "classification": max(r["classification"] for r in rows.values())})
                    for r in rows.values():
                        before = float(r["eff"])
                        self._set_strength(cur, r["id"], before * C.SUMMARIZED_FACTOR, reinforced=False)
                        self._event(cur, r["id"], "summarized_into", before * C.SUMMARIZED_FACTOR - before, client)
                    result["summary_id"] = rec["id"]
                elif t == "promotion":            # 09, TM-15: the curator accepted; the record appears in this layer
                    rec = self._apply_promotion(client, ns, it["proposal"] or {})
                    result["memory_id"] = rec["id"]
                    result["state"] = rec["state"]
                elif t == "rule_confirm":         # 1.6.1: the owner confirmed a rule from an external source
                    for r in rows.values():
                        if r["state"] != "candidate" or r.get("duplicate_of"):
                            continue                  # a merged duplicate: already confirmed by the current record
                        occ = cur.execute("SELECT id FROM memories WHERE namespace=%s AND slot=%s AND state='current' "
                                          "AND id <> %s FOR UPDATE", (ns, r["slot"], r["id"])).fetchone() \
                            if r["slot"] else None
                        target = (it["proposal"] or {}).get("supersedes")
                        if occ:                       # the slot is held: the confirmed rule replaces the current one (D-2)
                            self._supersede_tx(cur, r, self._load(cur, ns, occ["id"], maxc=maxc), "rule_confirm", client)
                        elif target and cur.execute("SELECT 1 FROM memories WHERE id=%s AND namespace=%s AND "
                                                    "state='current'", (target, ns)).fetchone():
                            self._supersede_tx(cur, r, self._load(cur, ns, target, maxc=maxc), "rule_confirm", client)
                        else:
                            cur.execute("UPDATE memories SET state='current', valid_from=%s WHERE id=%s", (now, r["id"]))
                        self._owner_confirm_tx(cur, r, client)
            elif it["type"] == "stale":
                for r in rows.values():            # "still relevant" = the owner's confirmation
                    self._owner_confirm_tx(cur, r, client)
            elif it["type"] == "rule_confirm":     # rejected: archived, it will not become a rule
                for r in rows.values():
                    if r["state"] == "candidate":
                        self._archive_tx(cur, r, client, str(client), rule_id="review:rule_rejected")
            cur.execute("UPDATE review_items SET status=%s, decided_at=%s, decided_by=%s WHERE id=%s",
                        ("accepted" if decision == "accept" else "rejected", now, str(client), rid))
            if it["type"] == "rule_confirm":     # decided by confirmation: a contradiction on the same record is closed
                cur.execute("UPDATE review_items SET status='expired', decided_at=%s, decided_by=%s WHERE status='open' "
                            "AND type='contradiction' AND memory_ids && %s::uuid[]",
                            (now, str(client), list(it["memory_ids"])))
            self._audit(cur, client, "review_decision", ns, None,
                        detail={"review_id": rid, "type": it["type"], "decision": decision, "winner": winner})
            conn.commit()
        return result

    def _supersede_tx(self, cur, winner: dict, loser: dict, reason: str, actor) -> None:
        now = self.clock.now()
        cur.execute("UPDATE memories SET state='superseded', valid_to=%s WHERE id=%s", (now, loser["id"]))
        if winner["state"] == "candidate":
            cur.execute("UPDATE memories SET state='current', valid_from=%s WHERE id=%s", (now, winner["id"]))
        taken = cur.execute("SELECT 1 FROM memories WHERE supersedes_id=%s", (loser["id"],)).fetchone()
        if winner["supersedes_id"] is None and not taken:
            cur.execute("UPDATE memories SET supersedes_id=%s WHERE id=%s", (loser["id"], winner["id"]))
        self._audit(cur, actor, "supersede", loser["namespace"], loser["id"],
                    detail={"successor": str(winner["id"]), "reason": reason})

    def _owner_confirm_tx(self, cur, row: dict, actor) -> None:
        before = float(row["eff"])
        after = LC.reinforce(before, C.W_OWNER_CONFIRM)
        now = self.clock.now()
        self._insert_evidence(cur, row["id"], EvidenceIn(f"owner:review:{now.isoformat()}", "owner", now,
                                                         C.OWNER_SPEAKER, None, f"{C.OWNER_SPEAKER}@owner"))
        cur.execute("UPDATE memories SET evidence_count = evidence_count + 1, trust='trusted' WHERE id=%s", (row["id"],))
        self._set_strength(cur, row["id"], after, reinforced=True)
        self._event(cur, row["id"], "owner_confirm", after - before, actor)

    # ---------- 09: promotion between layers, shared queue, aliases, a member leaving

    def promote(self, client: Client, ns: str, mid: str, p: dict) -> dict:
        """TM-13...TM-19: propose a record for another layer. The source record does not change."""
        maxc = self._require(client, ns, "write")
        to = (p or {}).get("to")
        if not to or not isinstance(to, str) or to == ns:
            raise BadRequest("to: a target namespace other than the source")
        rights_to, maxc_to = self._access(client, to)
        if "write" not in rights_to:
            raise Forbidden(f"no write right in namespace {to}")
        share = (p or {}).get("share_quotes", True) is not False
        now = self.clock.now()
        with self.pool.connection() as conn:
            cur = conn.cursor()
            row = self._load(cur, ns, mid, maxc=maxc)
            if row["state"] != "current":
                raise BadRequest("only a current record can be promoted")
            src, dst = self._ns_meta(cur, ns), self._ns_meta(cur, to)
            if src["tenant_id"] != dst["tenant_id"]:
                raise Forbidden(f"no access to namespace {to}")
            k = cur.execute("SELECT profiles FROM kinds WHERE id=%s", (row["kind"],)).fetchone()
            if dst["profile"] not in k["profiles"]:
                raise BadRequest(f"kind {row['kind']} is not allowed in namespace {to} (profile {dst['profile']})")
            if row["classification"] > maxc_to:          # TM-17: the level is never lowered
                raise BadRequest(f"the record level ({row['classification']}) is above your clearance in namespace {to}")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"promote#{to}#{row['id']}",))
            dup = cur.execute("SELECT id FROM review_items WHERE namespace=%s AND type='promotion' AND status='open' "
                              "AND proposal->>'source_id' = %s", (to, str(row["id"]))).fetchone()
            if dup:
                raise Conflict(f"a promotion of this record is already waiting for a decision (item {dup['id']})")
            evs = cur.execute("SELECT * FROM evidence WHERE memory_id=%s ORDER BY observed_at, id", (row["id"],)).fetchall()
            proposal = {
                "source_ns": ns, "source_id": str(row["id"]), "text": row["text"], "kind": row["kind"],
                "subject": row["subject"], "slot": row["slot"], "tags": list(row["tags"] or []),
                "importance": row["importance"], "classification": row["classification"],
                "note": (p or {}).get("note"), "share_quotes": share,
                "evidence": [{"source_ref": e["source_ref"], "source_scope": e["source_scope"], "speaker": e["speaker"],
                              "speaker_principal": e.get("speaker_principal"), "quote": e["quote"] if share else None,
                              "observed_at": _iso(e["observed_at"]), "independent_key": e["independent_key"]}
                             for e in evs],
                "explanation": f"a member proposes to move the record from {ns} to {to}",
            }
            auto = row["kind"] in ((dst["settings"] or {}).get("promotion_auto") or [])        # TM-19
            rid = None
            if not auto:
                rid = cur.execute(
                    "INSERT INTO review_items(namespace, created_at, type, memory_ids, proposal, evidence_quotes, "
                    "proposed_by) VALUES (%s,%s,'promotion','{}',%s,%s,%s) RETURNING id",
                    (to, now, Jsonb(proposal), Jsonb([{"speaker": e["speaker"], "quote": e["quote"],
                                                      "source_ref": e["source_ref"]} for e in evs] if share else []),
                     client.subject.get("id"))).fetchone()["id"]
            self._audit(cur, client, "promote_request", ns, row["id"], detail={"to": to, "review_id": rid, "auto": auto})
            conn.commit()
        if auto:
            rec = self._apply_promotion(client, to, proposal)
            return {"status": "accepted", "to": to, "memory_id": rec["id"], "state": rec["state"]}
        return {"status": "open", "to": to, "review_id": rid}

    def _apply_promotion(self, client: Client, ns: str, proposal: dict) -> dict:
        """Creates the record in the target layer. A repeated call for the same source returns the record already created."""
        src_id = proposal.get("source_id")
        with self.pool.connection() as conn:
            ex = conn.execute("SELECT id FROM memories WHERE namespace=%s AND promoted_from=%s", (ns, src_id)).fetchone()
            conn.rollback()
        if ex:
            return self.get(client, ns, str(ex["id"]))
        body = {k: proposal.get(k) for k in ("text", "kind", "subject", "slot", "tags", "importance", "classification")}
        body["evidence"] = proposal.get("evidence") or []
        return self.remember(client, ns, body, _source=f"promote:{proposal.get('source_ns')}",
                             _promoted_from=src_id, _curator_ok=True)

    def review_all(self, client: Client, status: str = "open") -> dict:
        """TM-21: queue across all namespaces where the caller has the review right."""
        self._set_scope(client)
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT id, scope FROM namespaces ORDER BY id").fetchall()
            conn.rollback()
        out = []
        for r in rows:
            rights, _ = self._access(client, r["id"])
            if "review" not in rights:
                continue
            for it in self.review_list(client, r["id"], status)["items"]:
                it["layer"] = {"namespace": r["id"], "scope": r["scope"]}
                out.append(it)
        out.sort(key=lambda x: (x["created_at"], x["id"]))
        return {"items": out}

    def alias_set(self, client: Client, ns: str, alias: str, canonical: str) -> dict:
        """TM-31: a subject alias for the namespace's organization (person:tg123 -> crm:contact:77)."""
        self._require(client, ns, "write")
        if not alias or not canonical or alias == canonical:
            raise BadRequest("alias and canonical must differ")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            tenant = self._ns_meta(cur, ns)["tenant_id"]
            cur.execute("INSERT INTO subject_aliases(tenant_id, alias, canonical, created_by, created_at) "
                        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (tenant_id, alias) DO UPDATE SET "
                        "canonical=EXCLUDED.canonical, created_by=EXCLUDED.created_by",
                        (tenant, alias, canonical, client.subject.get("id"), self.clock.now()))
            self._audit(cur, client, "alias_set", ns, detail={"alias": alias, "canonical": canonical})
            conn.commit()
        return {"alias": alias, "canonical": canonical, "tenant": tenant}

    def offboard(self, pid: str, purge_after_days: int = 30) -> dict:
        """TM-26...TM-28: a member leaves. Access ends at once; the personal layer waits for deletion; their words in team
        memory stay as evidence. Called from the CLI (admin right)."""
        now = self.clock.now()
        with self.pool.connection() as conn:
            cur = conn.cursor()
            if not cur.execute("SELECT 1 FROM principals WHERE id=%s", (pid,)).fetchone():
                raise BadRequest(f"principal {pid} does not exist")
            ids = [pid] + [r["id"] for r in cur.execute("SELECT id FROM principals WHERE on_behalf_of=%s", (pid,)).fetchall()]
            cur.execute("UPDATE principals SET active=false WHERE id = ANY(%s)", (ids,))
            tokens = cur.execute("DELETE FROM clients WHERE principal_id = ANY(%s) RETURNING id", (ids,)).fetchall()
            groups = cur.execute("DELETE FROM group_members WHERE principal_id=%s RETURNING group_id", (pid,)).fetchall()
            spaces = cur.execute(
                "UPDATE namespaces SET settings = settings || %s WHERE scope='personal' AND owner_principal=%s "
                "RETURNING id", (Jsonb({"offboarded_at": now.isoformat(),
                                        "purge_after": (now + timedelta(days=purge_after_days)).isoformat()}), pid)).fetchall()
            self._audit(cur, "admin", "offboard", None,
                        detail={"principal": pid, "clients": len(tokens), "groups": [g["group_id"] for g in groups],
                                "namespaces": [n["id"] for n in spaces], "purge_after_days": purge_after_days})
            conn.commit()
        return {"principal": pid, "clients_revoked": [t["id"] for t in tokens],
                "groups_left": [g["group_id"] for g in groups], "personal_namespaces": [n["id"] for n in spaces]}

    def purge_namespace(self, ns: str) -> dict:
        """Erases all records of a namespace (the personal layer of a member who left). CLI only."""
        with self.pool.connection() as conn:
            cur = conn.cursor()
            self._ns_exists(cur, ns)
            ids = [r["id"] for r in cur.execute("SELECT id FROM memories WHERE namespace=%s", (ns,)).fetchall()]
            if ids:
                self._purge_tx(cur, ids, ns, "admin", rule_id="offboard")
            cur.execute("DELETE FROM review_items WHERE namespace=%s", (ns,))
            cur.execute("UPDATE namespaces SET settings = settings || %s WHERE id=%s",
                        (Jsonb({"purged_at": self.clock.now().isoformat()}), ns))
            conn.commit()
        return {"namespace": ns, "purged": len(ids)}

    def forget_subject(self, tenant: str, subject: str, dry_run: bool = True, confirm: int | None = None) -> dict:
        """TM-29: "forget a subject": all records about it and its aliases in all namespaces of the organization."""
        if not subject:
            raise BadRequest("subject is required")
        with self.pool.connection() as conn:
            cur = conn.cursor()
            canon = cur.execute("SELECT canonical FROM subject_aliases WHERE tenant_id=%s AND alias=%s",
                                (tenant, subject)).fetchone()
            root = canon["canonical"] if canon else subject
            names = {subject, root} | {r["alias"] for r in cur.execute(
                "SELECT alias FROM subject_aliases WHERE tenant_id=%s AND canonical=%s", (tenant, root)).fetchall()}
            rows = cur.execute("SELECT m.id, m.namespace FROM memories m JOIN namespaces n ON n.id = m.namespace "
                               "WHERE n.tenant_id=%s AND m.subject = ANY(%s)", (tenant, sorted(names))).fetchall()
            by_ns: dict = {}
            for r in rows:
                by_ns.setdefault(r["namespace"], []).append(r["id"])
            report = {"tenant": tenant, "subjects": sorted(names), "matched": len(rows),
                      "by_namespace": {k: len(v) for k, v in by_ns.items()}, "dry_run": dry_run}
            if dry_run:
                conn.rollback()
                return report
            if confirm != len(rows):
                raise BadRequest(f"confirm must equal the number of records ({len(rows)})")
            for ns, ids in by_ns.items():
                self._purge_tx(cur, ids, ns, "admin", rule_id="forget_subject")
            cur.execute("DELETE FROM subject_aliases WHERE tenant_id=%s AND (alias = ANY(%s) OR canonical = ANY(%s))",
                        (tenant, sorted(names), sorted(names)))
            self._audit(cur, "admin", "forget_subject", None, detail={"tenant": tenant, "count": len(rows)})
            conn.commit()
        report["purged"] = len(rows)
        return report

    # ---------- sleep (05 §3)

    def sleep_run(self, client: Client, kind: str = "light", dry_run: bool = True) -> dict:
        self._require(client, None, "admin")
        if kind == "light":
            from mem.sleep import LightSleep
            return LightSleep(self).run(dry_run=dry_run)
        if kind == "deep":
            from mem.deep_sleep import DeepSleep
            return DeepSleep(self, llm=getattr(self, "llm", None)).run(dry_run=dry_run)
        raise BadRequest("kind: light | deep")

    def sleep_report(self, client: Client, ns: str, kind: str = "light", job_id: int | None = None) -> dict:
        maxc = self._require(client, ns, "read")
        from mem.sleep import report
        jk = {"light": "light_sleep", "deep": "deep_sleep"}.get(kind)
        if not jk:
            raise BadRequest("kind: light | deep")
        with self.pool.connection() as conn:
            if job_id is not None:
                job = conn.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
            else:
                job = conn.execute("SELECT * FROM jobs WHERE kind=%s AND status <> 'RUNNING' "
                                   "ORDER BY NOT dry_run DESC, started_at DESC LIMIT 1", (jk,)).fetchone()
            conn.rollback()
        if not job:
            raise NotFound("no sleep runs yet")
        return report(self, job, ns, maxc)

    # ---------- service

    def stats(self, client: Client, ns: str) -> dict:
        maxc = self._require(client, ns, "read")
        now = self.clock.now()
        with self.pool.connection() as conn:
            by = conn.execute("SELECT kind, state, count(*) n FROM memories WHERE namespace=%s AND classification <= %s "
                              "GROUP BY 1,2 ORDER BY 1,2", (ns, maxc)).fetchall()
            q = conn.execute(
                f"SELECT percentile_cont(ARRAY[0.25,0.5,0.75]) WITHIN GROUP (ORDER BY ({EFF})) AS q, count(*) n, "
                f"avg(({EFF})) avg FROM memories m JOIN kinds k ON k.id=m.kind "
                "WHERE m.namespace=%(ns)s AND m.state='current' AND m.classification <= %(maxc)s",
                {"ns": ns, "now": now, "maxc": maxc}).fetchone()
            conn.rollback()
        counts: dict = {}
        for r in by:
            counts.setdefault(r["kind"], {})[r["state"]] = r["n"]
        qs = q["q"] or [None, None, None]
        return {"namespace": ns, "counts": counts,
                "strength_current": {"n": q["n"], "q1": qs[0], "median": qs[1], "q3": qs[2],
                                     "avg": float(q["avg"]) if q["avg"] is not None else None}}

    def status(self) -> dict:
        from mem.db import ns_scope
        ns_scope.set("*")                       # public status: only counters across all namespaces
        now = self.clock.now()
        out: dict = {"dbOk": False, "embedDim": self.emb.dim, "modelId": self.emb.model_id,
                     "time": _iso(now), "clockOffsetS": self.clock.offset_seconds()}
        try:
            with self.pool.connection() as conn:
                ns_counts: dict = {}
                for r in conn.execute("SELECT namespace, state, count(*) n FROM memories GROUP BY 1,2").fetchall():
                    ns_counts.setdefault(r["namespace"], {})[r["state"]] = r["n"]
                out["memories"] = ns_counts
                qs = conn.execute(
                    f"SELECT percentile_cont(ARRAY[0.25,0.5,0.75]) WITHIN GROUP (ORDER BY ({EFF})) AS q "
                    "FROM memories m JOIN kinds k ON k.id=m.kind WHERE m.state='current'", {"now": now}).fetchone()["q"]
                out["strengthQuartiles"] = qs
                for kind, key in (("light_sleep", "lastLightSleep"), ("deep_sleep", "lastDeepSleep")):
                    j = conn.execute("SELECT started_at, finished_at, status FROM jobs WHERE kind=%s AND NOT dry_run "
                                     "ORDER BY started_at DESC LIMIT 1", (kind,)).fetchone()
                    out[key] = {"at": _iso(j["started_at"]), "status": j["status"]} if j else None
                out["reviewOpen"] = conn.execute("SELECT count(*) n FROM review_items WHERE status='open'").fetchone()["n"]
                conn.rollback()
            out["dbOk"] = True
        except Exception as e:  # noqa: BLE001
            out["error"] = type(e).__name__
        return out

    def export(self, client: Client, ns: str) -> Iterable[str]:
        """AC-5: export is for the namespace owner (manage) or admin from localhost."""
        maxc = self._require(client, ns, "admin" if client.local_admin else "manage")
        now = self.clock.now()
        with self.pool.connection() as conn:
            rows = conn.execute(f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
                                "WHERE m.namespace=%(ns)s AND m.classification <= %(maxc)s ORDER BY m.created_at, m.id",
                                {"ns": ns, "now": now, "maxc": maxc}).fetchall()
            ev = conn.execute("SELECT e.* FROM evidence e JOIN memories m ON m.id=e.memory_id WHERE m.namespace=%s "
                              "AND m.classification <= %s ORDER BY e.memory_id, e.id", (ns, maxc)).fetchall()
            conn.rollback()
        by: dict = {}
        for e in ev:
            by.setdefault(e["memory_id"], []).append(_evidence_to_dict(e))
        for r in rows:
            m = _row_to_memory(r, r["eff"])
            m["stored_strength"] = float(r["strength"])
            m["evidence"] = by.get(r["id"], [])
            yield json.dumps(m, ensure_ascii=False) + "\n"
