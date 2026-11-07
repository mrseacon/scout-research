"""Schutz vor und nach einem Live-Lauf (Plan 12.10): Logging mit Schwärzung, Scan der Ausgaben (TA-16b)."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from scout_research import live_guard
from scout_research.live_guard import scan_outputs, secrets_of, setup_logging, teardown_logging
from scout_research.tools.sanitize import Redactor

KEY = "sk-ant-FAKE-guard-0123456789"


@pytest.fixture(autouse=True)
def _clean():
    yield
    teardown_logging()


def test_an_entry_of_any_logger_is_redacted_in_every_handler(tmp_path: Path) -> None:
    stream = io.StringIO()
    log = tmp_path / "x" / "run.log"
    setup_logging(Redactor([KEY]), stream=stream, log_path=log, env={})
    for name in ("irgendwas.beliebiges", "httpx2", "anthropic._base_client"):
        logging.getLogger(name).warning("Anfrage mit %s", KEY)
    try:
        raise RuntimeError(f"Fehler {KEY}")
    except RuntimeError:
        logging.getLogger("noch.eins").exception("kaputt")
    for handler in logging.getLogger().handlers:
        handler.flush()
    for text in (stream.getvalue(), log.read_text(encoding="utf-8")):
        assert KEY not in text and text.count("<redacted>") >= 4


def test_sdk_loggers_are_at_warning_and_infos_do_not_appear() -> None:
    stream = io.StringIO()
    setup_logging(Redactor([KEY]), stream=stream, env={})
    assert {n: logging.getLogger(n).level for n in live_guard.SDK_LOGGERS} == {n: logging.WARNING for n in live_guard.SDK_LOGGERS}
    logging.getLogger("httpx").info("Info %s", KEY)
    assert stream.getvalue() == ""


def test_anthropic_log_gives_the_fixed_warning(capsys) -> None:
    setup_logging(Redactor([KEY]), stream=io.StringIO(), env={"ANTHROPIC_LOG": "debug"})
    assert live_guard.ANTHROPIC_LOG_WARNING in capsys.readouterr().err
    setup_logging(Redactor([KEY]), stream=io.StringIO(), env={})
    assert capsys.readouterr().err == ""


def test_setup_is_idempotent_and_teardown_removes_the_handlers() -> None:
    before = list(logging.getLogger().handlers)
    setup_logging(Redactor([KEY]), stream=io.StringIO(), env={})
    setup_logging(Redactor([KEY]), stream=io.StringIO(), env={})
    assert len(logging.getLogger().handlers) == len(before) + 1
    teardown_logging()
    assert logging.getLogger().handlers == before


def test_scan_finds_files_recursively_and_returns_no_values(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    hit, clean = tmp_path / "a" / "b" / "x.json", tmp_path / "a" / "ok.md"
    hit.write_text(f"vorn {KEY} hinten", encoding="utf-8")
    clean.write_text("nichts", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\x00\xff" + KEY.encode() + b"\x00")
    found = scan_outputs([tmp_path], [KEY, "", "kurz"])
    assert sorted(found) == sorted([hit, tmp_path / "bin.dat"])
    assert all(isinstance(p, Path) for p in found) and KEY not in repr(found)


def test_scan_ignores_values_below_eight_characters(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("1234567 und abcdefgh", encoding="utf-8")
    assert scan_outputs([tmp_path], ["1234567"]) == []
    assert scan_outputs([tmp_path], ["abcdefgh"]) == [tmp_path / "f.txt"]


def test_scan_accepts_single_files_and_missing_paths(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text(KEY, encoding="utf-8")
    assert scan_outputs([f, tmp_path / "gibt_es_nicht"], [KEY]) == [f]


def test_secrets_of_takes_keys_contact_and_user_agent_and_skips_empty() -> None:
    s = SimpleNamespace(anthropic_api_key="a" * 10, finnhub_api_key=None, edgar_contact_email="m@x.invalid",
                        edgar_user_agent="App (m@x.invalid)")
    assert secrets_of(s) == ["a" * 10, "m@x.invalid", "App (m@x.invalid)"]


def test_teardown_restores_the_levels_of_the_sdk_loggers() -> None:
    before = {n: logging.getLogger(n).level for n in live_guard.SDK_LOGGERS}
    setup_logging(Redactor([KEY]), stream=io.StringIO(), env={})
    teardown_logging()
    assert {n: logging.getLogger(n).level for n in live_guard.SDK_LOGGERS} == before


def test_scan_reports_links_without_following_them_and_unreadable_files(tmp_path: Path) -> None:
    outside = tmp_path / "fremd"
    outside.mkdir()
    (outside / "geheim.txt").write_text(KEY, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "link").symlink_to(outside)
    (out / "dateilink.txt").symlink_to(outside / "geheim.txt")
    locked = out / "gesperrt.txt"
    locked.write_text("x", encoding="utf-8")
    locked.chmod(0)
    try:
        findings = live_guard.scan_findings([out], [KEY])
    finally:
        locked.chmod(0o644)
    assert sorted((f.path.name, f.kind) for f in findings) == [("dateilink.txt", "link"), ("gesperrt.txt", "unreadable"), ("link", "link")]


def test_a_folder_without_read_permission_is_reported_as_unreadable(tmp_path: Path) -> None:
    import os

    if os.geteuid() == 0:
        pytest.skip("als root sind Rechte wirkungslos")
    out = tmp_path / "out"
    locked = out / "gesperrt"
    locked.mkdir(parents=True)
    (locked / "x.txt").write_text(KEY, encoding="utf-8")
    locked.chmod(0)
    try:
        findings = live_guard.scan_findings([out], [KEY])
    finally:
        locked.chmod(0o755)
    assert [(f.path.name, f.kind) for f in findings] == [("gesperrt", "unreadable")]
