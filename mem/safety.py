"""Stage 1.6: protecting memory from poisoning (ideas from Cordon). Fully deterministic, no model:
the code decides by origin and the shape of the text, not by its meaning.

1.6.1 trust: a record's trust comes from its evidence (scope and reference), not from the text.
1.6.2 sanitize: hidden text is cut out (invisible characters, bidi, Unicode tags, ANSI), suspicious text is flagged.
1.6.3 find_secret: keys and tokens are never stored in memory.
1.6.4 kin: related texts (forwards, copy-paste) do not count as independent confirmations.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
import unicodedata

# ---------------------------------------------------------------- 1.6.1 trust

TRUSTED_SCOPES = tuple(s.strip() for s in os.environ.get(
    "MEM_TRUSTED_SCOPES", "owner,dev,session,repo,file,import,cowork,code,claude,crm").split(",") if s.strip())
RULE_KINDS = frozenset({"decision", "convention", "gotcha"})   # what agents read as rules


def evidence_trusted(source_scope: str, source_ref: str, speaker: str | None, owner_speaker: str) -> bool:
    """Trusted evidence: from the owner or from the trusted client's own work.
    Everything external (web, other people's chats, mail, outside documents) is untrusted, whatever the scope is called."""
    if speaker == owner_speaker:
        return True
    ref = (source_ref or "").strip().lower()
    if ref.startswith(("http://", "https://", "www.")):
        return False
    scope = (source_scope or "").split(":", 1)[0].strip().lower()
    return scope in TRUSTED_SCOPES


# ---------------------------------------------------------------- 1.6.2 hidden text

_INVISIBLE = re.compile(
    "[­͏؜ᅟᅠ឴឵᠎​-‏‪-‮⁠-⁤⁦-⁯"
    "ㅤ﻿ﾠ⠀\U000e0000-\U000e007f]")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_B64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")
_HEX = re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{2}){24,}(?![0-9a-fA-F])")


def _script(ch: str) -> str | None:
    name = unicodedata.name(ch, "")
    for s in ("CYRILLIC", "LATIN", "GREEK"):
        if name.startswith(s):
            return s
    return None


def _readable(b: bytes) -> bool:
    try:
        t = b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    letters = sum(ch.isalpha() or ch.isspace() for ch in t)
    return len(t) >= 12 and letters / max(1, len(t)) > 0.8 and " " in t


def sanitize(text: str | None) -> tuple[str | None, list[str]]:
    """-> (cleaned text, findings). Invisible characters are cut out; mixed scripts and encoded
    coherent text are only flagged, since they occur in honest texts."""
    if not text:
        return text, []
    found: list[str] = []
    inv = _INVISIBLE.findall(text)
    if inv:
        found.append("removed:invisible:" + ",".join(sorted({f"U+{ord(c):04X}" for c in inv})[:6]))
        text = _INVISIBLE.sub("", text)
    if _ANSI.search(text):
        found.append("removed:ansi")
        text = _ANSI.sub("", text)
    if _CONTROL.search(text):
        found.append("removed:control")
        text = _CONTROL.sub("", text)
    for w in _WORD.findall(text):
        scripts = {s for s in (_script(c) for c in w) if s}
        if len(scripts) > 1:
            found.append(f"flag:mixed-script:{w[:40]}")
            break
    for m in _B64.finditer(text):
        try:
            if _readable(base64.b64decode(m.group(0) + "=" * (-len(m.group(0)) % 4), validate=False)):
                found.append("flag:encoded:base64")
                break
        except (binascii.Error, ValueError):
            continue
    for m in _HEX.finditer(text):
        if _readable(bytes.fromhex(m.group(0))):
            found.append("flag:encoded:hex")
            break
    return text, found


# ---------------------------------------------------------------- 1.6.3 keys and tokens

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}")),
    ("AWS key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_\-]{33}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
]


def find_secret(*texts: str | None) -> str | None:
    """-> kind of key found (without its value) or None."""
    for t in texts:
        if not t:
            continue
        for kind, rx in SECRET_PATTERNS:
            if rx.search(t):
                return kind
    return None


# ---------------------------------------------------------------- 1.6.4 related texts

SHINGLE = 32
KIN_WINDOWS = 3
EXACT_MIN = 12


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = _INVISIBLE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _shingles(s: str) -> set[str]:
    return {s[i:i + SHINGLE] for i in range(0, max(0, len(s) - SHINGLE + 1))}


def kin(a: str | None, b: str | None) -> bool:
    """Related texts: equal verbatim (12 characters or more) or sharing >= 3 fragments of 32 characters.
    Three shared windows cannot be chance; it is a forward or a copy-paste of one source."""
    na, nb = _norm(a or ""), _norm(b or "")
    if not na or not nb:
        return False
    if na == nb and len(na) >= EXACT_MIN:
        return True
    return len(_shingles(na) & _shingles(nb)) >= KIN_WINDOWS


def independent_groups(items: list[tuple[str, str | None]]) -> int:
    """items = [(independent_key, quote)]. Number of independent groups: the same key or a related text
    merges evidence into one group (union-find)."""
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if items[i][0] == items[j][0] or kin(items[i][1], items[j][1]):
                parent[find(i)] = find(j)
    return len({find(i) for i in range(n)})
