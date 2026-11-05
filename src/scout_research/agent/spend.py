"""Budgetdeckel je Lauf und je Tag (Plan 12.8, Schritt 9a, TA-12b).

Zusätzlich zur Sitzungsgrenze (`LoopConfig.cost_limit_usd`, L1) gelten zwei Grenzen, es gilt die strengste: die Summe
**eines Laufs** (im Prozess, über mehrere Sitzungen geteilt) und die Summe **des UTC-Tages** über alle Läufe, geführt in
einer kleinen JSON-Datei (`SpendLedger`). Ist das Kostenbuch vorhanden, aber nicht lesbar oder nicht schreibbar, gilt der
Lauf als gesperrt (fail closed): Ohne Kostenbuch lässt sich der Tagesdeckel nicht prüfen. Mehrere Prozesse dürfen
gleichzeitig buchen: Der ganze Zyklus Lesen-Ändern-Schreiben läuft unter einer exklusiven Dateisperre (`flock`).

Vorprüfung (O-24, schlechtester Fall): Vor jedem Modellaufruf zählt zu den gebuchten Kosten die Obergrenze des nächsten
Aufrufs; wird die Grenze damit erreicht, findet der Aufruf nicht statt.

Kein Netz, kein `print`; nur Dateizugriff auf das Kostenbuch.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

LOCK_TIMEOUT_S = 5.0
"""Frist, in der die Sperre des Kostenbuchs erreichbar sein muss; danach `LedgerError` (-> `budget_ledger`)."""
_LOCK_POLL_S = 0.01

BudgetReason = Literal["budget_run", "budget_day", "budget_ledger"]


class LedgerError(Exception):
    """Das Kostenbuch ist vorhanden, aber nicht lesbar oder nicht gültig (oder nicht schreibbar)."""


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


class SpendLedger:
    """Tagessummen in US-Dollar als `{"YYYY-MM-DD": summe}` (UTC-Datum). Fehlende Datei = 0."""

    def __init__(self, path: Path, clock_utc_date: Callable[[], date] = _utc_today,
                 lock_timeout_s: float = LOCK_TIMEOUT_S) -> None:
        self.path = Path(path)
        self._today = clock_utc_date
        self._lock_timeout_s = lock_timeout_s

    def _acquire(self, fd: int) -> None:
        """Exklusive Sperre ohne Blockieren, wiederholt bis zur Frist; danach `LedgerError`."""
        deadline = time.monotonic() + self._lock_timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LedgerError("Kostenbuch gesperrt (Frist überschritten)") from None
                time.sleep(_LOCK_POLL_S)

    def _read(self) -> dict[str, float]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError) as exc:
            raise LedgerError(f"Kostenbuch nicht lesbar ({type(exc).__name__})") from None
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError, OverflowError):  # tief verschachtelt bzw. riesige Zahl: wie kaputtes JSON
            raise LedgerError("Kostenbuch ist kein gültiges JSON") from None
        if not isinstance(data, dict):
            raise LedgerError("Kostenbuch ist kein JSON-Objekt")
        try:
            for key, value in data.items():
                ok = (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                      and value >= 0)
                if not isinstance(key, str) or not ok:
                    raise LedgerError("Kostenbuch enthält einen ungültigen Eintrag")
            return {k: float(v) for k, v in data.items()}
        except (RecursionError, OverflowError):  # z. B. ganze Zahl jenseits von float
            raise LedgerError("Kostenbuch enthält einen ungültigen Eintrag") from None

    def _key(self) -> str:
        return self._today().isoformat()

    def day_total(self) -> float:
        """Summe des heutigen UTC-Tages."""
        return self._read().get(self._key(), 0.0)

    def add(self, cost_usd: float) -> None:
        """Addiert für heute und schreibt sofort. Der ganze Zyklus Lesen-Ändern-Schreiben läuft unter einer exklusiven
        `flock`-Sperre (mit Frist, `LOCK_TIMEOUT_S`) auf einer eigenen Sperrdatei neben dem Buch; geschrieben wird in eine eindeutige temp-Datei im
        selben Ordner und per `os.replace` ersetzt (bei Fehler wird die temp-Datei entfernt)."""
        lock_path = self.path.with_name(self.path.name + ".lock")
        tmp_name: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, "a", encoding="utf-8") as lock:
                self._acquire(lock.fileno())  # wird mit dem Schließen der Datei freigegeben
                data = self._read()
                key = self._key()
                data[key] = data.get(key, 0.0) + cost_usd
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False,
                                                 prefix=self.path.name + ".", suffix=".tmp") as tmp:
                    tmp_name = tmp.name
                    tmp.write(json.dumps(data, sort_keys=True))
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_name, self.path)
                tmp_name = None
        except OSError as exc:
            raise LedgerError(f"Kostenbuch nicht schreibbar ({type(exc).__name__})") from None
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass


class SpendBudget:
    """Lauf- und Tagesgrenze. Ein Objekt gehört zu **einem Lauf** und wird von allen Sitzungen dieses Laufs geteilt."""

    def __init__(self, run_limit_usd: float, day_limit_usd: float, ledger: SpendLedger) -> None:
        self.run_limit_usd = run_limit_usd
        self.day_limit_usd = day_limit_usd
        self.ledger = ledger
        self.run_spent = 0.0
        self._ledger_failed = False

    @classmethod
    def from_settings(cls, settings) -> SpendBudget:
        return cls(settings.budget_run_usd, settings.budget_day_usd,
                   SpendLedger(Path(settings.runs_dir) / "spend_ledger.json"))

    def charge(self, cost_usd: float) -> None:
        """Bucht in den Lauf und ins Kostenbuch. Scheitert das Buch, ist der Lauf danach gesperrt (`budget_ledger`)."""
        if cost_usd <= 0:
            return
        self.run_spent += cost_usd
        try:
            self.ledger.add(cost_usd)
        except LedgerError:
            self._ledger_failed = True
        except BaseException:  # z. B. Strg+C: Buchung ungewiss, der Lauf ist danach gesperrt (fail closed)
            self._ledger_failed = True
            raise

    def day_total(self) -> float | None:
        """Tagessumme oder `None`, wenn das Kostenbuch nicht lesbar ist."""
        try:
            return self.ledger.day_total()
        except LedgerError:
            return None

    def exhausted(self, next_call_usd: float = 0.0) -> BudgetReason | None:
        """Grenzwert gilt mit `>=`. `next_call_usd` ist die Obergrenze des nächsten Aufrufs (Vorprüfung, O-24): gebuchte
        Kosten plus diese Obergrenze dürfen die Grenze nicht erreichen. Nach einem Aufruf (Standard 0) zählt nur Gebuchtes."""
        if self.run_spent + next_call_usd >= self.run_limit_usd:
            return "budget_run"
        if self._ledger_failed:
            return "budget_ledger"
        total = self.day_total()
        if total is None:
            return "budget_ledger"
        if total + next_call_usd >= self.day_limit_usd:
            return "budget_day"
        return None
