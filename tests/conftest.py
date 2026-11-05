"""Pytest-Einstieg für den Netzwerk-Wächter gegen Anthropic-Aufrufe (Logik in tests/network_guard.py, Plan 12.10)."""

from __future__ import annotations

import os

from tests import network_guard  # noqa: F401  (Import installiert den Wächter)

pytest_plugins = ["pytester"]

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
FAKE_API_KEY = "sk-ant-FAKE-test-guard-0000"


def pytest_configure(config):
    """Umgebung der Sitzung festlegen: keine Proxys/Base-URL (Host-Abgleich trifft die echte API), Fake-Schlüssel."""
    for name in _PROXY_VARS:
        os.environ.pop(name, None)
        os.environ.pop(name.lower(), None)
    os.environ.pop("ANTHROPIC_BASE_URL", None)
    os.environ["ANTHROPIC_API_KEY"] = FAKE_API_KEY


def pytest_sessionfinish(session, exitstatus):
    """Fallnetz: auch ein abgefangener Versuch lässt die Sitzung scheitern."""
    if network_guard.ATTEMPTS:
        print(
            f"\nERROR NETZWERKWAECHTER: {len(network_guard.ATTEMPTS)} Versuch(e) an die Anthropic-API: "
            f"{network_guard.ATTEMPTS}"
        )
        if session.exitstatus == 0:
            session.exitstatus = 1
