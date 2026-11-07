"""TA-17 — Diakritika vor dem Zahlwort-Abgleich (Plan 11, Restrisiko `zwéi`; Review TA-7, B6).

`scan_words` gleicht `masked` und eine zeichengenaue Kopie ohne Diakritika ab. Hier stehen die Fehlalarm-Prüfungen
(Wörter mit Diakritika, die kein Zahlwort sind) und die systematische Prüfung der Wortlisten; die Einzelfälle liegen im
Korpus (`tests/data/number_check_corpus.yaml`, ST-B100 ff.).
"""

import unicodedata

import pytest

from scout_research.tools import numbercheck as nc
from scout_research.tools.numbercheck import normalize, scan_numbers, scan_words, strip_diacritics

_WORD_KINDS = {"number_word", "ordinal", "magnitude_word", "fraction_word"}


def _kinds(text: str) -> set[str]:
    return {f.kind for f in scan_numbers(normalize(text).norm)} & _WORD_KINDS


# --- Kopie ohne Diakritika -----------------------------------------------------------------------------------------


def test_the_copy_keeps_every_position_and_leaves_non_decomposable_letters_alone() -> None:
    text = "zwéi ß æ ø ł ü Œ ﬁ ² ª Nestlé"
    folded = strip_diacritics(text)
    assert len(folded) == len(text)
    assert folded == "zwei ß æ ø ł u Œ ﬁ ² ª Nestle"


def test_the_umlaut_forms_stay_findable_through_masked_and_the_wordlists_know_the_plain_forms() -> None:
    for word in ("fünf", "zwölf", "dreißig", "fünfzig", "zwölfte", "fünfte", "Millionen"):
        assert _kinds(word), word
    # Die Listen kennen die Umlaut-Formen auch ohne Umlaut (`funf`, `funfzig`, `funft`, `zwoelf`); für „zwölf“ ohne
    # `ö` ist `zwoelf` die Schreibweise. Die Kopie erzeugt daher für „über“, „für“ nichts Neues.
    for plain in ("funf", "funfzig", "funft", "zwoelf", "dreissig"):
        assert _kinds(plain), plain
    for word in ("über", "für", "Ölpreis", "Größe", "Müller", "Zürich", "Köln", "Bär"):
        assert not _kinds(word), word


# --- Fehlalarm-Prüfung 2: Wörter mit Diakritika, die kein Zahlwort sind -----------------------------------------------

_HARMLESS = [
    # Firmen und Marken
    "Nestlé", "Société Générale", "Hermès", "L'Oréal", "Telefónica", "Crédit Agricole", "Danone Évian", "Zürich Insurance",
    "Citroën", "Škoda", "Ørsted", "Nokia Oyj Espoo", "Volkswagen Wolfsburg", "Müller Milch", "Bâloise", "Rémy Cointreau",
    "Peugeot Sociétè", "Société Anonyme", "Banco Santander Río", "Cañon Inc", "Hennes & Mauritz Stockholm Åland",
    "Électricité de France", "Médiobanca", "Sociedad Anónima", "Böhler-Uddeholm", "Fjällräven", "Düsseldorf Köln Nürnberg",
    "São Paulo", "Kraków", "Málaga", "Zaragoza Sevilla Córdoba", "Göteborg", "Malmö", "Århus", "Genève", "Besançon",
    # Fachwörter
    "Café", "Résumé", "naïve", "Exposé", "Coöperation", "Señor", "Größe", "über", "für", "Ölpreis", "Zürich", "Übersicht",
    "Geschäftsjahr Änderung", "Prämie", "Bilanzstruktur Überhang", "Währung", "Rückgang", "fällig", "größer", "Gewinnwarnung",
    "über dem Median", "für Peers", "façade", "cliché", "déjà vu", "fiancée", "entrepôt", "détente", "protégé", "pâté",
    "años", "niño", "mañana", "español", "più", "città", "università", "caffè", "perché", "così", "également", "où",
    "très", "être", "à la carte", "coup d'état", "tête-à-tête", "smörgåsbord", "Älvsborg", "Östersund", "Ærø", "Łódź",
    "İstanbul", "Çanakkale", "Zoë", "Chloé", "René", "André", "Núñez", "Müller", "Jürgen", "Björn", "Sørensen", "Åse",
    # Wörter nahe an Zahlwörtern, aber keine
    "Séance", "Élan", "Étage", "Énergie", "Eléonore", "Tenór", "Sévérine", "Tréma", "Aché",
    "doublé", "triplé", "Drittländer", "Drittverkäufer", "Drittstaaten", "fünftens", "Fünftens", "zweitens", "Drittanbieter",
]


@pytest.mark.parametrize("text", _HARMLESS)
def test_words_with_diacritics_that_are_not_number_words_stay_clean(text: str) -> None:
    assert len(_HARMLESS) >= 60
    assert _kinds(text) == set(), text
    assert _kinds(unicodedata.normalize("NFD", text)) == set(), text  # auch zerlegt (kombinierende Zeichen)


# --- Prüfung 3: systematisch über die Wortlisten ---------------------------------------------------------------------

_ACCENTS = {"a": "áàâä", "e": "éèêë", "i": "íìîï", "o": "óòôõ", "u": "úùûü"}
_COMBINING = ("\u0301", "\u0300", "\u0302", "\u0308", "\u0307", "\u0323", "\u0331", "\u030c")


def _forms() -> set[str]:
    forms = {w for w in nc._DE_NUM if w not in ("anderthalb", "eineinhalb", "einhalb")}
    forms |= {s + "e" for s in nc._ORDINAL_STEMS}
    forms |= set(nc._EN_NUM) | set(nc._EN_ORDINALS)
    forms |= {p + "en" for p in nc._MAGNITUDE_PREFIXES}
    forms |= {"zwölfte", "fünfte", "zwölfmal", "fünftel", "dreifünftel"}
    return forms


def _accented_variants(word: str) -> list[str]:
    """Je Position: vorkomponierter Akzent auf ASCII-Vokalen; kombinierendes Zeichen hinter jedem Buchstaben, also auch
    hinter Konsonanten und Umlauten (`zwö\u0301lf`)."""
    out = []
    for i, ch in enumerate(word):
        for a in _ACCENTS.get(ch, ""):
            out.append(word[:i] + a + word[i + 1:])
        out += [word[:i + 1] + c + word[i + 1:] for c in _COMBINING]
    return out


def test_every_wordlist_form_with_an_accent_on_a_vowel_is_found() -> None:
    missed = []
    checked = 0
    for word in sorted(_forms()):
        for variant in _accented_variants(word):
            checked += 1
            if not _kinds(variant):
                missed.append(variant)
    assert checked > 1000
    assert not missed, missed[:20]


@pytest.mark.parametrize("word", ["zwéi", "dréißig", "Fünfté", "Zwéi", "ZWÉI", "Trillión", "tén", "Eléven"])
def test_an_accented_number_word_inside_a_sentence_is_found_with_its_position(word: str) -> None:
    text = f"ADBE hat {word} Peers"
    found = scan_words(normalize(text).norm)
    start = text.index(word)
    assert any(f.start == start and f.end == start + len(word) for f in found), (word, found)


@pytest.mark.parametrize("word", ["Hälfte", "Hálfte", "Hälfte", "Ha\u0308lfte\u0301", "Hälfté"])
def test_the_multiplicative_phrase_survives_accents(word: str) -> None:
    assert {f.kind for f in scan_words(normalize(f"ADBE zahlt die {word} davon").norm)} == {"multiplicative"}


# --- exakte Positionen im Rohtext (Problem.start/end) ------------------------------------------------------------------

_SESSION = None


def _problem_spans(text: str) -> list[tuple[int, int, str]]:
    global _SESSION
    from scout_research.tools import commentary
    from tests.unit.number_check_corpus import build_session, load_corpus

    if _SESSION is None:
        corpus = load_corpus()
        _SESSION = build_session(corpus, "base")
    result = commentary.check_text(_SESSION, text, "slot_text")
    return [(p.start, p.end, p.match) for p in result.problems]


@pytest.mark.parametrize("text, match", [
    ("ac\u0300ht Peers", "ac\u0300ht"),                       # am Textanfang
    ("Peers ac\u0300ht", "ac\u0300ht"),                       # am Textende
    ("\u0300ac\u0300ht Peers", "ac\u0300ht"),                # Mn als allererstes Zeichen
    ("Es gibt ac\u0323\u0300ht Peers", "ac\u0323\u0300ht"),  # zwei Mn hintereinander
    ("Es gibt ac\u20ddht Peers", "ac\u20ddht"),               # Me (umschließender Kreis)
    ("Es gibt zw\u0903ei Peers", "zw\u0903ei"),               # Mc mitten im Wort
    ("Es gibt acht\u20dd", "acht\u20dd"),                     # Me nach dem letzten Buchstaben, Textende
    ("Es gibt acht\u20dd Peers", "acht\u20dd"),
    ("Es gibt ach\u0323\u0300t\u0323\u0300 Mio Euro", "ach\u0323\u0300t\u0323\u0300"),  # Mn-Folge am Wortende
    ("Es gibt zwe\u0301i\u0301\u0300 Peers", "zwe\u0301i\u0301\u0300"),
])
def test_exact_raw_positions_with_unattachable_marks(text: str, match: str) -> None:
    start = text.index(match)
    assert _problem_spans(text) == [(start, start + len(match), match)]


@pytest.mark.parametrize("word, kind", [
    ("zwéijährig", "number_word"), ("dréitägige", "number_word"),  # Suffixe `jahrig`, `tagig` der Kopie
    ("fúnfachtel", "fraction_word"),  # Bruch-Präfix `funf`
])
def test_umlaut_free_suffix_and_prefix_forms_are_needed_by_the_copy(word: str, kind: str) -> None:
    assert [(f.start, f.end, f.kind) for f in scan_words(normalize(word).norm)] == [(0, len(word), kind)]


@pytest.mark.parametrize("word", ["Drittverkäufer", "Drittländer", "Drittstaaten"])
def test_third_party_words_with_umlaut_are_no_ordinals(word: str) -> None:
    assert scan_words(normalize(word).norm) == []


@pytest.mark.parametrize("word", ["acht⃝", "achṭ̀", "ac̀ht⃝", "acḥ̀ṭ̀"])
def test_the_finding_in_the_masked_text_includes_marks_after_the_last_letter(word: str) -> None:
    """Auf Ebene von `scan_words` (nicht nur im Rohtext, wo der Zeichenhaufen ohnehin ganz zählt)."""
    norm = normalize(f"Es gibt {word} Peers").norm  # `norm` setzt vorkomponierbare Zeichen zusammen
    length = len(normalize(word).norm)
    assert [(f.start, f.end) for f in scan_words(norm)] == [(8, 8 + length)]
