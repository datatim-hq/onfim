"""Deep sleep, the "cleanup" (05 §3): once a week. The LLM only proposes; the only automatic changes are episode
summaries that pass gate G2. Everything else becomes review_items for the owner.

Records with classification = 3 and visibility = owner_only never go to an external LLM (S-6, 08); one batch is
the records of one subject in one namespace.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from psycopg.types.json import Jsonb

from mem import lifecycle as LC
from mem.config import lifecycle as C
from mem.gates import G2_REASONS, g2
from mem.llm import LLM, LLMError, RoutedLLM
from mem.service import Client, _evidence_to_dict
from mem.sleep import LightSleep

log = logging.getLogger("mem.deep_sleep")

ACTOR = "sleep:deep"
MAX_SUBJECTS = 40
MAX_RECORDS = 25
VIS_ORDER = {"shareable": 0, "same_chat": 1, "owner_only": 2}
ELIGIBLE = ("m.state = 'current' AND m.duplicate_of IS NULL AND m.classification < 3 "
            "AND m.visibility <> 'owner_only'")

SYSTEM = ("You help maintain a long-term memory. Answer only through the provided tool. "
          "Do not invent anything: rely only on the texts and quotes of the records. If in doubt, do not propose.")

TOOL_CONTRA = {
    "name": "report_contradictions",
    "description": "Pairs of records that cannot both be true (they contradict each other).",
    "input_schema": {"type": "object", "required": ["pairs"], "properties": {"pairs": {"type": "array", "items": {
        "type": "object", "required": ["a", "b", "explanation"], "properties": {
            "a": {"type": "integer"}, "b": {"type": "integer"},
            "explanation": {"type": "string"},
            "attribute": {"type": ["string", "null"]}}}}}}}
TOOL_SLOTS = {
    "name": "suggest_slots",
    "description": "Groups of records that describe THE SAME attribute (whose value may change).",
    "input_schema": {"type": "object", "required": ["groups"], "properties": {"groups": {"type": "array", "items": {
        "type": "object", "required": ["attribute", "indices"], "properties": {
            "attribute": {"type": "string"}, "indices": {"type": "array", "items": {"type": "integer"}}}}}}}}
TOOL_SUMMARY = {
    "name": "write_summary",
    "description": "A short summary of episodes.",
    "input_schema": {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}}


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9а-я_]+", "_", s.lower().replace("ё", "е")).strip("_")[:40] or "attr"


class DeepSleep(LightSleep):
    STAGES = ["contradictions", "slot_suggestions", "stale", "summaries"]
    FATAL: set[str] = set()          # every deep-sleep stage -> PARTIAL on error (05)
    JOB_KIND = "deep_sleep"
    TITLE = "deep sleep"
    LOCK_ID = 777002

    def __init__(self, svc, llm: LLM | None = None, fail_stage: str | None = None):
        super().__init__(svc, export_dir="", fail_stage=fail_stage)
        self._llm = llm
        # models are explicit only: a stage whose model is not set is disabled, never sent to a default provider
        self.model_reason = (os.environ.get("MEM_DEEP_MODEL_REASON") or "").strip()
        self.model_summary = (os.environ.get("MEM_DEEP_MODEL_SUMMARY") or "").strip()
        self._dry = False
        self.sys_client = Client("sleep:deep", ["*"], ["read", "write", "review", "forget", "admin"], True,
                                 "admin", None, {"id": "admin", "tenant_id": "system", "clearance": 3, "active": True})

    @property
    def llm(self) -> LLM:
        if self._llm is None:                 # the route is the prefix in the model name (mem/llm.py)
            llm = RoutedLLM()
            llm.check(*(m for m in (self.model_reason, self.model_summary) if m))   # only the models that are set
            self._llm = llm
        return self._llm

    MODEL_STAGES = {"contradictions": "model_reason", "slot_suggestions": "model_reason", "summaries": "model_summary"}

    def _disabled(self, conn) -> set[str]:
        """Stages switched off by hand, plus the stages whose model is not set (a model injected by tests counts as set)."""
        off = super()._disabled(conn)
        if self._llm is None:
            off |= {st for st, attr in self.MODEL_STAGES.items() if not getattr(self, attr)}
        return off

    def run(self, dry_run: bool = False) -> dict:          # scope '*' is set by LightSleep.run (protection item 6)
        self._dry = dry_run
        return super().run(dry_run)

    # ------------------------------------------------------------------ shared helpers

    def _groups(self, cur, extra_where: str = "", params: dict | None = None):
        """(ns, subject) -> records; only those allowed to be sent to the LLM."""
        rows = cur.execute(f"""
            SELECT m.id, m.namespace, m.subject, m.slot, m.kind, m.text, m.occurred_at, m.created_at
            FROM memories m
            WHERE {ELIGIBLE} AND m.subject IS NOT NULL {extra_where}
            ORDER BY m.namespace, m.subject, coalesce(m.occurred_at, m.created_at) DESC, m.id
        """, params or {}).fetchall()
        groups: dict = {}
        for r in rows:
            g = groups.setdefault((r["namespace"], r["subject"]), [])
            if len(g) < MAX_RECORDS:
                g.append(r)
        return [(k, v) for k, v in groups.items() if len(v) >= 2][:MAX_SUBJECTS]

    def _render(self, cur, recs: list) -> str:
        lines = []
        for i, r in enumerate(recs):
            q = cur.execute("SELECT speaker, quote, observed_at FROM evidence WHERE memory_id=%s AND quote IS NOT NULL "
                            "ORDER BY observed_at DESC LIMIT 2", (r["id"],)).fetchall()
            when = (r["occurred_at"] or r["created_at"]).date().isoformat()
            quotes = "; ".join(f"\"{e['quote']}\" ({e['speaker'] or '?'}, {e['observed_at'].date().isoformat()})" for e in q)
            lines.append(f"[{i}] ({r['kind']}, {when}) {r['text']}" + (f"\n    quotes: {quotes}" if quotes else ""))
        return "\n".join(lines)

    def _seen(self, cur, type_: str, ids: list) -> bool:
        """The same set of records was already proposed (in any status): do not repeat (idempotency)."""
        return bool(cur.execute("SELECT 1 FROM review_items WHERE type=%s AND memory_ids @> %s::uuid[] "
                                "AND memory_ids <@ %s::uuid[]", (type_, list(ids), list(ids))).fetchone())

    def _review(self, cur, ns: str, type_: str, ids: list, proposal: dict) -> int:
        return cur.execute(
            "INSERT INTO review_items(namespace, created_at, type, memory_ids, proposal, evidence_quotes) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (ns, self.clock.now(), type_, list(ids), Jsonb(proposal), Jsonb(self._quotes(cur, ids)))).fetchone()["id"]

    # ------------------------------------------------------------------ stages

    def _stage_contradictions(self, cur):
        self.llm                    # the model setup is checked even without data: the error shows in the report
        created, skipped = [], 0
        for (ns, subject), recs in self._groups(cur):
            out = self.llm.call(self.model_reason, SYSTEM,
                                f"Subject: {subject}. Memory records:\n{self._render(cur, recs)}\n\n"
                                "Find pairs of records that CANNOT both be true (they contradict: different "
                                "values of the same attribute at the same time). Different facts about one "
                                "person or different commitments are not a contradiction. If there are none, return an empty list.",
                                TOOL_CONTRA)
            for p in out["pairs"]:
                a, b = p["a"], p["b"]
                if not (0 <= a < len(recs) and 0 <= b < len(recs)) or a == b:
                    # one invalid item does not break the stage: the other pairs and subjects are processed
                    log.warning("deep sleep: pair with invalid indices skipped (%s, %s; %s records)", a, b, len(recs))
                    skipped += 1
                    continue
                ids = sorted({recs[a]["id"], recs[b]["id"]}, key=str)
                if self._seen(cur, "contradiction", ids):
                    continue
                attr = p.get("attribute")
                prop = {"subject": subject, "explanation": p["explanation"][:500], "question": "Which record is right?"}
                if attr and not (recs[a]["slot"] or recs[b]["slot"]):
                    prop["suggested_slot"] = f"{subject}#{_slug(attr)}"
                rid = self._review(cur, ns, "contradiction", ids, prop)
                created.append({"ns": ns, "review_id": rid, "memory_ids": [str(i) for i in ids]})
        return len(created), {"reviews": created, "skipped_invalid": skipped}

    def _stage_slot_suggestions(self, cur):
        self.llm                    # the model setup is checked even without data: the error shows in the report
        created, skipped = [], 0
        # episodes and commitment mirrors are events, not an attribute with a changing value: no slots for them
        # (a real run once proposed a "delivery delay" slot for four episodes)
        for (ns, subject), recs in self._groups(cur, "AND m.slot IS NULL AND m.kind NOT IN "
                                                     "('episode', 'commitment_mirror', 'crm_mirror', 'insight')"):
            out = self.llm.call(self.model_reason, SYSTEM,
                                f"Subject: {subject}. Memory records:\n{self._render(cur, recs)}\n\n"
                                "Find groups of 2+ records that describe ONE attribute whose value changes "
                                "over time (for example, a meeting time, a person's role, the chosen tool). "
                                "attribute is a short name of the attribute. If there are no such groups, return an empty list.", TOOL_SLOTS)
            for g in out["groups"]:
                idx = sorted(set(g["indices"]))
                if len(idx) < 2 or any(not 0 <= i < len(recs) for i in idx):
                    # one group with invalid numbers used to break the stage for all subjects; now it is skipped
                    log.warning("deep sleep: group with invalid indices skipped (%s; %s records)", idx, len(recs))
                    skipped += 1
                    continue
                ids = sorted({recs[i]["id"] for i in idx}, key=str)
                if self._seen(cur, "slot_suggestion", ids) or self._seen(cur, "contradiction", ids):
                    continue
                slot = f"{subject}#{_slug(g['attribute'])}"
                rid = self._review(cur, ns, "slot_suggestion", ids, {"slot": slot, "subject": subject})
                created.append({"ns": ns, "review_id": rid, "slot": slot})
        return len(created), {"reviews": created, "skipped_invalid": skipped}

    def _stage_stale(self, cur):
        """Deterministic: kinds with a review_below threshold (gotcha) whose strength is below it -> "stale?"."""
        now = self.clock.now()
        rows = cur.execute(f"""
            SELECT m.id, m.namespace FROM memories m JOIN kinds k ON k.id = m.kind
            WHERE m.state = 'current' AND m.duplicate_of IS NULL AND k.review_below IS NOT NULL
              AND ({LC.EFFECTIVE_STRENGTH_SQL}) < k.review_below
            ORDER BY m.namespace, m.created_at
        """, {"now": now}).fetchall()
        created = []
        for r in rows:
            recent = cur.execute("SELECT 1 FROM review_items WHERE type='stale' AND memory_ids @> ARRAY[%s]::uuid[] "
                                 "AND (status='open' OR decided_at > %s)", (r["id"], now - timedelta(days=90))).fetchone()
            if recent:
                continue
            rid = self._review(cur, r["namespace"], "stale", [r["id"]],
                               {"question": "Is this gotcha still relevant? Yes: confirm it; no: archive it."})
            created.append({"ns": r["namespace"], "review_id": rid, "memory_id": str(r["id"])})
        return len(created), {"reviews": created}

    def _stage_summaries(self, cur):
        self.llm                    # the model setup is checked even without data: the error shows in the report
        now = self.clock.now()
        rows = cur.execute(f"""
            SELECT m.id, m.namespace, m.subject, m.kind, m.text, m.tags, m.visibility, m.classification,
                   coalesce(m.occurred_at, m.created_at) AS t
            FROM memories m
            WHERE {ELIGIBLE} AND m.kind = 'episode' AND m.subject IS NOT NULL
              AND coalesce(m.occurred_at, m.created_at) < %(cut)s
              AND NOT EXISTS (SELECT 1 FROM memory_events e WHERE e.memory_id = m.id AND e.type = 'summarized_into')
            ORDER BY m.namespace, m.subject, t
        """, {"cut": now - timedelta(days=30)}).fetchall()
        groups: dict = {}
        for r in rows:
            groups.setdefault((r["namespace"], r["subject"], r["t"].strftime("%Y-%m")), []).append(r)
        applied, rejected = [], []
        for (ns, subject, month), recs in list(groups.items())[:MAX_SUBJECTS]:
            if len(recs) < 2:
                continue
            ids = [r["id"] for r in recs]
            if self._seen(cur, "merge_suggestion", ids):
                continue
            sources = []
            for r in recs:
                ev = cur.execute("SELECT * FROM evidence WHERE memory_id=%s ORDER BY id", (r["id"],)).fetchall()
                sources.append({"kind": r["kind"], "text": r["text"], "quotes": [e["quote"] for e in ev if e["quote"]],
                                "evidence": [_evidence_to_dict(e) for e in ev], "row": r})
            listing = "\n".join(f"- {s['text']}" for s in sources)
            out = self.llm.call(self.model_summary, SYSTEM,
                                f"Episodes on {subject} for {month}:\n{listing}\n\nWrite a short summary (1-3 "
                                "sentences) in the same language as the episodes. Copy all dates, numbers and names EXACTLY as in the text "
                                "(for example, do not turn \"30.09\" into \"by October 1\"). Do not add anything that is not in the episodes.",
                                TOOL_SUMMARY)
            summary = out["summary"].strip()
            if not summary:
                raise LLMError("empty summary")
            fails = g2(summary, sources)
            if fails:
                rid = self._review(cur, ns, "merge_suggestion", ids, {
                    "summary": summary, "g2_failed": fails, "reasons": [G2_REASONS[f] for f in fails],
                    "subject": subject, "month": month})
                rejected.append({"ns": ns, "review_id": rid, "g2_failed": fails, "sources": [str(i) for i in ids]})
                continue
            if self._dry:
                applied.append({"ns": ns, "id": None, "sources": [str(i) for i in ids], "dry_run": True})
                continue
            mid = self._apply_summary(cur, ns, subject, summary, sources)
            applied.append({"ns": ns, "id": mid, "sources": [str(i) for i in ids]})
        return len(applied) + len(rejected), {"applied": applied, "rejected": rejected}

    def _apply_summary(self, cur, ns: str, subject: str, summary: str, sources: list) -> str:
        """Creates the summary (insight, source=sleep:deep, evidence = the union), and sets summarized_into on the sources."""
        evid, seen = [], set()
        for s in sources:
            for e in s["evidence"]:
                if e["source_ref"] not in seen:
                    seen.add(e["source_ref"])
                    evid.append({k: e[k] for k in ("source_ref", "source_scope", "speaker", "quote", "observed_at",
                                                    "independent_key") if e.get(k) is not None})
        rows = [s["row"] for s in sources]
        rec = self.svc.remember(self.sys_client, ns, {
            "text": summary, "kind": "insight", "subject": subject, "source": ACTOR, "importance": 2,
            "tags": sorted({t for r in rows for t in (r["tags"] or [])}),
            "visibility": max((r["visibility"] for r in rows), key=lambda v: VIS_ORDER[v]),
            "classification": max(r["classification"] for r in rows),
            "occurred_at": max(r["t"] for r in rows).isoformat(), "evidence": evid})
        for r in rows:
            row = self.svc._load(cur, ns, r["id"], lock=True)
            before = float(row["eff"])
            after = before * C.SUMMARIZED_FACTOR
            self.svc._set_strength(cur, r["id"], after, reinforced=False)
            self.svc._event(cur, r["id"], "summarized_into", after - before, ACTOR)
            self.svc._audit(cur, ACTOR, "reinforce", ns, r["id"], detail={"event": "summarized_into", "summary": rec["id"]})
        return rec["id"]
