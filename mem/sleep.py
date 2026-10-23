"""Light sleep (spec 05 §3): deterministic stages, honest status, idempotency, dry_run.

Each stage is its own transaction: with dry_run it is rolled back and the counters are the same.
The report stores only ids and numbers (no texts, S-5); texts and quotes are filled in when the report is read,
within the reader's rights (08).
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from psycopg.types.json import Jsonb

from mem import alerts
from mem import lifecycle as LC
from mem import safety
from mem.config import lifecycle as C
from mem.service import EFF, MEM_COLUMNS, EvidenceIn, MemService, _iso

log = logging.getLogger("mem.sleep")

# dedup_exact runs before promote: otherwise a duplicate candidate of the same text would replace the original at night
LIGHT_STAGES = ["decay", "dedup_exact", "promote", "forget", "retention", "review_expiry", "maintenance", "export"]
FATAL_STAGES = {"decay", "promote", "forget", "retention", "maintenance"}   # error -> FAILED, the others -> PARTIAL
ACTOR = "sleep:light"
NORM_SQL = "btrim(regexp_replace(lower(translate(m.text, 'Ёё', 'Ее')), '\\s+', ' ', 'g'))"


class Rollback(Exception):
    """Internal signal: roll back the stage transaction in dry_run."""


@dataclass
class StageResult:
    status: str = "OK"          # OK | ERROR | DISABLED
    changed: int = 0
    details: dict | None = None
    error: str | None = None

    def to_json(self) -> dict:
        d = {"status": self.status, "changed": self.changed}
        if self.details:
            d["details"] = self.details
        if self.error:
            d["error"] = self.error
        return d


def job_status(stages: dict[str, StageResult], fatal: set[str] = FATAL_STAGES) -> str:
    errs = {k for k, v in stages.items() if v.status == "ERROR"}
    if errs & fatal:
        return "FAILED"
    if errs:
        return "PARTIAL"
    return "OK"


class LightSleep:
    STAGES = LIGHT_STAGES
    FATAL = FATAL_STAGES
    JOB_KIND = "light_sleep"
    TITLE = "light sleep"
    LOCK_ID = 777001

    def __init__(self, svc: MemService, export_dir: str | None = None,
                 fail_stage: str | None = None):
        self.svc = svc
        self.pool = svc.pool
        self.clock = svc.clock
        self.export_dir = export_dir if export_dir is not None else os.environ.get("MEM_EXPORT_DIR")
        self._fail_stage = fail_stage   # only for the report honesty tests (M-J1)

    # ------------------------------------------------------------------ run

    def run(self, dry_run: bool = False) -> dict:
        """Sleep is a service job over all namespaces: scope '*' (protection item 6)."""
        from mem.db import scope
        with scope("*"):
            return self._run_all(dry_run)

    def _run_all(self, dry_run: bool = False) -> dict:
        now = self.clock.now()
        with self.pool.connection() as conn:
            got = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (self.LOCK_ID,)).fetchone()["ok"]
            if not got:
                conn.rollback()
                return {"status": "SKIPPED", "reason": "sleep is already running"}
            try:
                job_id = conn.execute(
                    "INSERT INTO jobs(started_at, kind, status, dry_run) VALUES (%s,%s,'RUNNING',%s) RETURNING id",
                    (now, self.JOB_KIND, dry_run)).fetchone()["id"]
                conn.commit()
                disabled = self._disabled(conn)
                results: dict[str, StageResult] = {}
                for stage in self.STAGES:
                    if stage in disabled:
                        results[stage] = StageResult(status="DISABLED")
                        continue
                    results[stage] = self._run_stage(stage, dry_run)
                status = job_status(results, self.FATAL)
                stages_json = {k: v.to_json() for k, v in results.items()}
                conn.execute("UPDATE jobs SET finished_at=%s, status=%s, stages=%s WHERE id=%s",
                             (self.clock.now(), status, Jsonb(stages_json), job_id))
                if not dry_run:
                    self.svc._audit(conn.cursor(), f"sleep:{self.JOB_KIND}", "job_run", None,
                                    detail={"job_id": job_id, "status": status,
                                            "changed": {k: v.changed for k, v in results.items()}})
                conn.commit()
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (self.LOCK_ID,))
                conn.commit()
        if status != "OK":
            errs = {k: v.error for k, v in results.items() if v.status == "ERROR"}
            alerts.send(f"mem: {self.TITLE} {status}{' (dry_run)' if dry_run else ''}, job {job_id}. "
                        f"Stage errors: {', '.join(errs)}")
        skipped = sum(int(((v or {}).get("details") or {}).get("skipped_invalid") or 0) for v in stages_json.values())
        if skipped and not dry_run:        # the stage did not fail, but part of the model answer was dropped; this must be visible
            alerts.send(f"mem: {self.TITLE}, job {job_id}: the model returned invalid record numbers, items skipped: "
                        f"{skipped}. The rest was processed.")
        return {"job_id": job_id, "status": status, "dry_run": dry_run, "stages": stages_json}

    def _disabled(self, conn) -> set[str]:
        r = conn.execute("SELECT value FROM meta WHERE key='sleep_disabled_stages'").fetchone()
        return {s.strip() for s in (r["value"] if r else "").split(",") if s.strip()}

    def _run_stage(self, stage: str, dry_run: bool) -> StageResult:
        fn: Callable = getattr(self, f"_stage_{stage}")
        res = StageResult()
        try:
            with self.pool.connection() as conn:
                try:
                    with conn.transaction():
                        if self._fail_stage == stage:
                            raise RuntimeError(f"artificial failure of stage {stage}")
                        res.changed, res.details = fn(conn.cursor())
                        if dry_run:
                            raise Rollback()
                except Rollback:
                    pass
        except Exception as e:  # noqa: BLE001
            log.error("stage %s failed: %s", stage, traceback.format_exc(limit=3))
            res.status, res.error = "ERROR", f"{type(e).__name__}: {e}"[:300]
            res.changed = 0
        return res

    # ------------------------------------------------------------------ stages

    def _stage_decay(self, cur):
        """Records decay: stored strength := current strength if the difference is noticeable (idempotent)."""
        now = self.clock.now()
        n = cur.execute(f"""
            UPDATE memories m SET strength = x.eff, strength_at = %(now)s
            FROM (SELECT m.id, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id = m.kind
                  WHERE m.state IN ('current','candidate','superseded')) x
            WHERE m.id = x.id AND abs(m.strength - x.eff) > 1e-4
        """, {"now": now}).rowcount
        return n, None

    def _stage_promote(self, cur):
        """Gate G1 + expired candidates (05 §2)."""
        now = self.clock.now()
        promoted, reviews, expired = [], [], []
        cands = cur.execute(
            "SELECT id, namespace, slot, occurred_at, created_at, classification, kind, trust FROM memories "
            "WHERE state='candidate' AND slot IS NOT NULL AND duplicate_of IS NULL ORDER BY created_at, id FOR UPDATE"
        ).fetchall()
        for c in cands:
            k = cur.execute("SELECT id, occurred_at, created_at, valid_from, classification FROM memories "
                            "WHERE namespace=%s AND slot=%s AND state='current' FOR UPDATE",
                            (c["namespace"], c["slot"])).fetchone()
            # A rule waits for confirmation (1.6.1: by the owner, 09 TM-11: by the curator): not only an untrusted one but any
            # with an open rule_confirm item; otherwise a team member's rule would take effect at night in an empty slot
            # without the curator
            held = c["kind"] in safety.RULE_KINDS and (c["trust"] == "untrusted" or cur.execute(
                "SELECT 1 FROM review_items WHERE type='rule_confirm' AND status='open' "
                "AND memory_ids @> ARRAY[%s]::uuid[]", (c["id"],)).fetchone() is not None)
            if k is None and not held:   # the slot is free (current archived/deleted) -> the candidate becomes current
                self._promote(cur, c, None, "slot_free", now)
                promoted.append({"ns": c["namespace"], "new": str(c["id"]), "old": None, "reason": "slot_free"})
                continue
            reason = None if (held or k is None) else self.g1(cur, c, k)
            if reason:
                self._promote(cur, c, k, reason, now)
                promoted.append({"ns": c["namespace"], "new": str(c["id"]), "old": str(k["id"]), "reason": reason})
                continue
            age = now - c["created_at"]
            if age >= timedelta(days=C.CANDIDATE_EXPIRE_DAYS):
                row = self.svc._load(cur, c["namespace"], c["id"], lock=True)
                self.svc._archive_tx(cur, row, ACTOR, "rule:candidate-expired", rule_id="candidate-expired")
                cur.execute("UPDATE review_items SET status='expired', decided_at=%s, decided_by=%s "
                            "WHERE status='open' AND type IN ('contradiction','rule_confirm') "
                            "AND memory_ids @> ARRAY[%s]::uuid[]", (now, ACTOR, c["id"]))
                expired.append({"ns": c["namespace"], "id": str(c["id"])})
            elif age >= timedelta(days=C.CANDIDATE_REVIEW_DAYS) and not held:   # a rule has its own rule_confirm item
                exists = cur.execute("SELECT 1 FROM review_items WHERE type='contradiction' "
                                     "AND memory_ids @> ARRAY[%s]::uuid[] AND status='open'", (c["id"],)).fetchone()
                if not exists:
                    if k is None:
                        continue
                    quotes = self._quotes(cur, [k["id"], c["id"]])
                    rid = cur.execute(
                        "INSERT INTO review_items(namespace, created_at, type, memory_ids, proposal, evidence_quotes) "
                        "VALUES (%s,%s,'contradiction',%s,%s,%s) RETURNING id",
                        (c["namespace"], now, [k["id"], c["id"]],
                         Jsonb({"current": str(k["id"]), "candidate": str(c["id"]), "slot": c["slot"],
                                "question": "Which record is right?"}), Jsonb(quotes))).fetchone()["id"]
                    reviews.append({"ns": c["namespace"], "review_id": rid, "current": str(k["id"]),
                                    "candidate": str(c["id"])})
        return len(promoted) + len(reviews) + len(expired), {
            "promoted": promoted, "reviews": reviews, "candidates_expired": expired}

    def g1(self, cur, c: dict, k: dict) -> str | None:
        """Gate G1: None means it does not pass; otherwise the ground: G1b-owner | G1b-independent | G1b-same-speaker."""
        c_time = c["occurred_at"] or c["created_at"]
        k_time = k["occurred_at"] or k["created_at"]
        if c_time < k_time:                                                     # a
            return None
        c_ev = cur.execute("SELECT speaker, independent_key, quote, created_at FROM evidence WHERE memory_id=%s "
                           "ORDER BY created_at, id", (c["id"],)).fetchall()
        k_ev = cur.execute("SELECT speaker, independent_key, created_at FROM evidence WHERE memory_id=%s "
                           "ORDER BY created_at, id", (k["id"],)).fetchall()
        if not c_ev:
            return None
        c_first = c_ev[0]["created_at"]
        if any(e["created_at"] > c_first for e in k_ev):                       # c
            return None
        if any(e["speaker"] == C.OWNER_SPEAKER for e in c_ev):                  # b
            return "G1b-owner"
        if safety.independent_groups([(e["independent_key"], e["quote"]) for e in c_ev]) >= 2:   # 1.6.4
            return "G1b-independent"
        k_speaker = k_ev[0]["speaker"] if k_ev else None
        if k_speaker and any(e["speaker"] == k_speaker for e in c_ev):
            return "G1b-same-speaker"
        return None

    def _promote(self, cur, c: dict, k: dict | None, reason: str, now: datetime) -> None:
        if k is not None:
            cur.execute("UPDATE memories SET state='superseded', valid_to=%s WHERE id=%s", (now, k["id"]))
        cur.execute("UPDATE memories SET state='current', valid_from=%s, supersedes_id=%s WHERE id=%s",
                    (now, k["id"] if k else None, c["id"]))
        self.svc._audit(cur, ACTOR, "promote", c["namespace"], c["id"],
                        detail={"reason": reason, "superseded": str(k["id"]) if k else None})
        if k is not None:
            self.svc._audit(cur, ACTOR, "supersede", c["namespace"], k["id"],
                            detail={"successor": str(c["id"]), "reason": reason})
        cur.execute("UPDATE review_items SET status='accepted', decided_at=%s, decided_by=%s "
                    "WHERE status='open' AND type='contradiction' AND memory_ids @> ARRAY[%s]::uuid[]",
                    (now, f"{ACTOR}:{reason}", c["id"]))

    def _quotes(self, cur, ids: list) -> list[dict]:
        out = []
        for mid in ids:
            e = cur.execute("SELECT memory_id, speaker, quote, source_ref, observed_at FROM evidence WHERE memory_id=%s "
                            "ORDER BY observed_at DESC, id DESC LIMIT 1", (mid,)).fetchone()
            if e:
                out.append({"memory_id": str(e["memory_id"]), "speaker": e["speaker"], "quote": e["quote"],
                            "source_ref": e["source_ref"], "observed_at": e["observed_at"].isoformat()})
        return out

    def _stage_dedup_exact(self, cur):
        """Exact duplicates within namespace + kind + subject + classification + visibility + slot: evidence goes to the kept
        record (the current one, otherwise the oldest). same_chat records merge only if they have the same set of
        evidence chats: otherwise a record of chat A would become visible in chat B together with its quotes."""
        groups = cur.execute(f"""
            SELECT m.namespace, m.kind, coalesce(m.subject, '') AS subj, m.classification, m.visibility,
                   coalesce(m.slot, '') AS slot,
                   CASE WHEN m.visibility = 'same_chat' THEN coalesce((
                       SELECT string_agg(DISTINCT nullif(split_part(e.source_scope, ':', 2), ''), ',')
                       FROM evidence e WHERE e.memory_id = m.id), m.id::text) ELSE '' END AS chats,
                   {NORM_SQL} AS norm,
                   array_agg(m.id ORDER BY (m.state = 'current') DESC, m.created_at, m.id) AS ids
            FROM memories m
            WHERE m.state IN ('current','candidate') AND m.duplicate_of IS NULL
            GROUP BY 1, 2, 3, 4, 5, 6, 7, 8 HAVING count(*) > 1
        """).fetchall()
        merged = []
        for g in groups:
            keep, dups = g["ids"][0], g["ids"][1:]
            for d in dups:
                keep_row = self.svc._load(cur, g["namespace"], keep, lock=True)
                evs = cur.execute("SELECT * FROM evidence WHERE memory_id=%s ORDER BY created_at, id", (d,)).fetchall()
                for e in evs:
                    self.svc._add_evidence_tx(cur, self.svc._load(cur, g["namespace"], keep), EvidenceIn(
                        e["source_ref"], e["source_scope"], e["observed_at"], e["speaker"], e["quote"],
                        e["independent_key"], e.get("speaker_principal")), ACTOR, created_at=e["created_at"])
                cur.execute("UPDATE memories SET duplicate_of=%s WHERE id=%s", (keep, d))
                # open queue items of the duplicate: if the kept record is current, the question is closed (the same already holds),
                # otherwise the item moves to the kept record, so accepting it cannot revive a hidden duplicate
                if keep_row["state"] == "current":
                    cur.execute("UPDATE review_items SET status='expired', decided_at=%s, decided_by=%s WHERE "
                                "status='open' AND type IN ('rule_confirm','contradiction') AND memory_ids @> ARRAY[%s]::uuid[]",
                                (self.clock.now(), ACTOR, d))
                cur.execute("UPDATE review_items SET memory_ids = array_replace(memory_ids, %s::uuid, %s::uuid) "
                            "WHERE status='open' AND memory_ids @> ARRAY[%s]::uuid[]", (d, keep, d))
                self.svc._audit(cur, ACTOR, "mark_duplicate", g["namespace"], d, detail={"duplicate_of": str(keep)})
                merged.append({"ns": g["namespace"], "kept": str(keep_row["id"]), "duplicate": str(d)})
        return len(merged), {"merged": merged}

    def _stage_forget(self, cur):
        """Gate G3: archive by strength."""
        now = self.clock.now()
        rows = cur.execute(f"""
            SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id = m.kind
            WHERE m.state = 'current' AND m.duplicate_of IS NULL
              AND k.forgettable                                                   -- a
              AND NOT m.pinned                                                    -- b
              AND ({EFF}) < %(thr)s                                               -- c
              AND NOT EXISTS (SELECT 1 FROM memory_events e WHERE e.memory_id = m.id
                              AND e.type IN ('used','helpful') AND e.at > %(since)s)   -- d
              AND NOT (m.slot IS NOT NULL AND EXISTS (                            -- e
                  SELECT 1 FROM review_items r WHERE r.status = 'open' AND r.memory_ids @> ARRAY[m.id]))
            ORDER BY m.namespace, m.created_at
            FOR UPDATE OF m
        """, {"now": now, "thr": C.FORGET_THRESHOLD,
              "since": now - timedelta(days=C.FORGET_NO_SIGNAL_DAYS)}).fetchall()
        by_kind: dict = {}
        for r in rows:
            self.svc._archive_tx(cur, r, ACTOR, "strength", rule_id="G3")
            by_kind.setdefault(r["namespace"], {}).setdefault(r["kind"], []).append(str(r["id"]))
        return len(rows), {"archived": by_kind}

    def _stage_retention(self, cur):
        now = self.clock.now()
        rules = cur.execute("SELECT * FROM retention_rules WHERE enabled ORDER BY id").fetchall()
        applied = {}
        for rule in rules:
            where = ["(%(ns)s = '*' OR m.namespace = %(ns)s)", "(%(kind)s = '*' OR m.kind = %(kind)s)"]
            if rule["condition"] == "archived_older_than":
                where += ["m.state = 'archived'", "m.archived_at < %(cut)s"]
            else:
                where += ["m.state = 'superseded'", "m.valid_to < %(cut)s"]
            params = {"ns": rule["namespace"], "kind": rule["kind"], "cut": now - timedelta(days=rule["after_days"]),
                      "now": now}
            rows = cur.execute(f"SELECT {MEM_COLUMNS}, ({EFF}) AS eff FROM memories m JOIN kinds k ON k.id=m.kind "
                               f"WHERE {' AND '.join(where)} FOR UPDATE OF m", params).fetchall()
            if not rows:
                continue
            if rule["action"] == "purge":
                for ns in {r["namespace"] for r in rows}:
                    self.svc._purge_tx(cur, [r["id"] for r in rows if r["namespace"] == ns], ns, ACTOR, rule_id=rule["id"])
            else:
                for r in rows:
                    self.svc._archive_tx(cur, r, ACTOR, f"rule:{rule['id']}", rule_id=rule["id"])
            applied[rule["id"]] = len(rows)
        # 09, TM-27: the personal layer of a member who left is erased when its term ends
        due = cur.execute("SELECT id FROM namespaces WHERE scope = 'personal' AND settings ? 'purge_after' "
                          "AND NOT settings ? 'purged_at' AND (settings->>'purge_after')::timestamptz <= %s",
                          (now,)).fetchall()
        for n in due:
            ids = [r["id"] for r in cur.execute("SELECT id FROM memories WHERE namespace=%s", (n["id"],)).fetchall()]
            if ids:
                self.svc._purge_tx(cur, ids, n["id"], ACTOR, rule_id="offboard")
            cur.execute("DELETE FROM review_items WHERE namespace=%s", (n["id"],))
            cur.execute("UPDATE namespaces SET settings = settings || %s WHERE id=%s",
                        (Jsonb({"purged_at": now.isoformat()}), n["id"]))
            applied[f"offboard:{n['id']}"] = len(ids)
        return sum(applied.values()), {"rules": applied}

    def _stage_review_expiry(self, cur):
        now = self.clock.now()
        n = cur.execute("UPDATE review_items SET status='expired', decided_at=%s, decided_by=%s "
                        "WHERE status='open' AND created_at < %s",
                        (now, ACTOR, now - timedelta(days=C.CANDIDATE_EXPIRE_DAYS))).rowcount
        return n, None

    def _stage_maintenance(self, cur):
        """Checks invariants D-2, D-3, D-5, D-6. A violation -> stage error -> FAILED."""
        checks = {
            "D-2 one current per slot": "SELECT count(*) n FROM (SELECT namespace, slot FROM memories "
                                        "WHERE state='current' AND slot IS NOT NULL GROUP BY 1,2 HAVING count(*)>1) x",
            "D-3 superseded has valid_to": "SELECT count(*) n FROM memories WHERE state='superseded' AND valid_to IS NULL",
            "D-5 every record has evidence": "SELECT count(*) n FROM memories m WHERE NOT EXISTS "
                                                "(SELECT 1 FROM evidence e WHERE e.memory_id=m.id)",
            "D-6 strength in [0,1]": "SELECT count(*) n FROM memories WHERE strength < 0 OR strength > 1",
        }
        bad = {name: cur.execute(sql).fetchone()["n"] for name, sql in checks.items()}
        bad = {k: v for k, v in bad.items() if v}
        if bad:
            raise AssertionError(f"invariants violated: {bad}")
        return 0, {"invariants": "ok"}

    def _stage_export(self, cur):
        if not self.export_dir:
            return 0, {"skipped": "MEM_EXPORT_DIR is not set"}
        d = Path(self.export_dir)
        d.mkdir(parents=True, exist_ok=True)
        stamp = self.clock.now().strftime("%Y%m%d")
        total = 0
        for ns in [r["id"] for r in cur.execute("SELECT id FROM namespaces ORDER BY id").fetchall()]:
            path = d / f"{ns}-{stamp}.jsonl"
            rows = cur.execute("SELECT m.id, m.namespace, m.kind, m.text, m.subject, m.slot, m.tags, m.importance, "
                               "m.state, m.classification, m.visibility, m.created_at, m.occurred_at, m.strength, "
                               "(SELECT json_agg(json_build_object('source_ref', e.source_ref, 'speaker', e.speaker, "
                               "'quote', e.quote, 'observed_at', e.observed_at)) FROM evidence e WHERE e.memory_id=m.id) ev "
                               "FROM memories m WHERE m.namespace=%s ORDER BY m.created_at", (ns,)).fetchall()
            with path.open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps({k: (v.isoformat() if hasattr(v, "isoformat") else
                                            str(v) if k == "id" else v) for k, v in r.items()}, ensure_ascii=False) + "\n")
            total += len(rows)
            for old in sorted(d.glob(f"{ns}-*.jsonl"))[:-14]:   # keep the 14 latest
                old.unlink()
        return 0, {"exported_rows": total}


# ---------------------------------------------------------------------- report (05 "Cleanup report")

def report(svc: MemService, job: dict, ns: str, maxc: int) -> dict:
    """Run report for namespace ns: texts and quotes are filled in now, only within the reader's clearance."""
    stages = job["stages"] or {}

    def mem(cur, mid):
        r = cur.execute("SELECT id, kind, text, slot, state, classification FROM memories "
                        "WHERE id=%s AND namespace=%s AND classification <= %s", (mid, ns, maxc)).fetchone()
        if not r:
            return None
        q = cur.execute("SELECT speaker, quote, observed_at, source_ref FROM evidence WHERE memory_id=%s "
                        "ORDER BY observed_at DESC, id DESC LIMIT 1", (mid,)).fetchone()
        return {"id": str(r["id"]), "kind": r["kind"], "text": r["text"], "slot": r["slot"], "state": r["state"],
                "quote": dict(q, observed_at=_iso(q["observed_at"])) if q else None}

    out = {"job_id": job["id"], "kind": job["kind"], "status": job["status"], "dry_run": job["dry_run"],
           "started_at": _iso(job["started_at"]), "finished_at": _iso(job["finished_at"]),
           "stages": {k: {kk: vv for kk, vv in v.items() if kk != "details"} for k, v in stages.items()}}
    with svc.pool.connection() as conn:
        cur = conn.cursor()
        pr = (stages.get("promote") or {}).get("details") or {}
        out["replaced"] = [x for x in (
            {"reason": p["reason"], "old": mem(cur, p["old"]) if p["old"] else None, "new": mem(cur, p["new"])}
            for p in pr.get("promoted", []) if p["ns"] == ns) if x["new"]]
        fg = ((stages.get("forget") or {}).get("details") or {}).get("archived", {}).get(ns, {})
        examples = [m for m in (mem(cur, i) for ids in fg.values() for i in ids[:10]) if m][:10]
        out["forgotten"] = {"by_kind": {k: len(v) for k, v in fg.items()}, "examples": examples}
        dd = ((stages.get("dedup_exact") or {}).get("details") or {}).get("merged", [])
        out["duplicates_merged"] = len([d for d in dd if d["ns"] == ns])
        out["candidates_expired"] = len([c for c in pr.get("candidates_expired", []) if c["ns"] == ns])
        items = cur.execute("SELECT id, created_at, memory_ids, proposal, evidence_quotes FROM review_items "
                            "WHERE namespace=%s AND status='open' AND type='contradiction' ORDER BY created_at", (ns,)).fetchall()
        contradictions = []
        for it in items:
            sides = [mem(cur, m) for m in it["memory_ids"]]
            if all(sides):
                contradictions.append({"review_id": it["id"], "created_at": _iso(it["created_at"]), "sides": sides})
        out["contradictions_open"] = contradictions
        if job["kind"] == "deep_sleep":
            det = lambda st: (stages.get(st) or {}).get("details") or {}  # noqa: E731
            out["slot_suggestions"] = [x for x in det("slot_suggestions").get("reviews", []) if x["ns"] == ns]
            out["model_items_skipped"] = sum(int(det(st).get("skipped_invalid") or 0)
                                             for st in ("contradictions", "slot_suggestions"))
            out["stale"] = [x for x in ({"review_id": r["review_id"], "record": mem(cur, r["memory_id"])}
                                        for r in det("stale").get("reviews", []) if r["ns"] == ns) if x["record"]]
            sm = det("summaries")
            out["summaries_applied"] = [x for x in ({"summary": mem(cur, a["id"]) if a.get("id") else None,
                                                      "sources": len(a["sources"]), "dry_run": a.get("dry_run", False)}
                                                     for a in sm.get("applied", []) if a["ns"] == ns)]
            from mem.gates import G2_REASONS
            out["summaries_rejected"] = [{"review_id": r["review_id"], "reasons": [G2_REASONS[f] for f in r["g2_failed"]]}
                                         for r in sm.get("rejected", []) if r["ns"] == ns]
        conn.rollback()
    return out
