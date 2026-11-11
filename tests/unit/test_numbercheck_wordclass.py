"""TA-20, Fable 02 Befund 2 und 3: Zahlwörter und Kasusformen von „ein“/„halb“ als KLASSE (Plan 11.2, Klassen 11–14).

Die Generatoren unten kennen die Zahlwortlisten des Produktcodes (`_DE_NUM`, `_EN_NUM`, `_ORDINAL_STEMS` …) nicht; sie
bauen die Formen aus eigenen Bausteinen. Jede erzeugte Form steht in einem Trägersatz und muss in beiden Profilen
(`slot_text`, `rationale`) eine `NAKED_NUMBER`-Fundstelle haben, die die Form überlappt. Einzelfälle: Korpus
(`tests/data/number_check_corpus.yaml`, ST-B114 ff., RA-…).
"""

from __future__ import annotations

import pytest

from scout_research.tools.commentary import check_text
from tests.unit.test_tools_commentary import _names_session

_PROFILES = ("slot_text", "rationale")


def _misses(forms: list[str], carrier: str) -> list[str]:
    """Formen, die in mindestens einem Profil keine überlappende `NAKED_NUMBER`-Fundstelle haben."""
    session = _names_session("Adobe Inc.")
    missed = []
    for form in forms:
        text = carrier.format(form)
        a = text.index(form)
        for profile in _PROFILES:
            result = check_text(session, text, profile)
            if not any(p.code == "NAKED_NUMBER" and p.start < a + len(form) and a < p.end for p in result.problems):
                missed.append(f"{form} [{profile}]")
    return missed


DE_CARRIER = "Das ist {} im Vergleich."
EN_CARRIER = "This is {} in the comparison."

# --- Generatoren (eigene Bausteine) --------------------------------------------------------------------------------

_DE_ONES = ["null", "eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun"]
_DE_TEENS = ["zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
_DE_TENS = {2: "zwanzig", 3: "dreißig", 4: "vierzig", 5: "fünfzig", 6: "sechzig", 7: "siebzig", 8: "achtzig", 9: "neunzig"}
_DE_COMBINING = {1: "ein", **{i: _DE_ONES[i] for i in range(2, 10)}}


def de_cardinal(n: int) -> str:
    if n < 10:
        return _DE_ONES[n]
    if n < 20:
        return _DE_TEENS[n - 10]
    if n == 100:
        return "hundert"
    tens, ones = divmod(n, 10)
    return _DE_TENS[tens] if ones == 0 else f"{_DE_COMBINING[ones]}und{_DE_TENS[tens]}"


def de_ordinal_stem(n: int) -> str:
    """Ordinalstamm 2–100 (Endung folgt): Sonderformen bis 19, sonst Kardinalzahl + t/st."""
    special = {2: "zweit", 3: "dritt", 4: "viert", 5: "fünft", 6: "sechst", 7: "siebt", 8: "acht", 9: "neunt", 10: "zehnt",
               11: "elft", 12: "zwölft"}
    if n in special:
        return special[n]
    return de_cardinal(n) + ("t" if n < 20 else "st")


_DE_ORD_ENDINGS = ["e", "en", "er", "es", "em"]
_EN_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_EN_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}


def en_cardinal(n: int, sep: str = "-") -> str:
    if n < 10:
        return _EN_ONES[n]
    if n < 20:
        return _EN_TEENS[n - 10]
    if n == 100:
        return "one hundred"
    tens, ones = divmod(n, 10)
    return _EN_TENS[tens] if ones == 0 else f"{_EN_TENS[tens]}{sep}{_EN_ONES[ones]}"


_EN_ORD_ONES = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth"}
_EN_ORD_TEENS = {10: "tenth", 11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth", 15: "fifteenth",
                 16: "sixteenth", 17: "seventeenth", 18: "eighteenth", 19: "nineteenth"}
_EN_ORD_TENS = {2: "twentieth", 3: "thirtieth", 4: "fortieth", 5: "fiftieth", 6: "sixtieth", 7: "seventieth",
                8: "eightieth", 9: "ninetieth"}


def en_ordinal(n: int) -> str:
    if n == 100:
        return "hundredth"
    if n < 10:
        return _EN_ORD_ONES[n]
    if n < 20:
        return _EN_ORD_TEENS[n]
    tens, ones = divmod(n, 10)
    return _EN_ORD_TENS[tens] if ones == 0 else f"{_EN_TENS[tens]}-{_EN_ORD_ONES[ones]}"


# --- Gruppen -------------------------------------------------------------------------------------------------------

DE_CARDINALS = [de_cardinal(n) for n in range(0, 101)] + ["einhundert"]
DE_ALTERNATIVES = ["zwo", "dreissig", "zwoelf", "zwolf", "zweiundzwanzig", "funf", "funfzig", "funfzehn"]
DE_CAPITALIZED = [w.capitalize() for w in DE_CARDINALS]
DE_MULTIPLIERS = [w + s for w in DE_CARDINALS[2:] for s in ("mal", "fach")] + [w.capitalize() + s for w in DE_CARDINALS[2:] for s in ("mal", "fach")]
DE_MAGNITUDES = ["zehn", "hundert", "tausend", "zehntausend", "hunderttausend", "Million", "Millionen", "Milliarde",
                 "Milliarden", "Billion", "Billionen"]
DE_ORDINALS = [de_ordinal_stem(n) + e for n in range(2, 101) for e in _DE_ORD_ENDINGS] + ["einhundertste"]
DE_ORDINALS_CAP = [w.capitalize() for w in DE_ORDINALS]

EN_CARDINALS = [en_cardinal(n) for n in range(0, 101) if n != 1] + [en_cardinal(n, " ") for n in range(21, 100) if n % 10]
EN_CAPITALIZED = [w.capitalize() for w in EN_CARDINALS]
EN_MAGNITUDES = ["ten", "hundred", "thousand", "ten thousand", "hundred thousand", "million", "billion", "trillion"]
EN_ORDINALS = [en_ordinal(n) for n in range(2, 101)]

GROUPS = {
    "de_cardinals": (DE_CARDINALS, DE_CARRIER),
    "de_alternatives": (DE_ALTERNATIVES, DE_CARRIER),
    "de_capitalized": (DE_CAPITALIZED, DE_CARRIER),
    "de_mal_fach": (DE_MULTIPLIERS, DE_CARRIER),
    "de_magnitudes": (DE_MAGNITUDES, DE_CARRIER),
    "de_ordinals": (DE_ORDINALS, DE_CARRIER),
    "de_ordinals_capitalized": (DE_ORDINALS_CAP, DE_CARRIER),
    "en_cardinals": (EN_CARDINALS, EN_CARRIER),
    "en_capitalized": (EN_CAPITALIZED, EN_CARRIER),
    "en_magnitudes": (EN_MAGNITUDES, EN_CARRIER),
    "en_ordinals": (EN_ORDINALS, EN_CARRIER),
}


def test_the_generators_produce_the_expected_forms() -> None:
    assert (de_cardinal(16), de_cardinal(17), de_cardinal(21), de_cardinal(66), de_cardinal(99)) == (
        "sechzehn", "siebzehn", "einundzwanzig", "sechsundsechzig", "neunundneunzig")
    assert (de_ordinal_stem(16), de_ordinal_stem(21), de_ordinal_stem(100)) == ("sechzehnt", "einundzwanzigst", "hundertst")
    assert (en_cardinal(99), en_cardinal(99, " "), en_ordinal(21), en_ordinal(100)) == (
        "ninety-nine", "ninety nine", "twenty-first", "hundredth")
    assert len(DE_CARDINALS) == 102 and len(DE_ORDINALS) == 99 * 5 + 1 and len(EN_ORDINALS) == 99


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_generated_number_word_is_a_naked_number_in_both_profiles(group: str) -> None:
    forms, carrier = GROUPS[group]
    missed = _misses(forms, carrier)
    assert not missed, f"{group}: {len(missed)} Formen ohne Fundstelle: {missed}"


@pytest.mark.parametrize("text", [
    "Das ist ein Peer.", "Das ist eine Firma.", "Wir nehmen einen Peer.", "Mit einem Peer.", "Auf einer Basis.",
    "Das ist eines der Ziele.", "Das ist one Peer.", "A single peer.", "Das ist einzig NVDA.", "Beide Peers sind groß.",
    "Both peers are large.", "Kein Peer fehlt.", "None of the peers.", "Der erste Peer.", "The first peer.",
    "Erstens ist das teuer.", "Zweitens ist das billig.", "Secondly, it is cheap.", "Second, it is cheap.",
    "Drittens das Risiko.", "Firstly, the model.",
])
def test_the_allowed_forms_of_one_and_the_enumeration_adverbs_stay_accepted(text: str) -> None:
    session = _names_session("Adobe Inc.")
    for profile in _PROFILES:
        assert check_text(session, text, profile).ok, (text, profile)


# Gewöhnliche Wörter mit Zahlstämmen, die der Backstop vor TA-20 nicht fand und auch jetzt nicht finden darf: die neuen
# Bausteine (`sechzehn`, `siebzehn`) und die Ordinalregel (Kardinalzahl als Ganzes + t/st + Endung) greifen nur auf
# ganze Wörter.
_HARMLESS_STEMS = [
    "Sieb", "Siebdruck", "Siebe", "Siebung", "Achtung", "Achtsamkeit", "beachten", "Elfenbein", "Elfmeter", "Neutrum",
    "Dreieck", "Dreirad", "Vierkant", "Fünfkampf", "Zehnkampf", "Sechseck", "Siebenbürgen", "Neunauge", "Zwilling",
    "Zwiebel", "Zwist", "Zwischen", "Dreck", "dreist", "Dreistigkeit", "Hundertschaft", "Tausendsassa", "einst", "einstig",
    "Einzelfall", "einzeln", "Einheit", "Einkommen", "Einsatz", "Einstieg", "erst", "Erstens", "Gesundheit", "Kundenstamm",
    "Zweig", "Zweifel", "Zweck", "Zahlungsziel", "Hundertwasser", "Tausendfüßler", "Tenant", "Tenor", "Tensor", "thirsty",
    "Attention", "Weight", "Sixt", "Zehnerpotenz", "Jahrzehnt", "Jahrzehnte", "Jahrzehnten",
]


def test_ordinary_words_with_number_stems_are_still_not_found() -> None:
    session = _names_session("Adobe Inc.")
    for word in _HARMLESS_STEMS:
        for profile in _PROFILES:
            result = check_text(session, f"Das ist {word} im Vergleich.", profile)
            assert result.ok, (word, profile, [(p.code, p.kind) for p in result.problems])


# --- Befund 3: Kasusformen von „ein“ und „halb“ vor Einheiten ---------------------------------------------------------

_DE_ARTICLES = ["ein", "eine", "einem", "einen", "einer", "eines"]
_DE_ARTICLES += [a.capitalize() for a in _DE_ARTICLES]
# Zwischenwörter nach Policy (TA-20.1, M4), nicht aus dem Code abgeleitet; vor Prozent/Basispunkt/Promille steht jedes Wort
_DE_ADJECTIVES = ["", "weiteren ", "zusätzlichen ", "einzigen ", "knappen ", "guten ", "vollen ", "ganzen ", "halben "]
_DE_UNITS = ["Prozent", "Prozents", "Prozentpunkt", "Prozentpunkte", "Prozentpunkten", "Prozentpunkts", "Prozentpunktes",
             "Basispunkt", "Basispunkte", "Basispunkten", "Basispunkts", "Promille", "Mal", "Dollar", "US-Dollar", "Euro",
             "Cent", "Promillepunkt", "Basis-Punkt", "%-Punkt"]
# „weiteren“, „zusätzlichen“, „einzigen“ vor „Mal“ bleiben frei („noch ein weiteres Mal“ ist eine Redewendung)
DE_ARTICLE_FORMS = [f"{a} {adj}{u}" for a in _DE_ARTICLES for adj in _DE_ADJECTIVES for u in _DE_UNITS
                    if not (u == "Mal" and adj in ("weiteren ", "zusätzlichen ", "einzigen "))]
DE_HALF_FORMS = [f"{h} {u}" for h in ("halb", "halbe", "halbes", "halben", "halber", "halbem", "Halbes", "Halben")
                 for u in _DE_UNITS]

_EN_UNITS = ["percent", "per cent", "percentage point", "percentage points", "basis point", "basis points", "dollar",
             "dollars", "cent", "cents", "euro", "euros"]
_EN_ADJECTIVES = ["", "additional ", "extra ", "further ", "whole ", "single ", "more ", "full ", "half "]
_EN_CURRENCIES = ("dollar", "dollars", "cent", "cents", "euro", "euros")
# Englische Währungen zählen nur nach „one“ (O-Entscheidung Stufe B): „a dollar“ bleibt frei (Restrisiko, siehe unten)
EN_ARTICLE_FORMS = [f"{a} {adj}{u}" for a in ("one", "a", "an", "One", "A", "An") for adj in _EN_ADJECTIVES
                    for u in _EN_UNITS if not (u in _EN_CURRENCIES and a.lower() != "one")]
EN_ARTICLE_FORMS += [f"{a} tiny {u}" for a in ("a", "an") for u in _EN_UNITS if u not in _EN_CURRENCIES]
EN_ARTICLE_FORMS += [f"{a} {u}" for a in ("one", "One") for u in ("bp", "bps")]
EN_HYPHEN_FORMS = ["one-percent", "One-percent", "one-percentage-point", "one-basis-point", "a one-percent",
                   "a one-percentage-point", "one-per-cent", "one-half-percent", "a half-percent", "a single-percent"]
EN_HALF_FORMS = ["half a percent", "half a percentage point", "half a basis point", "Half a percent", "half a dollar",
                 "half a cent", "half an euro"]

GROUPS_C = {
    "de_article_forms": (DE_ARTICLE_FORMS, DE_CARRIER),
    "de_half_forms": (DE_HALF_FORMS, DE_CARRIER),
    "en_article_forms": (EN_ARTICLE_FORMS, EN_CARRIER),
    "en_hyphen_forms": (EN_HYPHEN_FORMS, EN_CARRIER),
    "en_half_forms": (EN_HALF_FORMS, EN_CARRIER),
}


@pytest.mark.parametrize("group", sorted(GROUPS_C))
def test_every_case_form_of_one_and_half_before_a_unit_is_a_naked_number_in_both_profiles(group: str) -> None:
    forms, carrier = GROUPS_C[group]
    missed = _misses(forms, carrier)
    assert not missed, f"{group}: {len(missed)} Formen ohne Fundstelle: {missed[:40]}"


@pytest.mark.parametrize("text", [
    "Das gilt einmal im Vergleich.", "Das gilt noch einmal im Vergleich.", "Das ist einfach im Vergleich.",
    "Das ist ein Prozentsatz im Vergleich.", "Das ist eine Prozentangabe im Vergleich.",
    "Das ist einen Dollar-Kurs im Vergleich.", "Das ist ein Euro-Staat im Vergleich.", "Das ist ein US-Dollar-Kurs.",
    "Das ist ein Malus im Vergleich.", "Das ist eine Centrale im Vergleich.",
    "This is a one-time charge in the comparison.", "This is a percentage of revenue.", "This is a large share.",
    "This is a dollar-based plan.", "This is a euro-area bank.", "This is a percentage rate.",
])
def test_ordinary_phrases_with_one_or_half_stems_stay_accepted(text: str) -> None:
    session = _names_session("Adobe Inc.")
    for profile in _PROFILES:
        result = check_text(session, text, profile)
        assert result.ok, (text, profile, [(p.code, p.kind) for p in result.problems])


def test_the_idiom_with_one_stroke_is_a_known_false_alarm_and_documented_as_found() -> None:
    """„mit einem Mal“ (Redewendung) lässt sich von „einem Mal“ als Zahl nicht trennen: bewusst gefunden (Plan 11.2:
    im Zweifel blockieren, Kosten: umformulieren)."""
    assert _misses(["einem Mal"], "Mit {} stieg der Wert.") == []


# --- TA-20.1, zweite Runde: Beugung der Einheit, Zahl + Einheit in einem Wort, Bruchwörter ab 13, -fold ----------------

DE_GENITIVE_FORMS = [f"{a} {u}" for a in ("ein", "eine", "einem", "einen", "einer", "eines", "Eines", "ein halbes", "eines halben")
                     for u in ("Prozentes", "Prozente", "Prozenten", "Prozents", "Promill", "Promille", "Promilles", "Promillen")]
_DE_NUMBER_PREFIXES = ["ein", "halb", "anderthalb"] + DE_CARDINALS[2:]
_DE_UNIT_TAILS = ["prozentig", "prozentige", "prozentigen", "prozentiger", "prozentiges", "prozentmarke", "prozenthürde",
                  "promille", "promillegrenze", "basispunkt", "basispunkte"]
DE_UNIT_COMPOUNDS = [p + t for p in _DE_NUMBER_PREFIXES for t in _DE_UNIT_TAILS]
DE_UNIT_COMPOUNDS += [(p + t).capitalize() for p in _DE_NUMBER_PREFIXES for t in _DE_UNIT_TAILS]
DE_FRACTIONS = [de_ordinal_stem(n) + "el" for n in range(3, 101)]
DE_FRACTIONS += [f + "n" for f in DE_FRACTIONS] + [f.capitalize() for f in DE_FRACTIONS] + ["Einundzwanzigstel", "einhundertstel"]
EN_FOLD = [en_cardinal(n) + "fold" for n in range(2, 101)] + ["two-fold", "three-fold", "ten-fold", "Twofold", "Threefold"]
EN_FOLD += [w + "fold" for w in ("hundred", "thousand", "million", "billion")]

GROUPS_D = {
    "de_genitive_forms": (DE_GENITIVE_FORMS, DE_CARRIER),
    "de_unit_compounds": (DE_UNIT_COMPOUNDS, DE_CARRIER),
    "de_fractions": (DE_FRACTIONS, DE_CARRIER),
    "en_fold": (EN_FOLD, EN_CARRIER),
}


@pytest.mark.parametrize("group", sorted(GROUPS_D))
def test_round_two_forms_are_naked_numbers_in_both_profiles(group: str) -> None:
    forms, carrier = GROUPS_D[group]
    missed = _misses(forms, carrier)
    assert not missed, f"{group}: {len(missed)} Formen ohne Fundstelle: {missed[:40]}"


_HARMLESS_ROUND_TWO = [
    "Mittel", "Kittel", "Titel", "Stiftel", "manifold", "scaffold", "blindfold", "unfold", "household", "Dreiecksteil",
    "Prozentsatz", "Prozentangabe", "Prozentrechnung", "Promillegrenze", "Basispunktesystem", "Prozentual", "Elfenbeinküste",
    "Zwiebelring", "Bestandteil", "Hotel", "Mantel", "Nickel", "Zettel", "Artikel", "Gürtel",
]


def test_ordinary_words_near_fractions_fold_and_percent_compounds_are_still_not_found() -> None:
    session = _names_session("Adobe Inc.")
    for word in _HARMLESS_ROUND_TWO:
        for profile in _PROFILES:
            result = check_text(session, f"Das ist {word} im Vergleich.", profile)
            assert result.ok, (word, profile, [(p.code, p.kind) for p in result.problems])


# --- TA-20.1, dritte Runde ----------------------------------------------------------------------------------------------

# H1: genau ein beliebiges Wort zwischen Artikel und Prozent/Basispunkt/Promille; Trenner auch Bindestrich
DE_ANY_WORD_FORMS = [f"{a} {w} {u}" for a in ("ein", "eine", "einem", "einen", "einer", "eines", "Eine")
                     for w in ("weiteren", "zusätzlicher", "winzigen", "ganz", "kleines") for u in
                     ("Prozent", "Prozentpunkt", "Prozentpunkte", "Basispunkt", "Promille", "Promill", "Prozentes")]
DE_HYPHEN_ARTICLE_FORMS = ["Ein-Prozent-Hürde", "eine Ein-Dollar-Aktie", "die Ein-Euro-Münze", "Ein-Cent-Münze",
                           "ein Ein-Basispunkt-Schritt", "die Ein-Promille-Grenze", "ein-Prozent-Schritt"]
EN_ANY_WORD_FORMS = ["an additional percentage point", "a further percentage point", "one more percent",
                     "an extra basis point", "a tiny percent", "an additional per cent", "One further percentage point",
                     "a one-percent", "one more dollar", "one single dollar", "one additional euro", "one extra cent"]
EN_TIMES_QUARTER_FORMS = ["one times", "a times", "an times", "One times", "a quarter-point", "a quarter percentage point",
                          "a quarter-percentage-point", "a quarter percent", "a quarter of a percent", "quarter point",
                          "a quarter basis point", "Quarter-point"]

# M2: Rangwörter mit Superlativ und Aufzählungsadverbien ab 13
DE_SUPERLATIVES = [de_ordinal_stem(n) + t for n in range(13, 101) for t in ("größte", "höchste", "beste", "niedrigste")]
DE_SUPERLATIVES += ["dreizehnt-größte", "dreizehntgrößte", "dreißigstgrößte", "einundzwanzigstbeste", "Dreizehntgrößte"]
DE_ENUM_ADVERBS_13_PLUS = [de_ordinal_stem(n) + "ens" for n in (13, 14, 15, 16, 17, 18, 19, 21, 22, 30, 99, 100)]
DE_ENUM_ADVERBS_11_12 = ["elftens", "zwölftens"]

# N2: Genitiv-s der Bruchwörter, weitere Einheitenformen
DE_FRACTION_GENITIVES = [f + "s" for f in DE_FRACTIONS if not f.endswith("n")]
DE_MORE_UNIT_FORMS = ["eines Sechzehntels", "eines Fünfzigstels", "eines Einundzwanzigstels", "einen Promillepunkt",
                      "einen %-Punkt", "einen Basis-Punkt", "einen weiteren Basis-Punkt"]
EN_BP_FORMS = ["one bp", "one bps", "One bps"]
DE_PERCENT_COMPOUND_FALSE_ALARMS = ["eine hundertprozentige Tochtergesellschaft", "hundertprozentig sicher"]


def _umlaut_variants(form: str) -> list[str]:
    """Alle Schreibweisen mit ü/u/ue, ö/o/oe, ß/ss (je Zeichenart einheitlich)."""
    variants = [form]
    for char, alternatives in (("ü", ("u", "ue")), ("ö", ("o", "oe")), ("ß", ("ss",))):
        variants += [v.replace(char, alt) for v in variants if char in v for alt in alternatives]
    return list(dict.fromkeys(variants))


def _spellings(forms: list[str]) -> list[str]:
    return list(dict.fromkeys(v for f in forms for v in _umlaut_variants(f)))


GROUPS_E = {
    "de_any_word_between": (DE_ANY_WORD_FORMS, DE_CARRIER),
    "de_hyphen_article": (DE_HYPHEN_ARTICLE_FORMS, DE_CARRIER),
    "en_any_word_between": (EN_ANY_WORD_FORMS, EN_CARRIER),
    "en_times_quarter": (EN_TIMES_QUARTER_FORMS, EN_CARRIER),
    "de_superlatives": (DE_SUPERLATIVES, DE_CARRIER),
    "de_superlatives_spellings": (_spellings(DE_SUPERLATIVES), DE_CARRIER),
    "de_enum_adverbs": (DE_ENUM_ADVERBS_13_PLUS + DE_ENUM_ADVERBS_11_12, DE_CARRIER),
    "de_fraction_genitives": (DE_FRACTION_GENITIVES, DE_CARRIER),
    "de_more_unit_forms": (DE_MORE_UNIT_FORMS, DE_CARRIER),
    "en_bp": (EN_BP_FORMS, EN_CARRIER),
    "de_percent_compound_false_alarms": (DE_PERCENT_COMPOUND_FALSE_ALARMS, "{}."),
    # M3: Umschrift ue/oe, Fehlen der Umlaute, ss für ß — jede deutsche Form in allen Schreibweisen
    "spellings_cardinals": (_spellings(DE_CARDINALS + DE_CAPITALIZED), DE_CARRIER),
    "spellings_mal_fach": (_spellings(DE_MULTIPLIERS), DE_CARRIER),
    "spellings_ordinals": (_spellings(DE_ORDINALS + DE_ORDINALS_CAP), DE_CARRIER),
    "spellings_fractions": (_spellings(DE_FRACTIONS + DE_FRACTION_GENITIVES), DE_CARRIER),
    "spellings_unit_compounds": (_spellings(DE_UNIT_COMPOUNDS), DE_CARRIER),
    "spellings_named": (["fuenf", "fuenfzehn", "fuenfzig", "fuenfte", "Fuenftel", "zwoelfte", "fuenfprozentig",
                         "Fuenf", "zwoelf", "dreissigste"], DE_CARRIER),
}


@pytest.mark.parametrize("group", sorted(GROUPS_E))
def test_round_three_forms_are_naked_numbers_in_both_profiles(group: str) -> None:
    forms, carrier = GROUPS_E[group]
    missed = _misses(forms, carrier)
    assert not missed, f"{group}: {len(missed)} von {len(forms)} Formen ohne Fundstelle: {missed[:40]}"


def test_enumeration_adverbs_up_to_ten_stay_accepted_and_from_eleven_are_ranks() -> None:
    session = _names_session("Adobe Inc.")
    for word in ("erstens", "zweitens", "drittens", "viertens", "fünftens", "sechstens", "siebtens", "achtens", "neuntens",
                 "zehntens"):
        assert check_text(session, f"Das gilt {word} im Vergleich.", "slot_text").ok, word


_HARMLESS_ROUND_THREE = [
    "Steuer", "Steuern", "Bauer", "neue", "Feuer", "Mauer", "Boeing", "Poesie", "Israel", "Michael", "Aerospace", "Duell",
    "Queue", "Fuel", "Fuels", "Fueling", "Jahrzehnt", "Jahrzehnte",
]


def test_words_with_ue_oe_ae_are_not_taken_for_number_words() -> None:
    session = _names_session("Adobe Inc.")
    for word in _HARMLESS_ROUND_THREE:
        for profile in _PROFILES:
            result = check_text(session, f"Das ist {word} im Vergleich.", profile)
            assert result.ok, (word, profile, [(p.code, p.kind) for p in result.problems])


@pytest.mark.parametrize("text", [
    "This is on a dollar basis.", "This is a U.S. dollar functional currency.", "This is a euro area country.",
    "This is a Financial Times article.", "This is a one-time charge.", "Das ist ein starker Dollar.",
    "Das ist ein starker Euro im Vergleich.", "Das ist ein Dollar-Kurs im Vergleich.", "Das ist ein weiteres Mal im Vergleich.",
    "Das ist ein Prozentsatz im Vergleich.", "Das ist ein weiterer Prozentsatz im Vergleich.",
])
def test_round_three_ordinary_phrases_stay_accepted(text: str) -> None:
    session = _names_session("Adobe Inc.")
    for profile in _PROFILES:
        assert check_text(session, text, profile).ok, (text, profile)


def test_a_dollar_after_a_or_an_is_found_before_punctuation_text_end_or_a_quantity_word() -> None:
    """N-1 (TA-20.1, Runde 4): „a“/„an“ + (Wort der geschlossenen Liste) + dollar/cent/euro ist eine Fundstelle, wenn
    Satzzeichen, Textende oder per/a/an/each/more/less/higher/lower/above/below/apiece folgt."""
    forms = [f"{art} {adj}{cur}" for art in ("a", "an", "A") for adj in ("", "extra ", "additional ", "further ", "whole ",
                                                                         "single ", "more ", "full ")
             for cur in ("dollar", "dollars", "cent", "cents", "euro", "euros")]
    carriers = ["The stock gained {}.", "The stock gained {}, then fell.", "It lost {}; then rose.", "It gained ({}).",
                "It gained {}!", "Did it gain {}?", "It gained {}", "It gained {} per share.", "It gained {} a share.",
                "It gained {} each.", "It gained {} more.", "It gained {} less.", "It gained {} higher.", "It gained {} lower.",
                "It gained {} above par.", "It gained {} below par.", "It gained {} apiece.", "It gained {} an hour."]
    for carrier in carriers:
        missed = _misses(forms, carrier)
        assert not missed, f"{carrier}: {len(missed)} von {len(forms)}: {missed[:20]}"
    for text in ("The stock gained a dollar.", "a dollar per share", "an extra dollar a share", "It lost a cent,"):
        for profile in _PROFILES:
            assert not check_text(_names_session("Adobe Inc."), text, profile).ok, (text, profile)


def test_a_dollar_inside_a_noun_phrase_stays_free_and_the_rest_is_a_documented_residual_risk() -> None:
    session = _names_session("Adobe Inc.")
    for text in ("This is on a dollar basis.", "This is a U.S. dollar functional currency.", "This is a euro area country.",
                 "This is a dollar-denominated bond.", "It is a cent-based measure.", "He paid a dollar for it."):
        # die letzte Stellung (Währung, gefolgt von einem Wort außerhalb der Liste) ist das verbleibende Restrisiko
        for profile in _PROFILES:
            assert check_text(session, text, profile).ok, (text, profile)


def test_hundred_percent_compounds_are_known_false_alarms_documented_as_found() -> None:
    """„hundertprozentig“ ist die Zahl 100 % (N1); die Redewendung („hundertprozentig sicher“) ist ein bekannter Fehlalarm
    wie „mit einem Mal“."""
    assert _misses(DE_PERCENT_COMPOUND_FALSE_ALARMS, "{}.") == []


# M1: ein entferntes unsichtbares Zeichen (Cc, Cf, `_INVISIBLE_MARKS`) ist eine Wortgrenze; zerrissene Zahlwörter bleiben
_INVISIBLES = ["\r", "\x00", "\x1b", "\x7f", "\x85", "\b", "​", "⁠", "﻿", "­", "️", "͏"]


@pytest.mark.parametrize("ch", _INVISIBLES, ids=[f"U+{ord(c):04X}" for c in _INVISIBLES])
def test_an_invisible_character_between_a_number_word_and_its_neighbour_does_not_hide_it(ch: str) -> None:
    session = _names_session("Adobe Inc.")
    for text, found in ((f"Es gibt zwei{ch}Peers.", "zwei"), (f"Es gibt Peers{ch}zwei.", "zwei"),
                        (f"There are two{ch}peers.", "two"), (f"Es gibt zw{ch}ei Peers.", "zw"),
                        (f"Es gibt sechzehn{ch}Peers.", "sechzehn"), (f"Das ist die dreizehnte{ch}Stufe.", "dreizehnte")):
        for profile in _PROFILES:
            result = check_text(session, text, profile)
            a = text.index(found)
            assert any(p.code == "NAKED_NUMBER" and p.start <= a < p.end for p in result.problems), (text, profile)


def test_the_boundary_variant_does_not_change_the_rendered_body() -> None:
    from tests.unit.test_tools_commentary import _table
    from scout_research.tools.commentary import build_check_session

    ctx, _, _ = _table()
    result = check_text(build_check_session(ctx), "NVDA liegt bei [[co:NVDA:ev_ebitda]].\rWeiter\x00so.", "slot_text")
    assert result.ok and result.body is not None and result.body.endswith(".Weiterso.")


def test_a_soft_hyphen_inside_an_ordinary_word_is_a_known_false_alarm() -> None:
    """Preis der Wortgrenze bei jedem unsichtbaren Zeichen (M1): „Zwei\\u00adfel“ mit weichem Trennstrich enthält ein
    „zwei“ am Wortanfang; bewusst blockiert (ein Durchlass wäre teurer als eine Korrekturrunde)."""
    assert _misses(["Zwei­fel"], DE_CARRIER) == []


@pytest.mark.parametrize("word", ["Sechzehntel", "Sechzehntels", "Siebzehnteln", "Einundzwanzigstel", "Fünfzigstels",
                                  "Dreißigstel", "Achtels", "Drittels"])
def test_fractions_from_thirteen_and_their_genitive_are_fraction_words_not_ranks(word: str) -> None:
    """Die Ordinal-Präfixregel (M2) würde „Sechzehntel“ ebenfalls finden, aber als `ordinal`; der Bruch hat Vorrang."""
    from scout_research.tools.numbercheck import normalize, scan_numbers

    assert [f.kind for f in scan_numbers(normalize(word).norm)] == ["fraction_word"]


# --- TA-20.1, vierte Runde ---------------------------------------------------------------------------------------------

DE_TWO_WORD_FORMS = [f"{a} {w1} {w2} {u}" for a in ("ein", "einen", "einem", "Eine") for w1, w2 in
                     (("ganzen", "weiteren"), ("ganzes", "zusätzliches"), ("kleinen", "feinen"), ("vollen", "weiteren"))
                     for u in ("Prozent", "Prozentpunkt", "Basispunkt", "Promille", "Prozentpunkte")]
DE_TWO_WORD_FORMS += ["um einen ganzen weiteren Prozentpunkt", "um einen zusatzlichen Dollar", "ein zusatzlicher Basispunkt",
                      "einen zusatzlichen Prozentpunkt", "um einen zusätzlichen Dollar"]
EN_TWO_WORD_FORMS = [f"{a} {w1} {w2} {u}" for a in ("a", "an", "one", "One") for w1, w2 in
                     (("additional", "full"), ("further", "whole"), ("tiny", "extra"))
                     for u in ("percentage point", "percent", "per cent", "basis point", "percentage points")]
EN_TWO_WORD_FORMS += ["an additional full percentage point"]


@pytest.mark.parametrize(("forms", "carrier"), [(DE_TWO_WORD_FORMS, DE_CARRIER), (EN_TWO_WORD_FORMS, EN_CARRIER)],
                         ids=["de_two_words", "en_two_words"])
def test_up_to_two_words_between_article_and_percent_unit_are_a_naked_number(forms: list[str], carrier: str) -> None:
    missed = _misses(forms, carrier)
    assert not missed, f"{len(missed)} von {len(forms)} Formen ohne Fundstelle: {missed[:30]}"


@pytest.mark.parametrize("text", [
    "Das ist ein weiterer Prozentsatz im Vergleich.", "Das ist eine Kennzahl in Prozent im Vergleich.",
    "Das ist eine Angabe in Prozentpunkten im Vergleich.", "Das ist ein Wert von Prozentpunkten im Vergleich.",
    "Das ist eine Marge für Prozent im Vergleich.", "This is a measure in percent.", "This is a change of percentage points.",
    "This is a rate in percent terms.", "This is a one-time charge.",
    "This is a turn of leverage.", "This is one turn of leverage.",
])
def test_round_four_ordinary_phrases_stay_accepted(text: str) -> None:
    """Auch „a turn of leverage“ / „one turn“ bleiben bewusst frei (Fehlalarme in normaler Finanzsprache; Restrisiko)."""
    session = _names_session("Adobe Inc.")
    for profile in _PROFILES:
        assert check_text(session, text, profile).ok, (text, profile)


def test_the_case_rule_for_times_and_bps_keeps_names_free() -> None:
    session = _names_session("Adobe Inc.")
    for text in ("This is One Times Square.", "This is a BP refinery.", "This is a Financial Times article.",
                 "This is an Times Union note."):
        for profile in _PROFILES:
            assert check_text(session, text, profile).ok, (text, profile)


# N-2: unsichtbare Buchstaben (Lo) und nicht zugewiesene Default-Ignorable-Zeichen (Cn)
# Feste Liste der Default_Ignorable_Code_Points, die weder Cf noch Cc noch in `_INVISIBLE_MARKS` sind (Python liefert die
# Eigenschaft nicht): Lo = U+115F, U+1160, U+3164, U+FFA0; Cn = U+2065, U+FFF0–U+FFF8, U+E0000–U+E0FFF (nicht zugewiesene).
_LO_FILLERS = [0x115F, 0x1160, 0x3164, 0xFFA0]
_CN_IGNORABLE = [0x2065, *range(0xFFF0, 0xFFF9), 0xE0000, 0xE0002, 0xE001F, 0xE0080, 0xE00FF, 0xE0FFF]
_DEFAULT_IGNORABLE_LISTED = [*_LO_FILLERS, 0x17B4, 0x17B5, *range(0x180B, 0x1810), *range(0xFE00, 0xFE10),
                             *range(0xE0100, 0xE01F0), 0x034F, *_CN_IGNORABLE]


@pytest.mark.parametrize("cp", _DEFAULT_IGNORABLE_LISTED, ids=[f"U+{c:04X}" for c in _DEFAULT_IGNORABLE_LISTED])
def test_every_listed_default_ignorable_code_point_is_removed_counted_and_a_word_boundary(cp: int) -> None:
    import unicodedata

    from scout_research.tools.numbercheck import normalize

    ch = chr(cp)
    if unicodedata.category(ch) == "Cc" or ch in "\n\t":  # (nicht der Fall; Schutz gegen eine geänderte Liste)
        pytest.skip("Steuerzeichen, Teil von O-33")
    n = normalize(f"zw{ch}ei Peers")
    assert n.clean == "zwei Peers" and n.format_chars == 1 and 2 in n.breaks, (n.clean, n.format_chars, n.breaks)
    session = _names_session("Adobe Inc.")
    for text, found in ((f"Es gibt zwei{ch}Peers.", "zwei"), (f"Es gibt zw{ch}ei Peers.", "zw")):
        for profile in _PROFILES:
            result = check_text(session, text, profile)
            a = text.index(found)
            assert any(p.code == "NAKED_NUMBER" and p.start <= a < p.end for p in result.problems), (text, profile)


def test_a_filler_letter_never_reaches_the_body() -> None:
    from tests.unit.test_tools_commentary import _table
    from scout_research.tools.commentary import build_check_session

    ctx, _, _ = _table()
    fillers = "".join(chr(c) for c in (*_LO_FILLERS, 0x2065, 0xFFF0, 0xE0FFF))
    result = check_text(build_check_session(ctx), f"NVDA liegt bei [[co:NVDA:ev_ebitda]].{fillers} Weiter.", "slot_text")
    assert result.ok and result.body is not None and not any(ch in result.body for ch in fillers)
    assert result.format_chars == len(fillers)


# N-5: die Wortgrenzen kommen auf dem Produktionsweg an (`check_text` gibt `BreakText` an `scan_numbers`)
def test_check_text_hands_the_word_boundaries_to_the_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    from scout_research.tools import commentary

    seen = []
    real = commentary.scan_numbers
    monkeypatch.setattr(commentary, "scan_numbers", lambda masked, invalid=(): seen.append(getattr(masked, "breaks", None)) or real(masked, invalid))
    check_text(_names_session("Adobe Inc."), "Es gibt zwei\rPeers.", "slot_text")
    assert seen == [frozenset({12})], seen


# N-6: Restrisiko, ohne unsichtbares Zeichen geht ein angeklebtes Zahlwort durch
def test_a_number_word_glued_to_a_word_without_any_hidden_character_is_a_known_residual_risk() -> None:
    session = _names_session("Adobe Inc.")
    for text in ("Es gibt zweiPeers.", "There are twoPeers."):
        for profile in _PROFILES:
            assert check_text(session, text, profile).ok, (text, profile)


@pytest.mark.parametrize("text", [
    "Das ist eine Frage der Prozent.", "Das ist ein Vergleich zwischen Prozentpunkten.", "Das ist ein Unternehmen aus Prozent.",
    "Das ist eine Marge für Prozent.", "This is a company without percent.", "This is a measure in percent.",
])
def test_the_two_word_window_skips_prepositions_and_articles(text: str) -> None:
    """Neue Fehlalarme des Zweiwortfensters (N-4) wären „eine Frage der Prozent“ u. Ä.; Präpositionen und Artikel zählen dort nicht."""
    for profile in _PROFILES:
        assert check_text(_names_session("Adobe Inc."), text, profile).ok, (text, profile)
