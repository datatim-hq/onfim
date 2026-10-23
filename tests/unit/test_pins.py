"""Pinned builds: images by digest, libraries from hashed lock files that match requirements*.txt.

These tests read build files that are not in the test image, so they run from a checkout (CI) and skip inside the
image."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not (ROOT / "Dockerfile").exists(), reason="no build files (running inside the image)")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(lock: Path) -> dict[str, tuple[str, list[str]]]:
    out, cur = {}, None
    for line in lock.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==(\S+)", line)
        if m:
            cur = _norm(m.group(1))
            out[cur] = (m.group(2), [])
        elif cur and (h := re.search(r"--hash=sha256:([0-9a-f]{64})", line)):
            out[cur][1].append(h.group(1))
    return out


def _requirements(req: Path) -> list[Requirement]:
    out = []
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            out.append(Requirement(line))
    return out


def test_images_by_digest():
    """05: FROM in the Dockerfile and the pgvector image in Compose files and CI carry a digest, so a build tomorrow
    gets the same Python and the same Postgres."""
    froms = re.findall(r"^FROM\s+(\S+)", (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.M)
    files = list(ROOT.glob("docker-compose*.yml")) + [ROOT / ".github/workflows/ci.yml"]
    imgs = [m for p in files for m in re.findall(r"^\s*image:\s*(\S+)", p.read_text(encoding="utf-8"), re.M)]
    bad = [i for i in froms + imgs if not i.startswith("mem-api:") and not DIGEST.search(i)]
    assert froms and imgs and not bad, bad


def test_pip_installs_only_hashed_locks():
    """05: the image and CI install libraries only from lock files and only with hash checking."""
    texts = [(ROOT / "Dockerfile").read_text(encoding="utf-8"), (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")]
    installs = [i for t in texts for i in re.findall(r"pip install[^\n&]*", t)]
    assert installs and all("--require-hashes" in i and ".lock" in i for i in installs), installs


@pytest.mark.parametrize("req,lock", [("requirements.txt", "requirements.lock"),
                                      ("requirements-dev.txt", "requirements-dev.lock")])
def test_lock_matches_requirements(req, lock):
    """05: every library from requirements*.txt is in the lock file at a version its specifier allows (a bump in
    requirements without `tools/lock.sh` fails here), and every pinned line has hashes."""
    pins = _pins(ROOT / lock)
    reqs = [r for p in ([ROOT / "requirements.txt"] if req != "requirements.txt" else []) + [ROOT / req]
            for r in _requirements(p)]
    missing = [r.name for r in reqs if _norm(r.name) not in pins]
    stale = [f"{r.name}=={pins[_norm(r.name)][0]} vs {r.specifier}" for r in reqs
             if _norm(r.name) in pins and not r.specifier.contains(pins[_norm(r.name)][0], prereleases=True)]
    assert not missing and not stale, (missing, stale)
    assert "socksio" in pins and not [n for n, (_, h) in pins.items() if not h]
