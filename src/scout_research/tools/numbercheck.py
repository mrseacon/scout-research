"""L3 — Backstop gegen nackte Zahlen (Phase-3-Plan, Abschnitt 11.1/11.2; Schritt 6).

Reine Funktionen ohne Sitzungszugriff: Normalisierung des Modelltexts (mit Rückabbildung auf die Rohtext-Positionen),
die Ausnahmen der Klassen 1–6 (Formnamen, Warnungs-IDs, Listennummern, Namen der Sitzung, Fachbegriffe, Kennungen)
und der Scan nach Ziffern und Zahlwörtern. Kein typisierter Abgleich: seit Entscheidung O1 B darf in keinem Modelltext
eine Zahl stehen, die nicht aus einem Slot kommt.

Leitgedanke (Plan 11.2): Ein Durchlass (erfundene oder falsch zugeordnete Zahl) ist teuer und unsichtbar, ein
Fehlalarm kostet eine Korrekturrunde. Im Zweifel wird blockiert, außer die Ausnahme ist eng, geschlossen und häufig
nötig.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

PLACEHOLDER = ""
"""Ersatzzeichen für maskierte Bereiche (Slots, Ausnahmen): trennt Wörter und Zahlenfolgen, ist weder Ziffer noch
Buchstabe noch Leerraum."""


# --- Normalisierung ----------------------------------------------------------------------------------------------


@dataclass
class Normalized:
    raw: str
    clean: str
    """Rohtext ohne unsichtbare Formatzeichen (Kategorie `Cf`); daraus wird gerendert."""
    clean_to_raw: list[int]
    norm: str
    """Für den Scan: NFKC, Ziffern aller Schriften als ASCII, andere Zahlzeichen als `9`."""
    norm_start: list[int]
    norm_end: list[int]
    format_chars: int

    def raw_span(self, a: int, b: int) -> tuple[int, int]:
        """Norm-Bereich [a, b) → Rohtext-Bereich."""
        start = self.clean_to_raw[self.norm_start[a]]
        last = self.norm_end[b - 1] - 1
        return start, self.clean_to_raw[last] + 1

    def clean_span(self, a: int, b: int) -> tuple[int, int]:
        return self.norm_start[a], self.norm_end[b - 1]


def _norm_char(ch: str) -> str:
    if ch.isascii():
        return ch
    if ch.isdecimal():  # arabisch-indisch, Devanagari, Fullwidth …
        return str(unicodedata.decimal(ch))
    if ch == "\U0001F51F":  # Emoji „10“ (keine Unicode-Zahl, aber eine)
        return "9"
    if ch.isnumeric():  # Hoch-/Tiefstellung, Brüche, ①, Ⅻ, CJK …
        folded = unicodedata.normalize("NFKC", ch)
        return folded if folded.isascii() and folded.isdigit() else "9"
    if ch == "٫":
        return ","
    if ch == "٬":
        return "."
    return unicodedata.normalize("NFKC", ch)


def normalize(raw: str) -> Normalized:
    clean_chars: list[str] = []
    clean_to_raw: list[int] = []
    format_chars = 0
    for i, ch in enumerate(raw):
        if unicodedata.category(ch) == "Cf":
            format_chars += 1
            continue
        clean_chars.append(ch)
        clean_to_raw.append(i)
    clean = "".join(clean_chars)

    norm: list[str] = []
    norm_start: list[int] = []
    norm_end: list[int] = []
    i = 0
    while i < len(clean):
        j = i + 1
        while j < len(clean) and unicodedata.category(clean[j]).startswith("M"):
            j += 1
        cluster = clean[i:j]
        folded = _norm_char(cluster) if len(cluster) == 1 else unicodedata.normalize("NFKC", cluster)
        for ch in folded:
            norm.append(ch)
            norm_start.append(i)
            norm_end.append(j)
        i = j
    return Normalized(raw, clean, clean_to_raw, "".join(norm), norm_start, norm_end, format_chars)


# --- Ausnahmen (Klassen 1–6) --------------------------------------------------------------------------------------

_DASH = "-‐‑–"
FORM_NAMES = re.compile(
    rf"(?<![A-Za-z0-9])(?:10[{_DASH}]KT|10[{_DASH}]K|10[{_DASH}]Q|8[{_DASH}]K|6[{_DASH}]K|20[{_DASH}]F|40[{_DASH}]F|"
    rf"11[{_DASH}]K|S[{_DASH}][14]|DEF\s14A|Form\s[345])(?:/A)?(?![A-Za-z0-9])", re.IGNORECASE)
"""Klasse 1: geschlossene Liste, **mit** Bindestrich. „10K“ ohne Bindestrich ist eine Zahl (10.000)."""

_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9])[WP][0-9]{1,3}(?![A-Za-z0-9])")
DOMAIN_TERMS = re.compile(r"(?<![A-Za-z0-9])(?:B2B2C|B2B|B2C|D2C|P2P|2D|3D|4G|5G)(?![A-Za-z0-9])")
"""Klasse 5 (O7): feste Liste, nur per Code erweiterbar. Produktnamen mit Ziffern gehören nicht dazu."""

_LIST_NUMBER = re.compile(r"^[ \t]*(?:[-*+•][ \t]+)?(?P<n>[1-9][0-9]?)(?P<sep>[.)])(?=[ \t])", re.MULTILINE)
_UNIT_AFTER_LIST_NUMBER = re.compile(
    r"[ \t]+(?:mio|mrd|tsd|usd|eur|bn|mn|prozent|percent|million|billion|milliard|dollar|euro|u\.s|[%$€£])", re.IGNORECASE)
_CIK = re.compile(r"(?<![A-Za-z0-9])CIK[  ]+(?P<v>[0-9]{10})(?![0-9])")
_SIC = re.compile(r"(?<![A-Za-z0-9])SIC(?:-Code)?[  ]+(?P<v>[0-9]{4})(?![0-9])")
_ACCESSION = re.compile(r"(?<![0-9])[0-9]{10}-[0-9]{2}-[0-9]{6}(?![0-9])")


@dataclass(frozen=True)
class Identifiers:
    """Kennungen, die in Tool-Ergebnissen der Sitzung vorkommen (Klasse 6)."""

    ciks: frozenset[str] = frozenset()
    sic_codes: frozenset[str] = frozenset()
    accessions: frozenset[str] = frozenset()


def exempt_spans(
    norm: str, warning_ids: frozenset[str], identifiers: Identifiers, trigger_terms: tuple[str, ...]
) -> list[tuple[int, int]]:
    """Bereiche der normalisierten Zeichenkette, die keine Zahlen im Sinne des Backstops sind."""
    spans: list[tuple[int, int]] = []
    for m in FORM_NAMES.finditer(norm):
        spans.append(m.span())
    for m in _ID_TOKEN.finditer(norm):
        if m.group(0) in warning_ids:
            spans.append(m.span())
    for m in _LIST_NUMBER.finditer(norm):
        if int(m.group("n")) <= 20 and not _UNIT_AFTER_LIST_NUMBER.match(norm, m.end()):
            spans.append((m.start("n"), m.end("sep")))
    for m in DOMAIN_TERMS.finditer(norm):
        spans.append(m.span())
    for m in _CIK.finditer(norm):
        if m.group("v") in identifiers.ciks:
            spans.append(m.span())
    for m in _SIC.finditer(norm):
        if m.group("v") in identifiers.sic_codes:
            spans.append(m.span())
    for m in _ACCESSION.finditer(norm):
        if m.group(0) in identifiers.accessions:
            spans.append(m.span())
    for term in trigger_terms:
        # Namen mit Ziffer („3M“) gelten in jeder Schreibweise; Namen mit Zahlwort nur so, wie die Daten sie führen
        flags = re.IGNORECASE if any(ch.isdigit() for ch in term) else 0
        for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", norm, flags):
            spans.append(m.span())
    return spans


def mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = PLACEHOLDER
    return "".join(chars)


# --- Ziffern -----------------------------------------------------------------------------------------------------

_DIGIT_RUN = re.compile(r"[0-9](?:[  '’_.,:/\-–]*[0-9])*")
"""Eine Zahl, auch auseinandergezogen („1 2 . 5“), mit Daten („28.11.2025“, „2025-11-28“) und Brüchen („1/2“)."""
_LEFT = re.compile(r"[^\W\d_]|[$€£#+−±~≈]")
_RIGHT = re.compile(r"[^\W\d_]|[%×²³]")


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    kind: str
    """digits | number_word | magnitude_word | ordinal | fraction_word | multiplicative"""


def scan_digits(masked: str, invalid_slots: list[tuple[int, int]] = ()) -> list[Finding]:
    """Jede Ziffernfolge außerhalb von Ausnahmen und gültigen Slots. In ungültigen Slots zählen nur reine Zahlen
    (nicht Kennungen wie „W9“): Der ungültige Slot wird ohnehin abgewiesen."""
    found: list[Finding] = []
    for m in _DIGIT_RUN.finditer(masked):
        a, b = m.span()
        if a > 0 and any(s <= a < e for s, e in invalid_slots):
            before = masked[a - 1]
            if before.isalpha() or before == "_":
                continue
        left = a
        while left > 0 and a - left < 6 and _LEFT.fullmatch(masked[left - 1]):
            left -= 1
        right = b
        while right < len(masked) and right - b < 12 and _RIGHT.fullmatch(masked[right]):
            right += 1
        found.append(Finding(left, right, "digits"))
    return found


# --- Zahlwörter, Größenordnungen, Ränge, Vergleiche ---------------------------------------------------------------

_DE_NUM = ("anderthalb", "eineinhalb", "einhalb", "zwölf", "zwoelf", "zwanzig", "dreißig", "dreissig", "vierzig", "fünfzig", "funfzig", "sechzig",
           "siebzig", "achtzig", "neunzig", "hundert", "tausend", "dutzend", "zwei", "zwo", "drei", "vier", "fünf",
           "funf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "null", "eins")
_DE_GLUE = ("ein", "und", "komma")
_DE_SUFFIXES = ("", "e", "en", "er", "es", "em", "s", "mal", "fach", "fache", "fachen", "facher", "faches", "stellig",
                "stellige", "stelligen", "stelliger", "jährig", "jährige", "jährigen", "tägig", "tägige")
_EN_NUM = frozenset("""two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen
seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds thousand thousands
zero dozen dozens million millions billion billions trillion trillions""".split())
_ENUM_ADVERBS = frozenset("""erstens zweitens drittens viertens fünftens sechstens siebtens achtens neuntens zehntens
firstly secondly thirdly fourthly""".split())
_ORDINAL_STEMS = ("zweit", "dritt", "viert", "fünft", "funft", "sechst", "siebt", "neunt", "zehnt", "elft", "zwölft",
                  "zwanzigst", "hundertst", "tausendst")
_ORDINAL_EXCEPTIONS = re.compile(r"dritt(?:anbieter|land|länder|staat|staaten|mittel|partei|parteien|kunden|verkäufer)")
_EN_ORDINALS = frozenset("""third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth twentieth hundredth
thousandth""".split())
_FRACTION = re.compile(
    r"(?:ein|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn)?(?:drittel|viertel|fünftel|funftel|sechstel|siebtel|"
    r"achtel|neuntel|zehntel|zwanzigstel|hundertstel|tausendstel)n?")
_MAGNITUDE_PREFIXES = ("milliard", "million", "billion", "trillion", "billiard")

_PHRASES = [
    # (Muster, Art) — Vergleiche und Rechnungen in Worten (Klasse 14)
    (re.compile(r"\bdoppelt\s+(?:so\b|viel\w*|hoch\w*|gro(?:ß|ss)\w*|teuer\w*|stark\w*)", re.I), "multiplicative"),
    (re.compile(r"\bdoppelte[nrsm]?\b", re.I), "multiplicative"),
    (re.compile(r"\bhalb(?:e[nrsm]?)?\s+so\b", re.I), "multiplicative"),
    (re.compile(r"\bhalbier\w*|\bverdoppel\w*|\bverdreifach\w*|\bvervierfach\w*|\bverzehnfach\w*|\bhälfte\b", re.I),
     "multiplicative"),
    (re.compile(r"\btwice\b|\bhalf\b|\bhalves\b|\bhalved\b|\bquadrupl\w*|\btripl(?:e|ed|es|ing)\b", re.I),
     "multiplicative"),
    (re.compile(r"\bdoubl(?:e|ed|es|ing)\b(?!-?\s?(?:check|count))", re.I), "multiplicative"),
    (re.compile(r"\bquarters?\s+of\b", re.I), "fraction_word"),
    # „ein Prozent“, „one percent“: die Zahl 1 als Artikel
    (re.compile(r"\b(?:ein|eine|one)\s+(?:prozent|percent|prozentpunkt\w*|basispunkt\w*|percentage\s+points?|"
                r"basis\s+points?)", re.I), "number_word"),
    (re.compile(r"\b(?:erst|zweit|dritt|viert)\w*\s+(?:quartal|halbjahr)\w*", re.I), "ordinal"),
]
_WORD = re.compile(r"[^\W\d_]+")
_SPACED_LETTERS = re.compile(r"(?<![^\W\d_])(?:[^\W\d_][ .\-]){2,}[^\W\d_](?![^\W\d_])")
"""„z w e i“, „e.i.n.s“: ein Zahlwort, Buchstabe für Buchstabe auseinandergezogen."""


@lru_cache(maxsize=4096)
def _is_german_number_word(token: str) -> bool:
    for suffix in _DE_SUFFIXES:
        stem = token[: len(token) - len(suffix)] if suffix else token
        if suffix and not token.endswith(suffix):
            continue
        if len(stem) < 3:
            continue
        reachable: list[set[bool]] = [set() for _ in range(len(stem) + 1)]
        reachable[0].add(False)
        for i in range(len(stem)):
            if not reachable[i]:
                continue
            for morpheme in (*_DE_NUM, *_DE_GLUE):
                if stem.startswith(morpheme, i):
                    reachable[i + len(morpheme)] |= {has or morpheme in _DE_NUM for has in reachable[i]}
        if True in reachable[len(stem)]:
            return True
    return False


def _classify_token(token: str, following: str) -> str | None:
    t = token.lower()
    if t in _ENUM_ADVERBS or t in ("ein", "eine", "einen", "einem", "einer", "eines", "one", "single", "einzig",
                                   "beide", "beiden", "both", "kein", "keine", "none"):
        return None
    if t.startswith(_MAGNITUDE_PREFIXES):
        return "magnitude_word"
    if t in _EN_NUM or _is_german_number_word(t):
        return "number_word"
    if _FRACTION.fullmatch(t):
        return "fraction_word"
    if t.startswith(_ORDINAL_STEMS) and not _ORDINAL_EXCEPTIONS.match(t):
        return "ordinal"
    if t == "second" and not re.match(r"\s*[,;:]", following):
        return "ordinal"
    if t == "third" and re.match(r"-party|\s+part(?:y|ies)", following, re.I):
        return None
    if t in _EN_ORDINALS:
        return "ordinal"
    return None


def scan_words(masked: str) -> list[Finding]:
    """Zahlwörter ab „zwei“, Größenordnungswörter, Ränge und Vergleiche in Worten (Klassen 11–14)."""
    found: list[Finding] = []
    phrase_spans: list[tuple[int, int]] = []
    for pattern, kind in _PHRASES:
        for m in pattern.finditer(masked):
            found.append(Finding(m.start(), m.end(), kind))
            phrase_spans.append(m.span())
    for m in _SPACED_LETTERS.finditer(masked):
        joined = re.sub(r"[ .\-]", "", m.group(0))
        if _classify_token(joined, "") == "number_word":
            found.append(Finding(m.start(), m.end(), "number_word"))
            phrase_spans.append(m.span())
    for m in _WORD.finditer(masked):
        if any(a <= m.start() < b for a, b in phrase_spans):
            continue
        kind = _classify_token(m.group(0), masked[m.end(): m.end() + 12])
        if kind is not None:
            found.append(Finding(m.start(), m.end(), kind))
    return _merge_adjacent_numbers(masked, sorted(found, key=lambda f: (f.start, f.end)))


def _merge_adjacent_numbers(text: str, findings: list[Finding]) -> list[Finding]:
    """Wortfundstellen, die nur durch Bindestrich, Leerraum oder „und/and“ getrennt sind, bilden eine Fundstelle
    („Three quarters“, „doppelt so hoch“)."""
    merged: list[Finding] = []
    for f in findings:
        if merged:
            last = merged[-1]
            gap = text[last.end: f.start]
            if f.start < last.end or re.fullmatch(r"[\s\-]*(?:und|and)?[\s\-]*", gap, re.I):
                merged[-1] = Finding(last.start, max(last.end, f.end), last.kind)
                continue
        merged.append(f)
    return merged


def scan_numbers(masked: str, invalid_slots: list[tuple[int, int]] = ()) -> list[Finding]:
    """Alle Fundstellen nackter Zahlen in einem maskierten Text, zusammengeführt bei Überlappung."""
    findings = sorted([*scan_digits(masked, invalid_slots), *scan_words(masked)], key=lambda f: (f.start, f.end))
    merged: list[Finding] = []
    for f in findings:
        if merged and f.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Finding(last.start, max(last.end, f.end), last.kind)
        else:
            merged.append(f)
    return merged


def has_trigger(term: str) -> bool:
    """Ob ein Name der Sitzung selbst nach einer Zahl aussieht („3M“, „Twenty-First …“) und deshalb als Ausnahme
    gebraucht wird."""
    return bool(scan_numbers(normalize(term).norm))
