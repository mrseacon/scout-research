"""Unabhängige, bewusst grobe Prüfung der gezeigten Texte (Plan 12.12, TA-20.3, Review Fable 06 N-6).

`_b1` in `eval/metrics.py` zählt nur, was `check_text` selbst als Verstoß meldet; ein Text, den die Prüfung übersieht, stünde
als „B1 erfüllt“ im Bericht. Dieses Modul schaut deshalb ein zweites Mal auf die gezeigten Texte, **ohne Code der ersten
Prüfung**: Es importiert nichts aus `scout_research.tools.numbercheck` oder `commentary` (Architekturtest) und hat eigene
Zahlwörter, einen eigenen Generator statt Listen aus dem Produktionscode.

Je gezeigtem Eintrag des Dumps (`shown/*.json`: `kind`, `text`, optional `model_text`):
- mit `model_text` (Rohtext des Modells mit Slots): zuerst jedes `[[…]]` entfernen, dann den Text mit NFKD normalisieren und
  Mn/Cf/unsichtbare Zeichen entfernen (`zwéi`, `zw<ZWSP>ei`, `ｚｗｅｉ`). Danach melden: jede Ziffer (`str.isnumeric`), jedes Zahlwort
  (Kardinalzahlen 0–100 und Zehnerpotenzen, de/en, Kleinschreibung und Umlaut-Faltung; ein/eine/one/a bleiben erlaubt,
  Plan 11.2 Klasse 11), eine eigene kleine Liste für Ordinal- und Bruchwörter, Multiplikative, Dutzend und Größenordnungs-
  Abkürzungen, und jedes Wort, das einen Kardinalstamm ab „zwei“ als Präfix oder Suffix trägt und länger ist (`zweiPeers`,
  `fünfjährig`). Die Teilwort-Regel liefert Fehlalarme (`Revier`, `Nacht`); das ist gewollt, nur wenige Wörter sind ausgenommen;
- ohne `model_text` (feste Texte, `notice`): jede Ziffer im `text`; feste Texte sind per Konstruktion zahlenfrei (U-F1);
- `commentary_withheld` (zurückgehaltener Kommentar; der Rohtext ist der abgelehnte und wird nicht gescannt, die Tabelle
  stammt aus dem Code): keine Ziffern, nur die Zeichenprüfung;
- in jedem `text`: Steuerzeichen (Cc außer Zeilenumbruch und Tab) und unsichtbare Zeichen (Cf, U+3164, U+115F, U+1160, U+FFA0).

Bewusst nicht abgedeckt (`NOT_COVERED_NOTE`): Namen und Ticker, Formnamen wie 10-K, Warnungs-IDs. Sie erzeugen Treffer; das ist gewollt.
Treffer heißen „manuell prüfen“. Sie ändern kein B-Ergebnis und keinen Exit-Code. Der Ausschnitt eines Treffers hat höchstens
24 Zeichen und stammt aus dem Dump, der schon geschwärzt ist.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

SNIPPET_MAX = 24
_SLOT = re.compile(r"\[\[.*?\]\]", re.DOTALL)
_TOKEN = re.compile(r"[^\W\d_]+")
_INVISIBLE = frozenset("\u3164\u115f\u1160\uffa0")
_ALLOWED = frozenset(("ein", "eine", "one", "a"))


@dataclass(frozen=True)
class Hit:
    entry: int
    """Index des Eintrags im Dump."""
    kind: str
    """`ziffer`, `zahlwort`, `teilwort`, `steuerzeichen` oder `unsichtbar`."""
    snippet: str


NOT_COVERED_NOTE = ("Die grobe Prüfung deckt Namen und Ticker, Formnamen wie 10-K und Warnungs-IDs bewusst nicht ab; "
                    "sie erzeugen dort Treffer, das ist gewollt.")


def _fold(word: str) -> str:
    """Kleinschreibung, ß→ss, NFKD ohne kombinierende Zeichen (ä→a, é→e, Vollbreite und mathematische Buchstaben → ASCII)."""
    return "".join(c for c in unicodedata.normalize("NFKD", word.lower().replace("ß", "ss")) if not unicodedata.combining(c))


def _variants(word: str) -> set[str]:
    """Ein Wort in seinen Schreibvarianten (ü und ue, ö und oe, ä und ae). Nur für die Listen, nie für Tokens: sonst würde
    `neuen` zu `neun`."""
    return {_fold(word), _fold(word.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue"))}


def _strip_invisible(text: str) -> str:
    """Mn, Cf und die unsichtbaren Füllzeichen entfernen (für die Ziffernsuche: NFKD macht aus `Ⅻ` Buchstaben)."""
    return "".join(c for c in text if unicodedata.category(c) not in ("Mn", "Cf") and c not in _INVISIBLE)


def _normalize(text: str) -> str:
    """NFKD, dann Mn, Cf und die unsichtbaren Füllzeichen entfernen."""
    return _strip_invisible(unicodedata.normalize("NFKD", text))


_DE_UNITS = ["", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun"]
_DE_TEENS = ["zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
_DE_TENS = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig", 7: "siebzig", 8: "achtzig", 9: "neunzig"}
_EN_UNITS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_EN_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}
_POWERS = ["hundert", "tausend", "million", "millionen", "milliarde", "milliarden", "billion", "billionen", "billiarde",
           "trillion", "hundred", "thousand", "billions", "millions", "trillions", "zehntausend", "hunderttausend"]
_DE_ORD = ["zweit", "dritt", "viert", "fünft", "sechst", "siebt", "acht", "neunt", "zehnt", "elft", "zwölft"]
_EN_ORD = ["second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth",
           "twentieth", "thirtieth", "fortieth", "fiftieth", "hundredth", "thousandth"]
_ENDINGS = ["", "e", "en", "er", "es", "em", "ens"]
_OTHER = ["hälfte", "hälften", "halb", "halbe", "halben", "halber", "halbes", "anderthalb", "half", "halves", "quarter",
          "quarters", "third", "thirds", "twice", "dutzend", "dutzende", "dutzenden", "dozen", "dozens", "mio", "mrd",
          "tsd", "bn", "mn", "bln", "tn", "zwiefach"]
_PREFIXES = ("doppel", "verdoppel", "double", "tripl", "halbier", "verdreifach", "vervierfach", "drittel", "viertel",
             "fünftel", "sechstel", "siebtel", "achtel", "neuntel", "zehntel")
_EXCEPTIONS = ("zweifel", "zweig", "achtung", "beachten", "weight", "overweight", "underweight", "gewicht")
_EXCEPTION_ENDINGS = ("", "e", "en", "er", "es", "em", "n", "s")
"""Häufige Wörter, die nur zufällig einen Zahlstamm tragen; gelten nur für die Teilwort-Regel und nur als ganzes Token mit festen
Endungen (nicht als Präfix: `zweigeteilt` bleibt ein Treffer). `achten` fehlt mit Absicht: Es ist die Ordinalform von „acht“ und
bleibt ein Treffer wie in `check_text` (bekannter Fehlalarm, Plan 14.1)."""


def _cardinals() -> set[str]:
    """Kardinalzahlen 0–100 (de, en; „twenty-one“ zerfällt im Token-Muster in twenty und one), Zehnerpotenzen, Ordinal-,
    Bruchwörter, Multiplikative, Dutzend und Abkürzungen."""
    words = {"null", "zero", "hundert", "einhundert", "hundred", "eins", *_DE_TEENS, *_EN_TEENS, *_DE_UNITS[1:], *_EN_UNITS[1:]}
    for tens in range(2, 10):
        words |= {_DE_TENS[tens], _EN_TENS[tens]}
        words |= {f"{_DE_UNITS[unit]}und{_DE_TENS[tens]}" for unit in range(1, 10)}
    words |= set(_POWERS) | set(_EN_ORD) | set(_OTHER)
    words |= {stem + end for stem in _DE_ORD for end in _ENDINGS}
    return {v for w in words for v in _variants(w)} - _ALLOWED


def _stems() -> set[str]:
    """Kardinal- und Ordinalstämme ab „zwei“ für die Teilwort-Regel; mindestens vier Zeichen, damit `ten`, `six`, `elf` nicht
    jedes zweite Wort treffen."""
    base = [*_DE_UNITS[2:], *_DE_TEENS[3:], "zwölf", *_DE_TENS.values(), *_EN_UNITS[2:], *_EN_TEENS[1:], *_EN_TENS.values(),
            *_POWERS, *_DE_ORD]
    return {v for w in base for v in _variants(w) if len(v) >= 4}


_CARDINALS = _cardinals()
_STEMS = _stems()
_PREFIX_STEMS = tuple(v for p in _PREFIXES for v in _variants(p))
_EXCEPTION_TOKENS = frozenset(_fold(e) + end for e in _EXCEPTIONS for end in _EXCEPTION_ENDINGS)


def _snippet(text: str, start: int, end: int) -> str:
    """Ausschnitt um den Treffer, höchstens `SNIPPET_MAX` Zeichen, Zeilenumbrüche und Tabs als Leerzeichen."""
    end = min(end, start + SNIPPET_MAX)
    a = max(start - (SNIPPET_MAX - (end - start)) // 2, 0)
    return text[a:a + SNIPPET_MAX].replace("\n", " ").replace("\t", " ")


def _digit_hits(entry: int, text: str) -> list[Hit]:
    return [Hit(entry, "ziffer", _snippet(text, i, i + 1)) for i, _ in _first_of_runs(text)]


def _first_of_runs(text: str) -> list[tuple[int, str]]:
    """Je zusammenhängender Folge numerischer Zeichen das erste Zeichen mit Index (eine Zahl = ein Treffer)."""
    runs: list[tuple[int, str]] = []
    prev = False
    for i, c in enumerate(text):
        numeric = c.isnumeric()
        if numeric and not prev:
            runs.append((i, c))
        prev = numeric
    return runs


def _word_hits(entry: int, text: str) -> list[Hit]:
    hits: list[Hit] = []
    for m in _TOKEN.finditer(text):
        token = _fold(m.group())
        if token in _ALLOWED:
            continue
        if token in _CARDINALS:
            hits.append(Hit(entry, "zahlwort", _snippet(text, m.start(), m.end())))
        elif token.startswith(_PREFIX_STEMS):
            hits.append(Hit(entry, "teilwort", _snippet(text, m.start(), m.end())))
        elif token in _EXCEPTION_TOKENS:
            continue
        elif any(len(token) > len(s) and (token.startswith(s) or token.endswith(s)) for s in _STEMS):
            hits.append(Hit(entry, "teilwort", _snippet(text, m.start(), m.end())))
    return hits


def _char_hits(entry: int, text: str) -> list[Hit]:
    hits: list[Hit] = []
    for i, c in enumerate(text):
        category = unicodedata.category(c)
        if category == "Cc" and c not in "\n\t":
            hits.append(Hit(entry, "steuerzeichen", _snippet(text, i, i + 1)))
        elif category == "Cf" or c in _INVISIBLE:
            hits.append(Hit(entry, "unsichtbar", _snippet(text, i, i + 1)))
    return hits


def scan_dump(dump: list[dict[str, Any]]) -> list[Hit]:
    """Alle Treffer der gezeigten Einträge eines Laufs (Reihenfolge: Eintrag, dann Art Ziffer/Wort/Zeichen)."""
    hits: list[Hit] = []
    for index, item in enumerate(dump):
        text = str(item.get("text", ""))
        model_text = item.get("model_text")
        if item.get("kind") == "commentary_withheld":
            pass  # nur die Zeichenprüfung unten: der Rohtext ist der abgelehnte, die Tabelle stammt aus dem Code
        elif isinstance(model_text, str):
            bare = _SLOT.sub(" ", model_text)
            hits += _digit_hits(index, _strip_invisible(bare)) + _word_hits(index, _normalize(bare))
        else:
            hits += _digit_hits(index, text)
        hits += _char_hits(index, text)
    return hits
