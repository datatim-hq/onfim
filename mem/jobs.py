"""Scheduler of nightly jobs (the worker container): light sleep daily at 03:30, deep sleep on Sundays at 04:00
in the MEM_TZ time zone (UTC by default).
Run: python -m mem.jobs                          (loop)
     python -m mem.jobs light-now [--dry-run]    (one run)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mem import alerts, db
from mem.clock import Clock
from mem.config.settings import Settings
from mem.service import MemService
from mem.sleep import LightSleep

log = logging.getLogger("mem.jobs")
TZ = ZoneInfo(os.environ.get("MEM_TZ") or "UTC")
LIGHT_AT = (3, 30)
DEEP_AT = (4, 0)          # Sunday, after the light sleep


class _NoEmbed:
    model_id = "n/a"
    dim = 0


def next_run(now: datetime, hm=LIGHT_AT) -> datetime:
    local = now.astimezone(TZ)
    t = local.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if t <= local:
        t += timedelta(days=1)
    return t


def build_svc() -> MemService:
    s = Settings.load()
    pool = db.make_pool(s.database_url, max_size=3)
    return MemService(pool, _NoEmbed(), Clock())  # type: ignore[arg-type]


def next_deep(now: datetime) -> datetime:
    t = next_run(now, DEEP_AT)
    while t.weekday() != 6:
        t += timedelta(days=1)
    return t


def already_ran_today(svc: MemService, kind: str = "light_sleep") -> bool:
    today = datetime.now(TZ).date()
    with svc.pool.connection() as conn:
        r = conn.execute("SELECT started_at FROM jobs WHERE kind=%s AND NOT dry_run "
                         "AND status IN ('OK','PARTIAL') ORDER BY started_at DESC LIMIT 1", (kind,)).fetchone()
        conn.rollback()
    return bool(r and r["started_at"].astimezone(TZ).date() == today and r["started_at"].astimezone(TZ).hour >= 3)


def _run(kind: str, svc: MemService) -> None:
    if already_ran_today(svc, kind):
        log.info("%s already done today, skip", kind)
        return
    try:
        if kind == "light_sleep":
            res = LightSleep(svc).run(dry_run=False)
        else:
            from mem.deep_sleep import DeepSleep
            res = DeepSleep(deep_svc()).run(dry_run=False)
        log.info("%s: job=%s status=%s", kind, res.get("job_id"), res.get("status"))
    except Exception as e:  # noqa: BLE001
        log.exception("%s crashed", kind)
        alerts.send(f"mem: {kind} crashed: {type(e).__name__}: {e}")


_DEEP_SVC = None


def deep_svc() -> MemService:
    """Deep sleep needs the embedder (summaries are new records): the model is loaded only for the run."""
    global _DEEP_SVC
    if _DEEP_SVC is None:
        from mem.embedder import SafeEmbedder, build
        s = Settings.load()
        _DEEP_SVC = MemService(db.make_pool(s.database_url, max_size=3),
                               SafeEmbedder(build(s.embed_model, s.embed_dim)), Clock())
    return _DEEP_SVC


def loop() -> None:
    from mem.db import ns_scope
    ns_scope.set("*")                          # the sleep worker serves all namespaces (protection item 6)
    svc = build_svc()
    log.info("worker started; light %s, deep %s", next_run(datetime.now(TZ)).isoformat(),
             next_deep(datetime.now(TZ)).isoformat())
    while True:
        now = datetime.now(TZ)
        light, deep = next_run(now), next_deep(now)
        kind, target = ("light_sleep", light) if light <= deep else ("deep_sleep", deep)
        while (wait := (target - datetime.now(TZ)).total_seconds()) > 0:
            time.sleep(min(wait, 300))
        _run(kind, svc)
        time.sleep(60)


def main(argv: list[str]) -> int:
    from mem.db import ns_scope
    ns_scope.set("*")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if argv and argv[0] == "light-now":
        res = LightSleep(build_svc()).run(dry_run="--dry-run" in argv)
        print(res)
        return 0 if res.get("status") in ("OK", "SKIPPED") else 1
    if argv and argv[0] == "deep-now":
        from mem.deep_sleep import DeepSleep
        res = DeepSleep(deep_svc()).run(dry_run="--dry-run" in argv)
        print(res)
        return 0 if res.get("status") in ("OK", "SKIPPED") else 1
    loop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
