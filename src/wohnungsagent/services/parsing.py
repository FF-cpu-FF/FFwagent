"""Parser für deutschsprachige Inseratstexte.

Der wichtigste Teil ist `einzugsdatum()`. Das Kriterium "Einzug nicht vor dem
01.01.2027" lässt sich nur so gut umsetzen, wie sich das Verfügbarkeitsdatum
aus Freitext herausziehen lässt – und Portale schreiben es auf mindestens ein
Dutzend Arten. Alle unterstützten Formen sind in tests/test_parsing.py belegt.

Bewusst ohne dateutil o. ä.: die Formate sind eng genug, dass reguläre
Ausdrücke präziser sind als ein generischer Parser, der "3 Zimmer" als Datum
liest.
"""
from __future__ import annotations

import re
from datetime import date

from ..models.domain import Ausstattung, Einzugsstatus, Vermietertyp

# --------------------------------------------------------------------- Zahlen

# Tausenderpunkt-Form zuerst, aber nur mit mindestens einer Dreiergruppe –
# sonst würde "1250" als "125" gelesen.
_ZAHL = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?"


def euro(text: str | None) -> float | None:
    """'1.250,50 €' -> 1250.5 · 'auf Anfrage' -> None"""
    if not text:
        return None
    text = text.replace("\xa0", " ")
    if re.search(r"auf anfrage|nach vereinbarung|\bk\.\s?a\.", text, re.I) and not re.search(r"\d", text):
        return None
    treffer = re.search(_ZAHL, text)
    if not treffer:
        return None
    try:
        wert = float(f"{treffer.group(1).replace('.', '')}.{treffer.group(2) or '0'}")
    except ValueError:
        return None
    return wert if 0 < wert < 100_000 else None


def dezimal(text: str | None) -> float | None:
    """'2,5 Zi.' -> 2.5 · '67,80 m²' -> 67.8"""
    if not text:
        return None
    treffer = re.search(r"(\d+(?:[.,]\d+)?)", text.replace("\xa0", " "))
    if not treffer:
        return None
    try:
        return float(treffer.group(1).replace(",", "."))
    except ValueError:
        return None


def plz(text: str | None) -> str | None:
    if not text:
        return None
    treffer = re.search(r"\b(6[05]\d{3})\b", text)
    return treffer.group(1) if treffer else None


# ------------------------------------------------------------------- Einzug

MONATE = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mrz": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
    "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}

QUARTAL_START = {1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)}

# Signalwörter, die einen sofortigen Bezug bedeuten
_SOFORT = re.compile(
    r"\b(ab\s+sofort|sofort\s*(frei|beziehbar|bezugsfrei|verf[üu]gbar)|sofortbezug|"
    r"kurzfristig\s*(frei|verf[üu]gbar|beziehbar)|umgehend\s*beziehbar)\b",
    re.I,
)
_NACH_VEREINBARUNG = re.compile(r"nach\s+(vereinbarung|absprache)", re.I)

# Kontextanker – nur in deren Umfeld wird nach einem Datum gesucht, damit
# nicht das Baujahr oder ein Sanierungsdatum als Einzugstermin gilt.
_ANKER = re.compile(
    r"(frei\s*ab|verf[üu]gbar\s*ab|bezugsfrei\s*ab|bezugsfertig\s*ab|beziehbar\s*ab|"
    r"bezug\s*(?:ab|zum)?|einzug\s*(?:ab|zum)?|mietbeginn|verf[üu]gbarkeit|"
    r"[üu]bergabe\s*(?:ab|zum)?|zum)\b",
    re.I,
)

_D_PUNKT = re.compile(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b")          # 01.03.2027
_D_MONATSNAME = re.compile(
    r"\b(?:(\d{1,2})\.?\s*)?(" + "|".join(MONATE) + r")\.?\s+(\d{4})\b", re.I
)                                                                            # 1. März 2027
_D_MM_JJJJ = re.compile(r"\b(0?[1-9]|1[0-2])\s*[/.]\s*(20\d{2})\b")          # 04/2027
_D_QUARTAL = re.compile(r"\bQ([1-4])[ /.-]*(20\d{2})\b", re.I)               # Q1 2027
_D_JJJJ_MM = re.compile(r"\b(20\d{2})-(0?[1-9]|1[0-2])(?:-(\d{1,2}))?\b")    # 2027-01-15
_D_NUR_JAHR = re.compile(r"\b(20[2-9]\d)\b")


def _sicher(jahr: int, monat: int, tag: int) -> date | None:
    try:
        return date(jahr, monat, tag)
    except ValueError:
        return None


def _datum_aus(fragment: str) -> date | None:
    """Zieht das erste plausible Datum aus einem Textfragment."""
    if (t := _D_PUNKT.search(fragment)):
        return _sicher(int(t.group(3)), int(t.group(2)), int(t.group(1)))
    if (t := _D_JJJJ_MM.search(fragment)):
        return _sicher(int(t.group(1)), int(t.group(2)), int(t.group(3) or 1))
    if (t := _D_MONATSNAME.search(fragment)):
        return _sicher(int(t.group(3)), MONATE[t.group(2).lower()], int(t.group(1) or 1))
    if (t := _D_QUARTAL.search(fragment)):
        monat, tag = QUARTAL_START[int(t.group(1))]
        return _sicher(int(t.group(2)), monat, tag)
    if (t := _D_MM_JJJJ.search(fragment)):
        return _sicher(int(t.group(2)), int(t.group(1)), 1)
    if (t := _D_NUR_JAHR.search(fragment)):
        # "Einzug 2027" -> konservativ auf den Jahresanfang legen
        return _sicher(int(t.group(1)), 1, 1)
    return None


def einzugsdatum(text: str | None) -> tuple[date | None, str | None]:
    """Sucht den Einzugstermin.

    Rückgabe: (Datum oder None, gefundenes Rohfragment oder None).

    Vorgehen: erst nach expliziten Sofort-Signalen suchen (die schlagen jedes
    Datum), dann im Umfeld eines Ankerworts nach einem Datum. Ohne Anker wird
    nicht geraten – "Baujahr 1968" oder "saniert 2019" sollen kein Treffer sein.
    """
    if not text:
        return None, None
    text = " ".join(text.replace("\xa0", " ").split())

    if (t := _SOFORT.search(text)):
        return None, t.group(0)

    for anker in _ANKER.finditer(text):
        # Fenster: 60 Zeichen hinter dem Anker – lang genug für
        # "frei ab dem 1. Januar 2027", kurz genug, um nicht in den
        # nächsten Satz zu rutschen.
        fenster = text[anker.end(): anker.end() + 60]
        gefunden = _datum_aus(fenster)
        if gefunden:
            rohtext = (anker.group(0) + fenster).strip()
            return gefunden, rohtext[:80]

    if _NACH_VEREINBARUNG.search(text):
        return None, "nach Vereinbarung"
    return None, None


def einzugsstatus(
    text: str | None, stichtag: date, sofort_signale: list[str] | None = None
) -> tuple[Einzugsstatus, date | None, str | None]:
    """Bewertet den Einzugstermin gegen den Stichtag.

    Gibt (Status, Datum, Rohtext) zurück. Ein gefundenes Sofort-Signal ohne
    Datum gilt als ZU_FRUEH – das ist der häufigste Ausschlussfall.
    """
    if not text:
        return Einzugsstatus.UNBEKANNT, None, None

    flach = " ".join(text.lower().split())
    for signal in sofort_signale or []:
        if signal.lower() in flach:
            return Einzugsstatus.ZU_FRUEH, None, signal

    gefunden, rohtext = einzugsdatum(text)
    if gefunden is None:
        if rohtext and _SOFORT.search(rohtext):
            return Einzugsstatus.ZU_FRUEH, None, rohtext
        return Einzugsstatus.UNBEKANNT, None, rohtext

    status = Einzugsstatus.PASST if gefunden >= stichtag else Einzugsstatus.ZU_FRUEH
    return status, gefunden, rohtext


# --------------------------------------------------------------- Ausstattung

_MERKMALE: dict[str, tuple[re.Pattern[str], re.Pattern[str] | None]] = {
    # Feld: (positiv, negativ)
    #
    # Zusammensetzungen sind im Deutschen die Regel, nicht die Ausnahme:
    # "Südbalkon", "Kellerabteil", "Personenaufzug". Ein führendes \b würde
    # genau die häufigsten Schreibweisen verfehlen, deshalb ist der
    # Wortanfang offen und nur das Wortende gebunden.
    "balkon": (re.compile(r"\w*(balkon|loggia|terrasse)\w*", re.I),
               re.compile(r"\b(kein|keinen|ohne)\s+\w*(balkon|terrasse)\w*", re.I)),
    "keller": (re.compile(r"\w*(keller|abstellraum)\w*", re.I),
               re.compile(r"\b(kein|keinen|ohne)\s+\w*keller\w*", re.I)),
    "einbaukueche": (re.compile(r"(einbauk[üu]che\w*|\bebk\b|k[üu]che\s+vorhanden|"
                               r"k[üu]chenzeile|offene\s+k[üu]che)", re.I),
                     re.compile(r"\b(ohne|keine)\s+(einbauk[üu]che|k[üu]che)", re.I)),
    "aufzug": (re.compile(r"\w*(aufzug|fahrstuhl)\w*|\blift\b", re.I),
               re.compile(r"\b(kein|keinen|ohne)\s+\w*(aufzug|fahrstuhl)\w*", re.I)),
    "altbau": (re.compile(r"\w*(altbau|gr[üu]nderzeit|jugendstil)\w*|\bstuck\b", re.I), None),
    "wg_geeignet": (re.compile(r"\b(wg[- ]?geeignet|wg[- ]?tauglich|f[üu]r\s+(eine\s+)?wg|"
                               r"wg\s+m[öo]glich|zweier[- ]?wg|2er[- ]?wg)\b", re.I),
                    re.compile(r"\b(keine\s+wg|nicht\s+wg[- ]?geeignet|wg\s+ausgeschlossen)\b", re.I)),
    "oepnv_erwaehnt": (re.compile(r"\b(u-?bahn|s-?bahn|stra[ßs]enbahn|tram|[öo]pnv|u\d\b|s\d\b)\b|"
                                  r"\w*(haltestelle|verkehrsanbindung)\w*", re.I), None),
}


def ausstattung(text: str | None) -> Ausstattung:
    """Erkennt Ausstattungsmerkmale.

    Dreiwertig: True = belegt, False = ausdrücklich verneint, None = nicht
    erwähnt. Der Unterschied zwischen "kein Balkon" und "steht nichts dazu"
    ist im Ranking relevant.
    """
    ergebnis = Ausstattung()
    if not text:
        return ergebnis
    for feld, (positiv, negativ) in _MERKMALE.items():
        if negativ and negativ.search(text):
            setattr(ergebnis, feld, False)
        elif positiv.search(text):
            setattr(ergebnis, feld, True)
    return ergebnis


# ------------------------------------------------------------- Vermietertyp

_GEWERBLICH = re.compile(
    r"\b(immobilien|makler|gmbh|ag\b|kg\b|ohg|e\.?\s?k\.?|verwaltung|hausverwaltung|"
    r"real\s?estate|properties|wohnungsgesellschaft|genossenschaft|provision|"
    r"courtage|maklerprovision|k[äa]uferprovision)\b",
    re.I,
)
_PRIVAT = re.compile(
    r"\b(privat(vermieter|person|anbieter)?|von\s+privat|provisionsfrei|"
    r"keine\s+provision|ohne\s+makler|privatverkauf)\b",
    re.I,
)


def vermietertyp(anbieter: str | None, text: str | None = None) -> Vermietertyp:
    zusammen = f"{anbieter or ''} {text or ''}"
    if not zusammen.strip():
        return Vermietertyp.UNBEKANNT
    # "provisionsfrei" allein reicht nicht – Firmen werben ebenfalls damit.
    if _GEWERBLICH.search(anbieter or "") or _GEWERBLICH.search(zusammen):
        if _PRIVAT.search(anbieter or ""):
            return Vermietertyp.PRIVAT
        return Vermietertyp.GEWERBLICH
    if _PRIVAT.search(zusammen):
        return Vermietertyp.PRIVAT
    return Vermietertyp.UNBEKANNT


# ------------------------------------------------------------------ Qualität

def beschreibung_ist_gut(text: str | None, min_zeichen: int = 350) -> bool:
    """Heuristik für "gute Beschreibung".

    Länge allein genügt nicht – Makler füllen mit Textbausteinen. Deshalb
    zusätzlich: ausreichend viele verschiedene Wörter und mindestens ein
    konkretes Detail (Zahl, Straße, Stockwerk).
    """
    if not text:
        return False
    saeuberlich = " ".join(text.split())
    if len(saeuberlich) < min_zeichen:
        return False
    woerter = re.findall(r"\w+", saeuberlich.lower(), re.UNICODE)
    if len(set(woerter)) < 45:
        return False
    return bool(re.search(r"\d", saeuberlich))


def bilder_sind_gut(bilder: list[str], mindestens: int = 4) -> bool:
    echte = [b for b in bilder if b and not re.search(r"(placeholder|no[-_]?image|logo|dummy)", b, re.I)]
    return len(echte) >= mindestens


def enthaelt_stichwort(text: str, stichwoerter: list[str]) -> str | None:
    """Erstes gefundenes Stichwort oder None. Wortgrenzen, damit 'WBS' nicht
    in 'WBSchmidt' trifft."""
    flach = text.lower()
    for wort in stichwoerter:
        muster = r"\b" + re.escape(wort.lower()).replace(r"\ ", r"\s+") + r"\b"
        if re.search(muster, flach):
            return wort
    return None
