import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from mem import lifecycle as LC
from mem.config import lifecycle as C

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("imp,owner,exp", [(1, False, .35), (2, False, .55), (3, False, .75),
                                           (1, True, .45), (2, True, .65), (3, True, .85)])
def test_initial_strength(imp, owner, exp):
    """Initial strength depends on importance (0.35 / 0.55 / 0.75); the owner's words add 0.10."""
    assert LC.initial_strength(imp, owner) == pytest.approx(exp)


def test_initial_cap():
    """Initial strength stays under the 0.90 ceiling even with importance 3 from the owner's words."""
    assert LC.initial_strength(3, True) <= C.INITIAL_CAP


def test_reinforce_saturates_and_never_exceeds_1():
    """Reinforcements only raise strength towards 1 and never let it exceed 1."""
    s = 0.0
    for _ in range(200):
        s2 = LC.reinforce(s, C.W_OWNER_CONFIRM)
        assert s <= s2 <= 1.0
        s = s2
    assert s == pytest.approx(1.0, abs=1e-9)


def test_decay_half_life():
    """One half-life (30 days) halves the strength exactly: 0.8 -> 0.4."""
    assert LC.decayed(0.8, T0, T0 + timedelta(days=30), 30, False) == pytest.approx(0.4)


def test_decay_never_increases_property():  # D-6
    """D-6: decay never raises strength and never takes it below zero.
    Checked on 2000 random cases, including a clock shifted back and frozen records."""
    rnd = random.Random(7)
    for _ in range(2000):
        s = rnd.random()
        h = rnd.choice([None, 14, 30, 90, 180, 365])
        dt = rnd.uniform(-400, 4000)
        v = LC.decayed(s, T0, T0 + timedelta(days=dt), h, rnd.random() < 0.2)
        assert 0 <= v <= s + 1e-12


def test_frozen_and_no_half_life():
    """Kinds without a half-life and frozen records do not decay.
    Frozen means pinned records and open commitment mirrors; a closed mirror decays."""
    assert LC.decayed(0.5, T0, T0 + timedelta(days=999), None, False) == 0.5
    assert LC.decayed(0.5, T0, T0 + timedelta(days=999), 30, True) == 0.5
    assert LC.is_frozen("commitment_mirror", False, None)
    assert not LC.is_frozen("commitment_mirror", False, T0)
    assert LC.is_frozen("note", True, None)


@pytest.mark.parametrize("kind,h,days", [("episode", 30, 83), ("note", 90, 250), ("insight", 120, 335)])
def test_forget_horizon_examples(kind, h, days):
    """05 §1, table of examples: importance 2 without reinforcement drops below 0.08 after about N days."""
    s0 = LC.initial_strength(2, False)
    t = h * math.log2(s0 / C.FORGET_THRESHOLD)
    assert abs(t - days) <= 3


def test_repeat_is_weaker_than_independent():  # M-S3
    """M-S3: five repeats from one source raise strength less than one independent piece of evidence."""
    base = LC.initial_strength(2, False)
    s = base
    for _ in range(5):
        s = LC.reinforce(s, C.W_EVIDENCE_REPEAT)
    assert s - base < LC.reinforce(base, C.W_EVIDENCE_INDEPENDENT) - base
