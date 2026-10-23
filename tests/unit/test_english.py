# Covers: #17, code comments and docstrings are in English (CONTRIBUTING).
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (ROOT / "migrations").exists(), reason="no source tree")


def test_comments_and_docstrings_are_english():
    """Comments and docstrings under mem/, tools/, tests/ and migrations/ have no Cyrillic; string data may."""
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.places() == []
