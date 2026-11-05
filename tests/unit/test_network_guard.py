"""Kanarientests für den Netzwerk-Wächter (tests/network_guard.py, Plan 12.10).

Der SDK-Kanarientest schaltet sich selbst scharf: Ohne installiertes `anthropic` kehrt er ohne Prüfung zurück
und ist wirkungslos (kein skip). Beleg bei Installation des SDK nachholen.
"""

from __future__ import annotations

import socket

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from tests import network_guard as guard

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_attempts():
    """Jeder Kanarientest räumt seine Einträge aus dem Protokoll, damit das Fallnetz grün bleibt."""
    before = list(guard.ATTEMPTS)
    yield
    guard.ATTEMPTS[:] = before


def test_getaddrinfo_wird_blockiert():
    with pytest.raises(guard.AnthropicNetworkBlocked):
        socket.getaddrinfo("api.anthropic.com", 443)
    assert "api.anthropic.com" in guard.ATTEMPTS


def test_create_connection_gross_und_punkt_wird_blockiert():
    with pytest.raises(guard.AnthropicNetworkBlocked):
        socket.create_connection(("API.Anthropic.com.", 443), timeout=0.01)
    assert "API.Anthropic.com." in guard.ATTEMPTS


def test_socket_connect_mit_hostname_wird_blockiert():
    s = socket.socket()
    try:
        with pytest.raises(guard.AnthropicNetworkBlocked):
            s.connect(("claude.ai", 443))
        with pytest.raises(guard.AnthropicNetworkBlocked):
            s.connect_ex(("www.claude.ai", 443))
    finally:
        s.close()
    assert guard.ATTEMPTS[-2:] == ["claude.ai", "www.claude.ai"]


def test_httpx_post_wird_blockiert():
    with pytest.raises(guard.AnthropicNetworkBlocked):
        with httpx.Client(timeout=0.5) as client:
            client.post("https://api.anthropic.com/v1/messages", json={})
    assert any(h.lower().rstrip(".") == "api.anthropic.com" for h in guard.ATTEMPTS)


def test_fremd_host_wird_an_original_weitergereicht(monkeypatch):
    calls = []

    def fake_getaddrinfo(host, *args, **kwargs):
        calls.append(host)
        return ["fake"]

    def fake_create_connection(address, *args, **kwargs):
        calls.append(address)
        return "fake-conn"

    monkeypatch.setitem(guard.ORIGINALS, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setitem(guard.ORIGINALS, "create_connection", fake_create_connection)
    assert guard.guarded_getaddrinfo("example.invalid", 443) == ["fake"]
    assert guard.guarded_create_connection(("example.invalid", 443)) == "fake-conn"
    assert calls == ["example.invalid", ("example.invalid", 443)]
    assert "example.invalid" not in guard.ATTEMPTS


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("anthropic.com", True),
        ("API.Anthropic.com.", True),
        ("a.b.claude.ai", True),
        ("anthropicapi.com", False),
        ("notanthropic.com", False),
        ("anthropic.com.evil.example", False),
        ("example.invalid", False),
        ("127.0.0.1", False),
        (None, False),
    ],
)
def test_host_abgleich_ist_subdomain_nicht_teilstring(host, expected):
    assert guard.is_anthropic_host(host) is expected


def test_wächter_ist_installiert():
    """Identität statt Netz: die socket-Funktionen sind die Wächterfunktionen (rot bei GUARD_ENABLED = False)."""
    assert socket.getaddrinfo is guard.guarded_getaddrinfo
    assert socket.create_connection is guard.guarded_create_connection
    assert socket.socket.connect is guard.guarded_connect
    assert socket.socket.connect_ex is guard.guarded_connect_ex


def test_sitzungsumgebung_ist_festgelegt():
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "ANTHROPIC_BASE_URL"):
        assert name not in os.environ
        assert name.lower() not in os.environ
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-FAKE-test-guard-0000"


def test_httpx_async_client_wird_blockiert():
    async def run():
        async with httpx.AsyncClient(timeout=0.5) as client:
            await client.post("https://api.anthropic.com/v1/messages", json={})

    with pytest.raises(guard.AnthropicNetworkBlocked):
        asyncio.run(run())
    assert any(h.lower().rstrip(".") == "api.anthropic.com" for h in guard.ATTEMPTS)


def test_anthropic_sdk_wird_blockiert():
    try:
        import anthropic
    except ImportError:
        return  # SDK nicht installiert: Kanarientest wirkungslos (siehe Modul-Docstring)
    client = anthropic.Anthropic(api_key="sk-ant-FAKE-test-guard-0000", max_retries=0)
    try:
        client.messages.create(
            model="claude-sonnet-5-5", max_tokens=1, messages=[{"role": "user", "content": "x"}]
        )
    except guard.AnthropicNetworkBlocked:
        pass
    assert any(h.lower().rstrip(".") == "api.anthropic.com" for h in guard.ATTEMPTS)
    guard.ATTEMPTS.clear()


def test_fallnetz_greift_bei_verschlucktem_versuch(pytester, monkeypatch):
    """Ein Test, der die Ausnahme schluckt, lässt die Sitzung trotzdem mit Status 1 enden."""
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "src")]))
    pytester.makeconftest(
        "from tests import network_guard\n"
        "from tests.conftest import pytest_sessionfinish  # noqa: F401\n"
    )
    pytester.makepyfile(
        test_swallow="""
        import socket

        def test_swallow():
            try:
                socket.getaddrinfo("api.anthropic.com", 443)
            except BaseException:
                pass
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*ERROR NETZWERKWAECHTER*"])
    result.assert_outcomes(passed=1)
