# Covers: the test registry grows together with the code (CONTRIBUTING, the rule about tests).
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _catalog():
    spec = importlib.util.spec_from_file_location("test_catalog", ROOT / "tools" / "test_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_test_has_description():
    """Every test has a docstring saying what it checks."""
    missing = [r["id"] for r in _catalog().collect() if not r["what"]]
    assert not missing, "tests without a description: " + ", ".join(missing)


def test_registry_is_up_to_date():
    """docs/tests.json and docs/tests.md match the test code: after a new test, run python3 tools/test_catalog.py."""
    cat = _catalog()
    rows = cat.collect()
    assert json.loads((ROOT / "docs" / "tests.json").read_text(encoding="utf-8")) == rows, \
        "the registry is stale: run python3 tools/test_catalog.py"
    assert (ROOT / "docs" / "tests.md").read_text(encoding="utf-8") == cat.render(rows) + "\n"
