"""Lifecycle constants (spec 05). Change them only together with the tests (L-2)."""

# Initial strength by importance (05 §1)
INITIAL_STRENGTH = {1: 0.35, 2: 0.55, 3: 0.75}
OWNER_BONUS = 0.10
INITIAL_CAP = 0.90

# Reinforcement weights: s <- s + w * (1 - s)
W_EVIDENCE_INDEPENDENT = 0.30
W_EVIDENCE_REPEAT = 0.05
W_OWNER_CONFIRM = 0.50
W_USED = 0.15
W_HELPFUL = 0.25
W_RECALLED_TOP5 = 0.03
SUMMARIZED_FACTOR = 0.5          # s <- s * 0.5 when an episode is folded into a summary

RECALLED_TOP5_MIN_INTERVAL_S = 24 * 3600

# Forgetting (G3)
FORGET_THRESHOLD = 0.08
FORGET_NO_SIGNAL_DAYS = 30
UNARCHIVE_STRENGTH = 0.30

# Supersession
REVERT_WINDOW_DAYS = 30
CANDIDATE_REVIEW_DAYS = 14
CANDIDATE_EXPIRE_DAYS = 30

# Input limits (A-4)
TEXT_MAX = 4000
QUOTE_MAX = 200

OWNER_SPEAKER = "owner"
