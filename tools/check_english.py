"""Code comments and docstrings are in English: reports Cyrillic in them.

Checks Python comments and docstrings under mem/, tools/ and tests/, SQL comments in migrations/, and `#` comments in
Dockerfile, docker-compose*.yml, .github/ and tools/*.sh. String literals are not checked: Russian test data, Russian
MCP descriptions (MEM_MCP_LANG=ru) and Russian word lists of the summary gate are data, not comments.

    python3 tools/check_english.py        # exit code 1 and a list of places if anything is found
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CYR = re.compile(r"[А-Яа-яЁё]")


def python_places(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    out = [(t.start[0], t.string) for t in tokenize.generate_tokens(io.StringIO(src).readline)
           if t.type == tokenize.COMMENT and CYR.search(t.string)]
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str) and CYR.search(first.value.value)):
                out.append((first.lineno, first.value.value.strip().splitlines()[0]))
    return out


def line_comment_places(path: Path, marker: str) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = re.search(rf"(?:^|\s){re.escape(marker)}(.*)$", line)
        if m and CYR.search(m.group(1)):
            out.append((n, line.strip()))
    return out


def places() -> list[str]:
    found = []
    for d in ("mem", "tools", "tests"):
        for p in sorted((ROOT / d).rglob("*.py")):
            found += [f"{p.relative_to(ROOT)}:{n}: {t[:100]}" for n, t in python_places(p)]
    for p in sorted((ROOT / "migrations").glob("*.sql")):
        found += [f"{p.relative_to(ROOT)}:{n}: {t[:100]}" for n, t in line_comment_places(p, "--")]
    hashed = [ROOT / "Dockerfile", *ROOT.glob("docker-compose*.yml"), *(ROOT / ".github").rglob("*.yml"),
              *(ROOT / "tools").glob("*.sh"), ROOT / ".env.example"]
    for p in hashed:
        if p.exists():
            found += [f"{p.relative_to(ROOT)}:{n}: {t[:100]}" for n, t in line_comment_places(p, "#")]
    return found


def main() -> None:
    found = places()
    for f in found:
        print(f)
    if found:
        print(f"{len(found)} comments or docstrings with Cyrillic; write them in English", file=sys.stderr)
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
