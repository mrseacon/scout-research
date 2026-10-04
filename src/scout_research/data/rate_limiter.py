import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Sliding-Window Rate Limiter: max. `max_requests` pro `period_seconds`.

    Thread-safe. `acquire()` blockiert, bis ein Slot frei ist, statt Requests zu verwerfen —
    Aufrufe sollen verzögert, nicht abgebrochen werden. Das Fenster gleitet: in *jedem*
    Zeitraum von `period_seconds` werden höchstens `max_requests` Slots vergeben (damit ist
    auch ein serverseitig fest ausgerichtetes Minutenfenster sicher).

    `clock` und `sleep` sind injizierbar, damit Tests ohne echte Wartezeit laufen.
    """

    def __init__(
        self,
        max_requests: int,
        period_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_requests = max_requests
        self._period = period_seconds
        self._clock = clock
        self._sleep = sleep
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._timestamps and now - self._timestamps[0] >= self._period:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max_requests:
                    self._timestamps.append(now)
                    return

                wait_time = self._period - (now - self._timestamps[0])

            self._sleep(max(wait_time, 0))
