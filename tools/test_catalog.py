"""Test registry: what each test checks and which requirements it covers.

The registry is built from the test code without running it: the description is the first sentence of the test's
docstring; the references are the part of the docstring before the colon (if it has digits: "B-01, C-2: ..."), and
otherwise a "# Covers:" comment at the top of the file. The suite comes from the test folder, marks from `pytestmark` and decorators.

    python3 tools/test_catalog.py                    # update docs/tests.md and docs/tests.json
    python3 tools/test_catalog.py --check            # only check: every test described and the registry fresh
    python3 tools/test_catalog.py --junit out.xml    # same as without flags, and print rows with the run outcome (JSON)

The guard test (`test_test_catalog.py`) fails if a test has no description or the registry is stale.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
REF = re.compile(r"^[^:]*\d[^:]*$")          # the part before the colon is references if it has digits
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«\"(])")
SUITES = {"unit": "unit", "integration": "integration", "acceptance": "acceptance"}


def _marks(nodes: list[ast.expr]) -> set[str]:
    out = set()
    for n in nodes:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Attribute) and sub.value.attr == "mark":
                out.add(sub.attr)
    return out


def _header(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("# ").strip())
    head = " ".join(lines)
    return head.split("Covers:", 1)[1].strip().rstrip(".") if "Covers:" in head else ""


def _first_sentence(doc: str) -> str:
    """First sentence of the docstring's first paragraph (the paragraph may span several lines)."""
    para = " ".join(line.strip() for line in doc.strip().split("\n\n")[0].splitlines())
    return SENTENCE.split(para, 1)[0].strip() if para else ""


def collect() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        rel = path.relative_to(ROOT).as_posix()
        header = _header(src)
        module_marks: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
                module_marks |= _marks([node.value])
        for node in tree.body:
            if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")):
                continue
            first = _first_sentence(ast.get_docstring(node) or "")
            refs, text = "", first
            head = first.split(": ", 1)[0]
            if ": " in first and REF.match(head) and len(head) <= 40:      # a long phrase with a number is not references
                refs, text = first.split(": ", 1)
            marks = sorted((module_marks | _marks(node.decorator_list)) - {"parametrize", "usefixtures", "asyncio"})
            rows.append({
                "id": f"{rel}::{node.name}",
                "file": rel,
                "name": node.name,
                "suite": SUITES.get(path.parent.name, "main"),
                "slow": "slow" in marks,
                "marks": marks,
                "what": text.rstrip("."),
                "closes": refs or header,
            })
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    lines = ["# Test registry", "",
             "Built by `python3 tools/test_catalog.py` from the test docstrings; do not edit by hand.", "",
             f"Tests: {len(rows)}, slow: {sum(r['slow'] for r in rows)}.", ""]
    by_file: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    for f, rs in by_file.items():
        lines += [f"## `{f}`", "", "| Test | What it checks | Covers | Marks |", "|---|---|---|---|"]
        for r in rs:
            lines.append(f"| `{r['name']}` | {r['what'].replace('|', '/')} | {r['closes'].replace('|', '/')} | "
                         f"{', '.join(r['marks']) or '—'} |")
        lines.append("")
    return "\n".join(lines)


def problems(rows: list[dict[str, Any]] | None = None) -> list[str]:
    """What is wrong with the registry: tests without a description and stale docs/tests.json, docs/tests.md."""
    rows = collect() if rows is None else rows
    out = [f"no description: {r['id']}" for r in rows if not r["what"]]
    try:
        saved = json.loads((ROOT / "docs" / "tests.json").read_text(encoding="utf-8"))
        md = (ROOT / "docs" / "tests.md").read_text(encoding="utf-8")
    except (OSError, ValueError):
        saved, md = None, None
    if saved != rows or md != render(rows) + "\n":
        out.append("the registry is stale: run python3 tools/test_catalog.py and commit docs/tests.md, docs/tests.json")
    return out


def outcomes(junit: Path) -> dict[str, str]:
    """Outcome of each test from a pytest --junitxml report."""
    res: dict[str, str] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        cls, name = case.get("classname", ""), case.get("name", "").split("[")[0]
        tid = cls.replace(".", "/") + ".py::" + name
        if case.find("failure") is not None or case.find("error") is not None:
            st = "failed"
        elif case.find("skipped") is not None:
            st = "skipped"
        else:
            st = "passed"
        if res.get(tid) != "failed":
            res[tid] = st
    return res


def write(rows: list[dict[str, Any]]) -> None:
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "tests.md").write_text(render(rows) + "\n", encoding="utf-8")
    (ROOT / "docs" / "tests.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only check the descriptions and that the registry is fresh")
    ap.add_argument("--junit", type=Path, help="a pytest --junitxml report: add the run outcome and print JSON rows")
    a = ap.parse_args()
    rows = collect()
    if a.check:
        bad = problems(rows)
        for p in bad:
            print(p, file=sys.stderr)
        sys.exit(1 if bad else 0)
    write(rows)
    if a.junit:
        st = outcomes(a.junit)
        for r in rows:
            r["status"] = st.get(r["id"], "not run")
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
