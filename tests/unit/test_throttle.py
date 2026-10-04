"""Rate Limiter, Finnhub-Throttle und 429/Retry-After — ohne Netzwerk, ohne echte Wartezeit."""

from datetime import datetime, timezone

import httpx
import pytest

from scout_research.data.market_provider import (
    MAX_RETRY_AFTER_SECONDS,
    FinnhubProvider,
    _get_with_retry,
    _parse_retry_after,
)
from scout_research.data.rate_limiter import RateLimiter


class FakeClock:
    """Gemeinsame Uhr: `sleep` spult sie vor, statt zu warten."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class SpyLimiter:
    def __init__(self) -> None:
        self.acquires = 0

    def acquire(self) -> None:
        self.acquires += 1


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- RateLimiter ---------------------------------------------------------------------------


def test_rate_limiter_allows_burst_up_to_limit_without_waiting() -> None:
    fc = FakeClock()
    limiter = RateLimiter(3, period_seconds=60.0, clock=fc.clock, sleep=fc.sleep)
    for _ in range(3):
        limiter.acquire()
    assert fc.sleeps == []


def test_rate_limiter_blocks_until_oldest_slot_leaves_window() -> None:
    fc = FakeClock()
    limiter = RateLimiter(3, period_seconds=60.0, clock=fc.clock, sleep=fc.sleep)
    for _ in range(3):
        limiter.acquire()
    fc.now += 10.0  # 10 s später: 4. Aufruf muss bis zum Ablauf des ältesten Slots warten
    limiter.acquire()
    assert fc.sleeps == [pytest.approx(50.0)]


def test_rate_limiter_never_exceeds_limit_in_any_window() -> None:
    fc = FakeClock()
    limiter = RateLimiter(5, period_seconds=60.0, clock=fc.clock, sleep=fc.sleep)
    stamps = []
    for _ in range(40):
        limiter.acquire()
        stamps.append(fc.now)
    for i, t in enumerate(stamps):
        in_window = [s for s in stamps if t <= s < t + 60.0]
        assert len(in_window) <= 5, f"{len(in_window)} Slots im Fenster ab Aufruf {i}"


# --- Retry-After-Parser --------------------------------------------------------------------


def test_parse_retry_after_seconds() -> None:
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after(" 0 ") == 0.0
    assert _parse_retry_after("-5") == 0.0


def test_parse_retry_after_http_date() -> None:
    now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
    assert _parse_retry_after("Sun, 04 Oct 2026 12:00:30 GMT", now=now) == pytest.approx(30.0)
    assert _parse_retry_after("Sun, 04 Oct 2026 11:00:00 GMT", now=now) == 0.0  # Vergangenheit


@pytest.mark.parametrize("value", [None, "", "soon", "nan", "inf"])
def test_parse_retry_after_unusable_values_return_none(value) -> None:
    assert _parse_retry_after(value) is None


# --- _get_with_retry: jeder Versuch zählt --------------------------------------------------


def test_every_attempt_goes_through_the_limiter_including_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        if calls["n"] == 2:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"ok": True})

    spy, fc = SpyLimiter(), FakeClock()
    response = _get_with_retry(_client(handler), "https://x.test", rate_limiter=spy, sleep=fc.sleep)

    assert response.json() == {"ok": True}
    assert calls["n"] == 3
    assert spy.acquires == 3  # ein Slot pro HTTP-Versuch, nicht pro get_price-Aufruf
    assert fc.sleeps == [0.5, 3.0]  # Backoff nach Netzwerkfehler, dann Retry-After


def test_429_waits_for_retry_after_then_succeeds() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"c": 1})

    fc = FakeClock()
    _get_with_retry(_client(handler), "https://x.test", sleep=fc.sleep)
    assert fc.sleeps == [7.0]


def test_429_without_retry_after_uses_default_backoff() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(429) if len(seen) < 3 else httpx.Response(200, json={})

    fc = FakeClock()
    _get_with_retry(_client(handler), "https://x.test", sleep=fc.sleep)
    assert fc.sleeps == [5.0, 10.0]


def test_429_gives_up_after_max_retries_and_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"})

    spy, fc = SpyLimiter(), FakeClock()
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        _get_with_retry(_client(handler), "https://x.test", max_retries=2, rate_limiter=spy, sleep=fc.sleep)
    assert exc_info.value.response.status_code == 429
    assert spy.acquires == 3
    assert fc.sleeps == [1.0, 1.0]  # kein Sleep nach dem letzten Versuch


def test_retry_after_longer_than_cap_aborts_immediately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": str(int(MAX_RETRY_AFTER_SECONDS) + 1)})

    spy, fc = SpyLimiter(), FakeClock()
    with pytest.raises(httpx.HTTPStatusError):
        _get_with_retry(_client(handler), "https://x.test", rate_limiter=spy, sleep=fc.sleep)
    assert spy.acquires == 1
    assert fc.sleeps == []  # nicht blockieren


def test_other_status_errors_are_not_retried() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(401)

    fc = FakeClock()
    with pytest.raises(httpx.HTTPStatusError):
        _get_with_retry(_client(handler), "https://x.test", sleep=fc.sleep)
    assert len(seen) == 1 and fc.sleeps == []


# --- FinnhubProvider: Throttle über mehrere Ticker -----------------------------------------


def test_finnhub_provider_throttles_across_tickers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"c": 10.0, "t": 1759363200})

    fc = FakeClock()
    limiter = RateLimiter(2, period_seconds=60.0, clock=fc.clock, sleep=fc.sleep)
    provider = FinnhubProvider("key", client=_client(handler), rate_limiter=limiter, sleep=fc.sleep)

    for ticker in ["AAA", "BBB", "CCC", "DDD", "EEE"]:
        assert provider.get_price(ticker) is not None

    # 5 Abrufe bei 2/min: Abruf 3 wartet auf Fenster 1, Abruf 5 auf Fenster 2
    assert len(fc.sleeps) == 2
    assert sum(fc.sleeps) == pytest.approx(120.0)


def test_finnhub_provider_default_limit_is_below_documented_free_tier() -> None:
    provider = FinnhubProvider("key", client=_client(lambda r: httpx.Response(200, json={})))
    assert provider._rate_limiter._max_requests == 55
    assert provider._rate_limiter._period == 60.0
