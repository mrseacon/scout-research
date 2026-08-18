import threading
import time
from collections import deque


class RateLimiter:
    """Sliding-Window Rate Limiter: max. `max_requests` pro `period_seconds`.

    Thread-safe. `acquire()` blockiert, bis ein Slot frei ist, statt Requests zu verwerfen —
    EDGAR-Aufrufe sollen verzögert, nicht abgebrochen werden.
    """

    def __init__(self, max_requests: int, period_seconds: float = 1.0) -> None:
        self._max_requests = max_requests
        self._period = period_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self._period:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max_requests:
                    self._timestamps.append(now)
                    return

                wait_time = self._period - (now - self._timestamps[0])

            time.sleep(max(wait_time, 0))
