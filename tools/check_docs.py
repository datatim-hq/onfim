"""Documentation checks: every page has its translation, and every relative link resolves.

Pairs: README.md and README.ru.md; docs/<page>.md and docs/ru/<page>.md (docs/tests.md is generated and English
only); the same for docs/research/ and docs/design/. For each pair:

- both pages exist and link to each other;
- they have the same number of headings of each level and the same code blocks, so a change made in one language and
  forgotten in the other shows up. Comment lines inside code blocks may be translated; a block fenced as ```text
  (a diagram or a schema sketch with prose) is translated as a whole, so only the number of such blocks must match.

For every Markdown file in the repository root and under docs/: relative links point at existing files, and a `#anchor`
matches a heading or an explicit `<a id="...">` in the target. Translations keep the English anchor ids with explicit
`<a id>` tags, so the same links work in both languages.

    python3 tools/check_docs.py        # exit code 1 and a list of problems if anything is found
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
ANCHOR_TAG = re.compile(r'<a id="([^"]+)"></a>')
FENCE = re.compile(r"^```")


def pairs() -> list[tuple[Path, Path]]:
    out = [(ROOT / "README.md", ROOT / "README.ru.md")]
    docs = ROOT / "docs"
    for sub in ("", "research", "design"):
        en_dir, ru_dir = docs / sub, docs / "ru" / sub
        names = {p.name for p in en_dir.glob("*.md")} | {p.name for p in ru_dir.glob("*.md")}
        out += [(en_dir / n, ru_dir / n) for n in sorted(names) if not (sub == "" and n == "tests.md")]
    return out


def strip_code(text: str) -> list[str]:
    """Lines outside fenced code blocks."""
    out, inside = [], False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            inside = not inside
            continue
        if not inside:
            out.append(line)
    return out


def code_blocks(text: str) -> list[list[str]]:
    """Code blocks without comment lines; a ```text block is reduced to its marker."""
    blocks, cur, prose = [], None, False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            if cur is None:
                cur, prose = [], line.strip() == "```text"
            else:
                blocks.append(["<text>"] if prose else cur)
                cur = None
            continue
        if cur is not None and not line.strip().startswith(("#", "//", "--")):
            # A trailing comment after a command may be translated too.
            cur.append(re.sub(r"\s+(#|--)\s.*$", "", line).rstrip())
    return blocks


def heading_levels(text: str) -> list[int]:
    return [len(m.group(1)) for line in strip_code(text) if (m := re.match(r"^(#{1,6})\s", line))]


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    h = re.sub(r"`|\*\*|<[^>]+>", "", heading.strip().lower())
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
    return re.sub(r"[^\w\- ]", "", h).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = set(ANCHOR_TAG.findall(text))
    seen: dict[str, int] = {}
    for line in strip_code(text):
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if m:
            s = slug(m.group(1))
            found.add(s if s not in seen else f"{s}-{seen[s]}")
            seen[s] = seen.get(s, 0) + 1
    return found


def link_problems(path: Path) -> list[str]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for target in LINK.findall(line):
            if re.match(r"^[a-z]+:", target):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (path.parent / file_part).resolve() if file_part else path
            where = f"{path.relative_to(ROOT)}:{n}"
            if not dest.exists():
                out.append(f"{where}: link to a missing file {target}")
            elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                out.append(f"{where}: no anchor #{anchor} in {dest.relative_to(ROOT)}")
    return out


def pair_problems(en: Path, ru: Path) -> list[str]:
    for p in (en, ru):
        if not p.exists():
            other = ru if p is en else en
            return [f"{other.relative_to(ROOT)}: no translation at {p.relative_to(ROOT)}"]
    out = []
    en_text, ru_text = en.read_text(encoding="utf-8"), ru.read_text(encoding="utf-8")
    for a, b, text in ((en, ru, en_text), (ru, en, ru_text)):
        targets = {(a.parent / t.partition("#")[0]).resolve() for t in LINK.findall(text) if t.partition("#")[0]}
        if b.resolve() not in targets:
            out.append(f"{a.relative_to(ROOT)}: no link to its translation {b.relative_to(ROOT)}")
    if heading_levels(en_text) != heading_levels(ru_text):
        out.append(f"{en.relative_to(ROOT)} and {ru.relative_to(ROOT)}: different headings")
    en_code, ru_code = code_blocks(en_text), code_blocks(ru_text)
    if len(en_code) != len(ru_code):
        out.append(f"{en.relative_to(ROOT)} and {ru.relative_to(ROOT)}: {len(en_code)} and {len(ru_code)} code blocks")
    else:
        for i, (a, b) in enumerate(zip(en_code, ru_code), 1):
            if a != b:
                out.append(f"{en.relative_to(ROOT)} and {ru.relative_to(ROOT)}: code block {i} differs")
    return out


def problems() -> list[str]:
    found = []
    for en, ru in pairs():
        found += pair_problems(en, ru)
    for p in sorted([*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]):
        found += link_problems(p)
    return found


def main() -> None:
    found = problems()
    for f in found:
        print(f)
    if found:
        print(f"{len(found)} documentation problems", file=sys.stderr)
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
