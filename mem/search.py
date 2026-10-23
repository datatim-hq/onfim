"""Ranking formula (spec 04). Pure functions, for determinism and tests (R-1, R-2)."""
from __future__ import annotations

from dataclasses import dataclass

W_SEM = 0.50
W_LEX = 0.20
W_STRENGTH = 0.20
W_PRIOR = 0.10
CANDIDATES_PER_CHANNEL = 50


@dataclass
class Candidate:
    id: str
    sem_raw: float | None      # cosine similarity, or None if the record did not come from the vector channel
    lex_raw: float             # ts_rank_cd, 0 if nothing matched
    strength: float
    rank_weight: float
    importance: int
    created_at_ts: float


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [1.0 if hi > 0 else 0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def score(cands: list[Candidate]) -> list[dict]:
    """Returns [{id, score, sem, lex, strength, prior}] in result order."""
    if not cands:
        return []
    sem_vals = [c.sem_raw if c.sem_raw is not None else 0.0 for c in cands]
    sem_n = _minmax(sem_vals)
    lex_pos = [c.lex_raw for c in cands]
    lex_n = _minmax(lex_pos) if any(v > 0 for v in lex_pos) else [0.0] * len(cands)
    lex_n = [0.0 if c.lex_raw <= 0 else v for c, v in zip(cands, lex_n)]
    rows = []
    for c, sem, lex in zip(cands, sem_n, lex_n):
        prior = c.rank_weight * (c.importance / 3.0)
        total = W_SEM * sem + W_LEX * lex + W_STRENGTH * c.strength + W_PRIOR * prior
        rows.append({"id": c.id, "score": round(total, 6), "sem": round(sem, 6), "lex": round(lex, 6),
                     "strength": round(c.strength, 6), "prior": round(prior, 6), "_ts": c.created_at_ts})
    rows.sort(key=lambda r: (-r["score"], -r["_ts"], r["id"]))
    for r in rows:
        r.pop("_ts")
    return rows
