# Covers: #17, documentation in English and Russian (CONTRIBUTING).
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (ROOT / "docs").exists(), reason="no source tree")


def _check_docs():
    spec = importlib.util.spec_from_file_location("check_docs", ROOT / "tools" / "check_docs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_page_has_a_translation_and_links_resolve():
    """#17: every user page exists in English and Russian with the same headings and code; all relative links and anchors resolve."""
    assert _check_docs().problems() == []


def test_anchor_slugs_follow_github():
    """#17: heading anchors are computed the way GitHub does, for Latin and Cyrillic headings."""
    slug = _check_docs().slug
    assert slug("Running behind a TLS reverse proxy") == "running-behind-a-tls-reverse-proxy"
    assert slug("`POST /v1/{ns}/recall` — search") == "post-v1nsrecall--search"
    assert slug("Сила и затухание") == "сила-и-затухание"
