"""Pure functions of memory strength (spec 05 §1). No database, covered by unit tests."""
from __future__ import annotations

from datetime import datetime

from mem.config import lifecycle as C


def initial_strength(importance: int, owner_evidence: bool) -> float:
    s = C.INITIAL_STRENGTH[importance] + (C.OWNER_BONUS if owner_evidence else 0.0)
    return min(s, C.INITIAL_CAP)


def reinforce(s: float, w: float) -> float:
    """Saturates towards 1 without overshoot: s + w(1-s)."""
    if not 0.0 <= w <= 1.0:
        raise ValueError("reinforcement weight outside [0,1]")
    return min(1.0, s + w * (1.0 - s))


MIRROR_KINDS = frozenset({"commitment_mirror", "crm_mirror"})


def decayed(s: float, since: datetime, now: datetime, half_life_days: int | None, frozen: bool) -> float:
    """Strength at time now. Never above s (D-6): if now < since (clock shifted back), s."""
    if frozen or half_life_days is None:
        return s
    dt_days = (now - since).total_seconds() / 86400.0
    if dt_days <= 0:
        return s
    return s * 2.0 ** (-dt_days / half_life_days)


def is_frozen(kind: str, pinned: bool, external_closed_at: datetime | None) -> bool:
    """Do not decay: pinned records and open mirrors of external objects, commitments and CRM entities (09, TM-32);
    kinds without H are handled by decayed."""
    if pinned:
        return True
    if kind in MIRROR_KINDS and external_closed_at is None:
        return True
    return False


# SQL expression of the same formula for sorting/filtering in the database. Parameter %(now)s is service time.
# m is the alias of memories, k the alias of kinds.
EFFECTIVE_STRENGTH_SQL = """
CASE
  WHEN m.pinned OR k.half_life_days IS NULL
       OR (m.kind IN ('commitment_mirror', 'crm_mirror') AND m.external_closed_at IS NULL)
       OR %(now)s <= m.strength_at
  THEN m.strength
  ELSE m.strength * power(2.0, -(extract(epoch FROM (%(now)s - m.strength_at)) / 86400.0) / k.half_life_days)
END
"""
