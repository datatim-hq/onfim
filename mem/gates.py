"""Gate G2 (05 §3): deterministic check of a summary proposed by the LLM. Pure functions."""
from __future__ import annotations

import re

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
MONTH_RE = r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
SUMMARIZABLE_KINDS = {"episode"}   # G2d


def _norm(t: str) -> str:
    return t.replace("ё", "е").replace("Ё", "Е")


def extract_dates(text: str) -> set[tuple[int, int]]:
    """Dates as (day, month): 30.09, 30.09.2026, 2026-09-30, and Russian "1 October" with the month name."""
    out = set()
    for d, m in re.findall(r"\b(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\b", text):
        out.add((int(d), int(m)))
    for _y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        out.add((int(d), int(m)))
    for d, mon in re.findall(r"\b(\d{1,2})\s+" + MONTH_RE, text.lower()):
        m = next(v for k, v in MONTHS.items() if mon.startswith(k))
        out.add((int(d), m))
    return out


def _strip_dates(text: str) -> str:
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    text = re.sub(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b", " ", text)
    text = re.sub(r"\b\d{1,2}\s+" + MONTH_RE, " ", text, flags=re.I)
    return text


def extract_times(text: str) -> set[str]:
    return {f"{int(h)}:{mm}" for h, mm in re.findall(r"\b(\d{1,2}):(\d{2})\b", text) if int(h) < 24}


def extract_numbers(text: str) -> set[str]:
    t = _strip_dates(text)
    t = re.sub(r"\b\d{1,2}:\d{2}\b", " ", t)
    out = set()
    for n in re.findall(r"\d+(?:[.,]\d+)?", t):
        n = n.replace(",", ".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n.lstrip("0") or "0")
    return out


# Common sentence starts in summaries that are not names (the first word of a sentence is checked, except these).
SENTENCE_STARTERS = {
    "в", "во", "на", "по", "за", "с", "со", "к", "о", "об", "у", "из", "до", "после", "перед", "при", "для", "и", "а",
    "но", "итог", "итоги", "итого", "сводка", "всего", "также", "затем", "потом", "сначала", "далее", "кроме",
    "обсудили", "договорились", "решили", "было", "были", "был", "была", "это", "эти", "этот", "эта", "все", "всё",
    "дважды", "трижды", "несколько", "снова", "опять", "поставщик", "подрядчик", "the", "in", "on", "at",
    "during", "summary", "overall", "all", "this", "these",
}


def proper_names(text: str) -> set[str]:
    """Capitalized words -> stem (first 5 letters, yo->ye, lower). The first word of a sentence is checked too
    unless it is a common sentence start (otherwise a surname opening the summary would slip through)."""
    out = set()
    for sent in re.split(r"(?<=[.!?…:;])\s+|\n+", _norm(text)):
        words = re.findall(r"[A-Za-zА-Яа-я][\w-]*", sent)
        for i, w in enumerate(words):
            if not (w[0].isupper() and not w.isupper()):
                continue
            if i == 0 and (w.lower() in SENTENCE_STARTERS or len(w) <= 2):
                continue
            out.add(w.lower())
    return out


def _stem(w: str) -> str:
    """Rough stem: drop up to two ending letters, but keep at least 4 (Russian case endings of nouns and surnames)."""
    return w[:max(4, len(w) - 2)]


def _name_in_sources(name: str, src_words: set[str]) -> bool:
    st = _stem(name)
    return any(w.startswith(st) or name.startswith(_stem(w)) for w in src_words)


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-zА-Яа-я][\w-]*", _norm(text))}


def cyrillic_share(text: str) -> float:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    return sum(1 for c in letters if re.match(r"[А-Яа-яЁё]", c)) / len(letters) if letters else 0.0


def g2(summary: str, sources: list[dict]) -> list[str]:
    """Returns the list of failed conditions (empty = the summary passes). sources: [{kind, text, quotes:[..]}]."""
    src_text = "\n".join([s["text"] for s in sources] + [q for s in sources for q in s.get("quotes", []) if q])
    fails = []
    if not (extract_dates(summary) <= extract_dates(src_text)
            and extract_times(summary) <= extract_times(src_text)
            and extract_numbers(summary) <= extract_numbers(src_text)):
        fails.append("a")
    src_words = _words(src_text)
    if not all(_name_in_sources(n, src_words) for n in proper_names(summary)):
        fails.append("b")
    if len(summary) > sum(len(s["text"]) for s in sources):
        fails.append("c")
    if any(s["kind"] not in SUMMARIZABLE_KINDS for s in sources):
        fails.append("d")
    if (cyrillic_share(summary) >= 0.5) != (cyrillic_share(" ".join(s["text"] for s in sources)) >= 0.5):
        fails.append("e")
    return fails


G2_REASONS = {
    "a": "a date, time or number in the summary does not occur in the sources",
    "b": "a proper name in the summary is missing from the sources",
    "c": "the summary is longer than the sources",
    "d": "not all sources are episodes",
    "e": "the summary language differs from the language of the sources",
}
