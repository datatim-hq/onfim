"""Service clock. On a test stand (MEM_STAND=1) time can be shifted, so decay and forgetting
are checked in minutes rather than months (07). Shifting is forbidden in production."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone


class Clock:
    def __init__(self, stand: bool = False, offset_s: float = 0.0):
        self._stand = stand
        self._offset = timedelta(seconds=offset_s if stand else 0.0)
        self._lock = threading.Lock()

    @property
    def stand(self) -> bool:
        return self._stand

    def now(self) -> datetime:
        with self._lock:
            return datetime.now(timezone.utc) + self._offset

    def offset_seconds(self) -> float:
        return self._offset.total_seconds()

    def set_offset(self, seconds: float) -> None:
        if not self._stand:
            raise PermissionError("shifting the clock is allowed only in stand mode")
        with self._lock:
            self._offset = timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self.set_offset(self.offset_seconds() + seconds)
