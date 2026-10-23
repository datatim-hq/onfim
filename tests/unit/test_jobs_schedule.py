import importlib
from datetime import datetime, timezone

import mem.jobs


def test_schedule_follows_mem_tz(monkeypatch):
    """Release, operations: the sleep schedule follows the MEM_TZ time zone (UTC by default); deep sleep runs on Sunday."""
    noon_utc = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)          # Friday
    monkeypatch.delenv("MEM_TZ", raising=False)
    jobs = importlib.reload(mem.jobs)
    assert jobs.next_run(noon_utc).isoformat() == "2026-10-10T03:30:00+00:00"
    deep = jobs.next_deep(noon_utc)
    assert (deep.weekday(), deep.hour, deep.minute) == (6, 4, 0) and deep.date().isoformat() == "2026-10-11"
    monkeypatch.setenv("MEM_TZ", "Asia/Tokyo")
    jobs = importlib.reload(mem.jobs)
    assert jobs.next_run(noon_utc).isoformat() == "2026-10-10T03:30:00+09:00"
    monkeypatch.delenv("MEM_TZ")
    importlib.reload(mem.jobs)
