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
    """Rohtext ohne unsichtbare Formatzeichen (Kategorie `Cf`, die unsichtbaren `Mn` aus `_INVISIBLE_MARKS`) und ohne
    Steuerzeichen (Kategorie `Cc` außer `\\n` und `\\t`); daraus wird gerendert."""
    clean_to_raw: list[int]
    norm: str
    """Für den Scan: NFKC, Ziffern aller Schriften als ASCII, andere Zahlzeichen als `9`."""
    norm_start: list[int]
    norm_end: list[int]
    format_chars: int
    foreign_digits: frozenset[int] = frozenset()
    """`norm`-Indizes der ASCII-Ziffern, die aus einem Nicht-ASCII-Zeichen entstanden sind (Plan 11.2, Klasse 17 geht
    vor: eine Ausnahme gilt nie für so eine Stelle)."""
    breaks: frozenset[int] = frozenset()
    """`norm`-Indizes, vor denen ein unsichtbares Zeichen oder Steuerzeichen entfernt wurde (TA-20.1, M1). Der Wortscan
    prüft zusätzlich eine Fassung, in der jede dieser Stellen eine Wortgrenze ist (`zwei\\rPeers`)."""

    def raw_span(self, a: int, b: int) -> tuple[int, int]:
        """Norm-Bereich [a, b) → Rohtext-Bereich."""
        start = self.clean_to_raw[self.norm_start[a]]
        last = self.norm_end[b - 1] - 1
        return start, self.clean_to_raw[last] + 1

    def clean_span(self, a: int, b: int) -> tuple[int, int]:
        return self.norm_start[a], self.norm_end[b - 1]


_MARK_DIGITS = frozenset({*range(0xA8E0, 0xA8EA), *range(0x11366, 0x1136D)})
"""Sichtbare Ziffern der Kategorie `Mn` (kombinierende Devanagari- und Grantha-Ziffern, die 17 `M*`-Zeichen mit `DIGIT`
im Namen). Sie hängen am Vorzeichen davor und ergeben dennoch eine Ziffer (Plan 11.1 Punkt 2, Klasse 17)."""

_PICTOGRAPH_NUMBERS = frozenset({
    0x1F51F,                    # Emoji „10“ (keine Unicode-Zahl, aber eine)
    0x1F4AF, 0x1F51E, 0x1F522,  # 💯, 🔞, 🔢
    *range(0x2680, 0x2686),     # Würfelflächen ⚀–⚅
    0x1F19B, *range(0x1F19D, 0x1F1A5),  # 🆛 3D … 🆤 120P (Namen mit Zahl; 1F19C, 1F1A5–1F1AC nennen keine)
})
"""Geschlossene Liste von Bildzeichen mit sichtbarer Zahl (Plan 11.1 Punkt 2, Klasse 17, Erweiterung). Sie zählen wie
`🔟` als Zahlzeichen und falten zu `9`."""


def _is_number_char(ch: str) -> bool:
    """Nicht-ASCII-Zeichen, das für den Leser eine Zahl zeigt."""
    return not ch.isascii() and (ch.isnumeric() or ord(ch) in _MARK_DIGITS or ord(ch) in _PICTOGRAPH_NUMBERS)


def _norm_char(ch: str) -> str:
    if ch.isascii():
        return ch
    if ord(ch) in _MARK_DIGITS or ord(ch) in _PICTOGRAPH_NUMBERS:
        return "9"
    if ch.isdecimal():  # arabisch-indisch, Devanagari, Fullwidth …
        return str(unicodedata.decimal(ch))
    if ch.isnumeric():  # Hoch-/Tiefstellung, Brüche, ①, Ⅻ, CJK …
        folded = unicodedata.normalize("NFKC", ch)
        return folded if folded.isascii() and folded.isdigit() else "9"
    if ch == "٫":
        return ","
    if ch == "٬":
        return "."
    return unicodedata.normalize("NFKC", ch)


_INVISIBLE_MARKS = frozenset(
    {*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0), 0x034F, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F}
)
"""Unsichtbare Zeichen der Kategorie `Mn` ohne Zahlwert (Variantenzeichen U+FE00–U+FE0F und U+E0100–U+E01EF,
COMBINING GRAPHEME JOINER U+034F, Khmer U+17B4/U+17B5, mongolische Variantenzeichen U+180B–U+180D und U+180F). Sie werden wie `Cf` entfernt
und gezählt (Plan 11.1 Punkt 1): sonst zerreißen sie Zahlwörter und Ziffernfolgen und täuschen ein anderes Cluster vor."""


_INVISIBLE_LETTERS = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0})
"""Unsichtbare Buchstaben der Kategorie `Lo` (Hangul-Füllzeichen, Default_Ignorable_Code_Point; TA-20.1, N-2)."""


def _is_unassigned_ignorable(cp: int) -> bool:
    """Nicht zugewiesene Default_Ignorable_Code_Points (Kategorie `Cn`): U+2065, U+FFF0–U+FFF8, U+E0000–U+E0FFF."""
    return cp == 0x2065 or 0xFFF0 <= cp <= 0xFFF8 or 0xE0000 <= cp <= 0xE0FFF


def _is_invisible(ch: str) -> bool:
    """Zeichen, das beim Lesen nichts zeigt oder die Ausgabe verändert: `Cf`, `_INVISIBLE_MARKS`, unsichtbare Buchstaben
    (`_INVISIBLE_LETTERS`), nicht zugewiesene Default-Ignorable-Zeichen und Steuerzeichen (`Cc`: C0, DEL, C1) außer
    Zeilenumbruch und Tab (TA-20, O-33; TA-20.1, N-2). Steuerzeichen werden entfernt, nicht durch Leerzeichen
    ersetzt: so wachsen zerrissene Zahlwörter (`zw\\x00ei`) wieder zusammen, und `\\r`, `\\x1b[K` oder `\\b` können im
    gezeigten Text keine Zeile überschreiben."""
    category = unicodedata.category(ch)
    return (category == "Cf" or ord(ch) in _INVISIBLE_MARKS or ord(ch) in _INVISIBLE_LETTERS
            or (category == "Cc" and ch not in "\n\t") or (category == "Cn" and _is_unassigned_ignorable(ord(ch))))


def normalize(raw: str) -> Normalized:
    clean_chars: list[str] = []
    clean_to_raw: list[int] = []
    format_chars = 0
    gap_before: set[int] = set()  # `clean`-Indizes, vor denen etwas entfernt wurde
    previous = -1
    for i, ch in enumerate(raw):
        if _is_invisible(ch):
            format_chars += 1
            continue
        if i > previous + 1:
            gap_before.add(len(clean_chars))
        previous = i
        clean_chars.append(ch)
        clean_to_raw.append(i)
    clean = "".join(clean_chars)

    norm: list[str] = []
    norm_start: list[int] = []
    norm_end: list[int] = []
    foreign_digits: set[int] = set()
    breaks: set[int] = set()
    i = 0
    while i < len(clean):
        if i in gap_before and norm:
            breaks.add(len(norm))
        j = i + 1
        while j < len(clean) and unicodedata.category(clean[j]).startswith("M"):
            j += 1
        cluster = clean[i:j]
        base = cluster[0]
        # Zahlzeichen mit angehängtem Zeichen laufen über `_norm_char` (Plan 11.1 Punkt 2, Klasse 17); NFKC faltet
        # sie nicht, und der Anhang würde sonst die Fundstelle verdecken. Die angehängten Zeichen entfallen im `norm`.
        if len(cluster) == 1 or _is_number_char(base):
            # sichtbare Ziffern unter den angehängten Zeichen (Mn) bleiben Ziffern
            folded = _norm_char(base) + "".join("9" for m in cluster[1:] if ord(m) in _MARK_DIGITS)
        else:
            folded = unicodedata.normalize("NFKC", cluster)
        for ch in folded:
            foreign = not base.isascii()
            if _is_number_char(ch):  # auch, was erst NFKC zum Zahlzeichen macht (`⼆` → `二`)
                ch, foreign = _norm_char(ch), True
            if foreign and "0" <= ch <= "9":
                foreign_digits.add(len(norm))
            norm.append(ch)
            norm_start.append(i)
            norm_end.append(j)
        i = j
    return Normalized(raw, clean, clean_to_raw, "".join(norm), norm_start, norm_end, format_chars,
                      frozenset(foreign_digits), frozenset(breaks))


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


class BreakText(str):
    """Maskierter Text mit den Wortgrenzen aus `Normalized.breaks` (TA-20.1, M1). Ein `str`, damit `scan_numbers` seine
    Signatur behält; `scan_words` liest `breaks` per `getattr`."""

    breaks: frozenset[int] = frozenset()

    def __new__(cls, text: str, breaks: frozenset[int] = frozenset()) -> "BreakText":
        obj = super().__new__(cls, text)
        obj.breaks = breaks
        return obj


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

_DE_NUM = ("anderthalb", "eineinhalb", "einhalb", "zwölf", "zwolf", "zwoelf", "zwanzig", "dreißig", "dreissig", "vierzig", "fünfzig", "funfzig", "sechzehn", "siebzehn",
           "sechzig", "siebzig", "achtzig", "neunzig", "hundert", "tausend", "dutzend", "zwei", "zwo", "drei", "vier", "fünf",
           "funf", "sechs", "sieben", "acht", "neun", "zehn", "elf", "null", "eins")
_DE_GLUE = ("ein", "und", "komma")
_DE_SUFFIXES = ("", "e", "en", "er", "es", "em", "s", "mal", "fach", "fache", "fachen", "facher", "faches", "stellig",
                "stellige", "stelligen", "stelliger", "jährig", "jährige", "jährigen", "tägig", "tägige", "jahrig", "jahrige", "jahrigen", "tagig", "tagige")
_EN_NUM = frozenset("""two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen
seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds thousand thousands
zero dozen dozens million millions billion billions trillion trillions""".split())
_ENUM_ADVERBS = frozenset("""erstens zweitens drittens viertens fünftens funftens sechstens siebtens achtens neuntens zehntens
firstly secondly thirdly fourthly""".split())
_ORDINAL_STEMS = ("zweit", "dritt", "viert", "fünft", "funft", "sechst", "siebt", "neunt", "zehnt", "elft", "zwölft", "zwolft",
                  "zwanzigst", "hundertst", "tausendst")
_ORDINAL_EXCEPTIONS = re.compile(r"dritt(?:anbieter|land|länder|staat|staaten|mittel|partei|parteien|kunden|verkäufer|verkaufer)")
_EN_ORDINALS = frozenset("""third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth
fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth
ninetieth hundredth thousandth""".split())
_FRACTION = re.compile(
    r"(?:ein|zwei|drei|vier|fünf|funf|sechs|sieben|acht|neun|zehn)?(?:drittel|viertel|fünftel|funftel|sechstel|siebtel|"
    r"achtel|neuntel|zehntel|zwanzigstel|hundertstel|tausendstel)[ns]?")
_FRACTION_STEM_ENDINGS = ("zehn", "elf", "zwölf", "zwolf", "zwoelf")
_FRACTION_ST_ENDINGS = ("zig", "ßig", "ssig", "hundert", "tausend")
_EN_FOLD = re.compile(r"(?P<n>[a-z]+)fold")
_UNIT_COMPOUND = re.compile(r"(?P<n>[a-zäöüß]+?)(?:prozent|promill|basispunkt)")
_MAGNITUDE_PREFIXES = ("milliard", "million", "billion", "trillion", "billiard")

_UNIT_END = r"(?![^\W\d_])"  # Wortende: „Prozentsatz“, „Prozentangabe“ sind keine Einheit
_UNIT_END_CURRENCY = r"(?![\w]|-\w)"  # Währungen auch nicht vor Bindestrich + Wort: „Dollar-Kurs“, „Euro-Staat“
_W = r"[^\W\d_]+"
_W2 = (r"(?!(?:in|im|von|vom|an|am|um|auf|bei|für|fur|fuer|mit|pro|per|zwischen|aus|ohne|nach|über|uber|ueber|unter|zu|zum|"
       r"zur|der|des|dem|den|die|das|of|by|for|at|to|on|without|with|between|over|under|from|into|than|the)\b)[^\W\d_]+")
"""Wort für das Zweiwortfenster vor Prozent-Einheiten (TA-20.1, N-4): keine Präposition und kein Artikel, damit „eine Kennzahl in Prozent“
und „a measure in percent“ frei bleiben."""
_SEP = r"[\s\-]+"
_CURRENCY = rf"(?:u\.?s\.?[\s\-]?)?dollars?|euros?|cents?"
_DE_PCT_UNITS = (rf"(?:prozent(?:e[ns]?|s)?{_UNIT_END}|prozentpunkt\w*|promillepunkt\w*|basis[\s\-]?punkt\w*|"
                 rf"%[\s\-]?punkt\w*|promill(?:e[ns]?|s)?{_UNIT_END})")
_DE_CLOSED_UNITS = rf"(?:(?:{_CURRENCY}){_UNIT_END_CURRENCY})"
_DE_MAL = rf"mal{_UNIT_END}"
_DE_UNITS = rf"(?:{_DE_PCT_UNITS}|{_DE_CLOSED_UNITS}|{_DE_MAL})"
_DE_CLOSED_ADJECTIVE = r"(?:weiter|zus(?:ä|a)tzlich|einzig|knapp|gut|ganz|voll)(?:e[mnrs]?)?"
_DE_MAL_ADJECTIVE = r"(?:knapp|gut|ganz|voll)(?:e[mnrs]?)?"  # „ein weiteres Mal“ (Redewendung) bleibt frei
_DE_ART = r"\bein(?:e[mnrs]?)?"
_DE_ONE_UNIT = re.compile(
    rf"{_DE_ART}{_SEP}(?:{_W}{_SEP})?{_DE_PCT_UNITS}"  # ein beliebiges Wort: „um einen weiteren Prozentpunkt“
    rf"|{_DE_ART}{_SEP}{_W2}{_SEP}{_W2}{_SEP}{_DE_PCT_UNITS}"  # zwei: „um einen ganzen weiteren Prozentpunkt“
    rf"|{_DE_ART}{_SEP}(?:{_DE_CLOSED_ADJECTIVE}{_SEP})?{_DE_CLOSED_UNITS}"  # Mal, Währungen: geschlossene Liste
    rf"|{_DE_ART}{_SEP}(?:{_DE_MAL_ADJECTIVE}{_SEP})?{_DE_MAL}"
    rf"|\bein-(?:mal|(?:{_CURRENCY})){_UNIT_END}",  # „Ein-Dollar-Aktie“
    re.I)
_DE_HALF_UNIT = re.compile(rf"\bhalb(?:e[mnrs]?)?\s+{_DE_UNITS}", re.I)
_EN_PCT = (rf"(?:per[\s\-]?cent{_UNIT_END}|percentage[\s\-]+points?{_UNIT_END}|basis[\s\-]+points?{_UNIT_END}|"
           rf"(?-i:bps?){_UNIT_END})")
_EN_CLOSED_ADJECTIVE = r"(?:additional|extra|further|single|whole|more|full)"
_EN_AFTER_A_CURRENCY = r"(?=[.,;:)!?]|$|\s+(?:per|a|an|each|more|less|higher|lower|above|below|apiece)\b)"
_EN_ONE_UNIT = re.compile(
    rf"\b(?:one|an?){_SEP}(?:{_W}{_SEP})?{_EN_PCT}"  # „an additional percentage point“, „one more percent“
    rf"|\b(?:one|an?){_SEP}{_W2}{_SEP}{_W2}{_SEP}{_EN_PCT}"  # „an additional full percentage point“
    rf"|\bone{_SEP}(?:{_EN_CLOSED_ADJECTIVE}{_SEP})?(?:{_CURRENCY}){_UNIT_END_CURRENCY}"  # Währungen nur nach „one“
    rf"|\ban?{_SEP}(?:{_EN_CLOSED_ADJECTIVE}{_SEP})?(?:{_CURRENCY}){_EN_AFTER_A_CURRENCY}"  # „gained a dollar.“
    rf"|\b(?:one|an?){_SEP}(?-i:times)\b",  # „trades at one times sales“; „one-time charge“ bleibt frei
    re.I)
_EN_QUARTER_UNIT = re.compile(
    rf"\bquarter{_SEP}(?:points?{_UNIT_END}|percent{_UNIT_END}|percentage{_SEP}points?|per{_SEP}cent|basis{_SEP}points?)",
    re.I)
"""Einheiten nach „ein“/„halb“/„one“/„a“/„an“ (Klasse 11/14). Der Artikel steht in jedem Fall (`ein`, `eine`, `einem`, …),
Trenner ist Leerraum oder Bindestrich (`Ein-Prozent-Hürde`). Vor Prozent, Prozentpunkt, Basispunkt, Promille und den
englischen Gegenstücken darf genau EIN beliebiges Wort stehen (TA-20.1, H1); „Mal“ und Währungen kennen nur eine
geschlossene Liste von Zwischenwörtern („ein starker Dollar“ bleibt frei). Englische Währungen zählen nur nach „one“ (bekanntes
Restrisiko „a dollar“; Fehlalarme wie „on a dollar basis“ wären sonst häufig). Einheiten nur am Wortende (`Prozentsatz`,
`Prozentangabe` bleiben frei), Währungen nicht vor Bindestrich + Wort (`Dollar-Kurs`), außer der Artikel hängt mit Bindestrich
dran (`Ein-Dollar-Aktie`). Bekannter Fehlalarm: „mit einem Mal“ (Redewendung)."""

_PHRASES = [
    # (Muster, Art) — Vergleiche und Rechnungen in Worten (Klasse 14)
    (re.compile(r"\bdoppelt\s+(?:so\b|viel\w*|hoch\w*|gro(?:ß|ss)\w*|teuer\w*|stark\w*)", re.I), "multiplicative"),
    (re.compile(r"\bdoppelte[nrsm]?\b", re.I), "multiplicative"),
    (re.compile(r"\bhalb(?:e[nrsm]?)?\s+so\b", re.I), "multiplicative"),
    (re.compile(r"\bhalbier\w*|\bverdoppel\w*|\bverdreifach\w*|\bvervierfach\w*|\bverzehnfach\w*|\bh(?:ä|a)lfte\b", re.I),
     "multiplicative"),
    (re.compile(r"\btwice\b|\bhalf\b|\bhalves\b|\bhalved\b|\bquadrupl\w*|\btripl(?:e|ed|es|ing)\b", re.I),
     "multiplicative"),
    (re.compile(r"\bdoubl(?:e|ed|es|ing)\b(?!-?\s?(?:check|count))", re.I), "multiplicative"),
    (re.compile(r"\bquarters?\s+of\b", re.I), "fraction_word"),
    # „ein Prozent“, „einem halben Prozentpunkt“, „one percent“, „a single basis point“, „one-percent“, „halbes Prozent“:
    # die Zahl 1 (bzw. ½) als Artikel in jedem Fall, mit optionalem Adjektiv dazwischen (TA-20, Fable 02 Befund 3)
    (_DE_ONE_UNIT, "number_word"),
    (_EN_ONE_UNIT, "number_word"),
    (_DE_HALF_UNIT, "fraction_word"),
    (_EN_QUARTER_UNIT, "fraction_word"),
    (re.compile(r"\b(?:erst|zweit|dritt|viert)\w*\s+(?:quartal|halbjahr)\w*", re.I), "ordinal"),
]
_WORD = re.compile(r"[^\W\d_]+")
_SPACED_LETTERS = re.compile(r"(?<![^\W\d_])(?:[^\W\d_][ .\-]){2,}[^\W\d_](?![^\W\d_])")
"""„z w e i“, „e.i.n.s“: ein Zahlwort, Buchstabe für Buchstabe auseinandergezogen."""


@lru_cache(maxsize=4096)
def _is_german_cardinal(stem: str) -> bool:
    """Ganzes Wort aus Zahlbausteinen (`_DE_NUM`) und Bindegliedern (`_DE_GLUE`), mindestens ein Zahlbaustein."""
    if len(stem) < 3:
        return False
    reachable: list[set[bool]] = [set() for _ in range(len(stem) + 1)]
    reachable[0].add(False)
    for i in range(len(stem)):
        if not reachable[i]:
            continue
        for morpheme in (*_DE_NUM, *_DE_GLUE):
            if stem.startswith(morpheme, i):
                reachable[i + len(morpheme)] |= {has or morpheme in _DE_NUM for has in reachable[i]}
    return True in reachable[len(stem)]


@lru_cache(maxsize=4096)
def _is_german_number_word(token: str) -> bool:
    for suffix in _DE_SUFFIXES:
        if suffix and not token.endswith(suffix):
            continue
        if _is_german_cardinal(token[: len(token) - len(suffix)] if suffix else token):
            return True
    return False


_ORDINAL_PREFIX = re.compile(r"zehnt|zigst|ßigst|ssigst|hundertst|tausendst")


def _has_ordinal_prefix(token: str) -> bool:
    """Wort beginnt mit einer Ordinalzahl ab 13 aus zusammengesetzter Kardinalzahl (TA-20.1, M2): Kardinalzahl auf -zehn + `t`
    (`dreizehntgrößte`, `siebzehnte`) oder auf -zig/-hundert/-tausend + `st` (`einundzwanzigstbeste`, `dreizehntens`); der
    Rest ist beliebig (Endung, Superlativ, Adverb). Der Anfang muss als Ganzes eine Kardinalzahl sein: „Jahrzehnt“ bleibt
    frei, weil „jahrzehn“ keine ist. Die Sonderformen bis „zwölft“ stehen in `_ORDINAL_STEMS`."""
    for m in _ORDINAL_PREFIX.finditer(token):
        if _is_german_cardinal(token[: m.end() - (1 if m.group().endswith("zehnt") else 2)]):
            return True
    return False


def _is_compound_fraction(token: str) -> bool:
    """Bruchwort aus zusammengesetzter Kardinalzahl (TA-20.1): Kardinalzahl auf -zehn/-elf/-zwölf + `tel`
    (`sechzehntel`, `elftel`) oder auf -zig/-hundert/-tausend + `stel` (`einundzwanzigstel`, `fünfzigstel`), mit
    optionalem `n` (Mehrzahl) oder `s` (Genitiv). Der Stamm muss als Ganzes eine Kardinalzahl sein; Drittel bis Zehntel stehen in `_FRACTION`."""
    t = token[:-1] if token[-1:] in ("n", "s") else token  # Mehrzahl (-n) und Genitiv (-s)
    if t.endswith("tel") and t[:-3].endswith(_FRACTION_STEM_ENDINGS):
        return _is_german_cardinal(t[:-3])
    if t.endswith("stel") and t[:-4].endswith(_FRACTION_ST_ENDINGS):
        return _is_german_cardinal(t[:-4])
    return False


def _is_number_unit_compound(token: str) -> bool:
    """Zahlwort und Einheit in einem Wort (TA-20.1): `einprozentig`, `Fünfprozentmarke`, `halbprozentig`,
    `sechzehnprozentige`, `zweibasispunkt…`. Der Teil vor `prozent`/`promill`/`basispunkt` muss `ein`, `halb` oder
    ein Kardinalzahlwort sein; `Prozentsatz` und `Promillegrenze` ohne Zahl davor bleiben frei."""
    for m in _UNIT_COMPOUND.finditer(token):
        head = m.group("n")
        if head in ("ein", "halb") or _is_german_cardinal(head):
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
    if _FRACTION.fullmatch(t) or _is_compound_fraction(t):
        return "fraction_word"
    if _is_number_unit_compound(t):
        return "number_word"
    fold = _EN_FOLD.fullmatch(t)
    if fold and fold.group("n") in _EN_NUM:
        return "number_word"
    if t.startswith(_ORDINAL_STEMS) and not _ORDINAL_EXCEPTIONS.match(t) or _has_ordinal_prefix(t):
        return "ordinal"
    if t == "second" and not re.match(r"\s*[,;:]", following):
        return "ordinal"
    if t == "third" and re.match(r"-party|\s+part(?:y|ies)", following, re.I):
        return None
    if t in _EN_ORDINALS:
        return "ordinal"
    return None


@lru_cache(maxsize=4096)
def _strip_diacritic(ch: str) -> str:
    """Grundbuchstabe eines Buchstabens mit Diakritikum (NFKD, kombinierende Zeichen `Mn` weg), nur wenn genau ein
    Zeichen übrig bleibt; sonst das Zeichen selbst (`ß`, `æ`, `ø`, `ł`, Ligaturen …)."""
    if ch.isascii():
        return ch
    decomposed = unicodedata.normalize("NFKD", ch)
    base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    if len(base) == 1 and base != ch and len(base) < len(decomposed) and base.isalpha():
        return base
    return ch


def strip_diacritics(masked: str) -> str:
    """Zeichengenaue Kopie von `masked` ohne Diakritika (`zwéi` → `zwei`); Positionen bleiben 1:1 (Plan 11 Restrisiko
    `zwéi`, TA-17). `norm` selbst behält das `é`, weil Slots, Sitzungsbegriffe („Nestlé“) und Ausnahmen daran hängen."""
    return "".join(_strip_diacritic(ch) for ch in masked)


def _scan_words_in(masked: str) -> list[Finding]:
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
    return found


@dataclass
class _Variant:
    """Eine Fassung von `masked` für den Wortscan; `spans[i]` ist die Spanne in `masked`, aus der `text[i]` entstand."""

    text: str
    spans: list[tuple[int, int]]
    extend_marks: bool = False  # Fundstellen über angehängte kombinierende Zeichen hinaus verlängern


def _drop_marks(v: _Variant) -> _Variant:
    kept = [i for i, ch in enumerate(v.text) if not unicodedata.category(ch).startswith("M")]
    return _Variant("".join(v.text[i] for i in kept), [v.spans[i] for i in kept], True)


def _split_at_breaks(v: _Variant, breaks: frozenset[int]) -> _Variant:
    """Vor jeder Stelle, an der ein unsichtbares Zeichen entfernt wurde, ein Leerzeichen einfügen (TA-20.1, M1)."""
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for ch, span in zip(v.text, v.spans):
        if span[0] in breaks and chars and chars[-1] != " ":
            chars.append(" ")
            spans.append((span[0], span[0]))
        chars.append(ch)
        spans.append(span)
    return _Variant("".join(chars), spans, v.extend_marks)


_UMSCHRIFT = {"u": "ü", "o": "ö", "a": "ä"}


def _fold_umschrift(v: _Variant) -> _Variant:
    """`ue`/`oe`/`ae` → `ü`/`ö`/`ä` (TA-20.1, M3: `fuenf`, `zwoelfte`); die Länge ändert sich, `spans` hält die Rohstellen."""
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(v.text):
        ch = v.text[i]
        if ch.lower() in _UMSCHRIFT and v.text[i + 1: i + 2].lower() == "e":
            folded = _UMSCHRIFT[ch.lower()]
            chars.append(folded.upper() if ch.isupper() else folded)
            spans.append((v.spans[i][0], v.spans[i + 1][1]))
            i += 2
        else:
            chars.append(ch)
            spans.append(v.spans[i])
            i += 1
    return _Variant("".join(chars), spans, v.extend_marks)


def scan_words(masked: str) -> list[Finding]:
    """Zahlwörter ab „zwei“, Größenordnungswörter, Ränge und Vergleiche in Worten (Klassen 11–14).

    Der Abgleich läuft auf `masked` (damit `fünf`, `zwölf`, `dreißig` über ihre Umlaut-Formen gefunden werden) und auf
    Fassungen davon, deren Fundstellen vereinigt werden (TA-17, TA-20.1): ohne Diakritika (`zwéi`, `dréißig`), ohne
    kombinierende Zeichen, mit einer Wortgrenze an jeder Stelle, an der ein unsichtbares Zeichen entfernt wurde
    (`zwei\\rPeers`, nur bei `BreakText`) und mit aufgelöster Umschrift (`fuenf` → `fünf`)."""
    breaks: frozenset[int] = getattr(masked, "breaks", frozenset())
    text = str(masked)
    base = _Variant(text, [(i, i + 1) for i in range(len(text))])
    variants = [base]
    if not text.isascii():  # ohne Nicht-ASCII-Zeichen gibt es nichts zu entfernen
        folded = strip_diacritics(text)
        if folded != text:
            variants.append(_Variant(folded, base.spans))
        core = variants[-1]
        if any(unicodedata.category(ch).startswith("M") for ch in core.text):
            core = _drop_marks(core)
            variants.append(core)
    else:
        core = base
    extra = [core]
    if breaks:
        extra.append(_split_at_breaks(core, breaks))
    if re.search(r"[uoa]e", core.text, re.I):
        extra += [_fold_umschrift(v) for v in extra]
    variants += extra[1:]
    found: list[Finding] = []
    for variant in variants:
        for f in _scan_words_in(variant.text):
            start, end = variant.spans[f.start][0], variant.spans[f.end - 1][1]
            if variant.extend_marks:
                while end < len(text) and unicodedata.category(text[end]).startswith("M"):
                    end += 1
            mapped = Finding(start, end, f.kind)
            if mapped not in found:
                found.append(mapped)
    return _merge_adjacent_numbers(text, sorted(found, key=lambda f: (f.start, f.end)))


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
