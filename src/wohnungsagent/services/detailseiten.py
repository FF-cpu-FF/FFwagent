"""Auswertung von Detailseiten.

Trefferlisten sind knapp: WG-Gesucht nennt dort weder den Verfügbarkeits-
zeitraum noch Nebenkosten, Kleinanzeigen kein Enddatum. Genau diese Felder
entscheiden aber darüber, ob ein Inserat überhaupt in Frage kommt — eine
Wohnung "frei ab 19.10.2026 bis 31.01.2027" ist bei Einzug im Januar 2027
wertlos, sieht in der Liste aber aus wie ein Treffer.

Deshalb holt der Agent für die wenigen Inserate, die alle harten Filter
überstanden haben, zusätzlich die Detailseite. Das sind pro Lauf eine
Handvoll Abrufe, keine hundert.

Ausgewertet wird über **Beschriftungen**, nicht über CSS-Klassen: "frei ab",
"Kaution", "Nebenkosten" stehen auf beiden Portalen im Text, die Klassennamen
ändern sich dagegen bei jedem Deploy. Ein Parser reicht damit für alle
Quellen.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models.domain import Inserat
from .parsing import einzugsdatum, euro

# Beschriftung -> Feld. Reihenfolge zählt: die erste passende gewinnt,
# deshalb stehen die spezifischen Varianten oben.
GELDFELDER: list[tuple[str, str]] = [
    (r"gesamtmiete|warmmiete|miete\s*inkl", "warmmiete"),
    (r"nebenkosten|betriebskosten", "nebenkosten"),
    (r"kaution|sicherheitsleistung", "kaution"),
    (r"kaltmiete|grundmiete|nettomiete", "kaltmiete"),
    (r"\bmiete\b", "kaltmiete"),
]

_FREI_AB = re.compile(
    r"(?:frei|verf[üu]gbar|bezugsfrei|beziehbar)\s*ab\s*:?\s*"
    r"(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})",
    re.I,
)
_FREI_BIS = re.compile(
    r"(?:frei|verf[üu]gbar|vermietet|befristet)\s*bis\s*:?\s*"
    r"(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})",
    re.I,
)
_BEFRISTET = re.compile(
    r"\b(befristet|zwischenmiete|untermiete|zeitlich\s+begrenzt|"
    r"tempor[äa]r|auf\s+zeit)\b",
    re.I,
)

# Bilddateien, die keine Wohnungsfotos sind
_KEIN_FOTO = re.compile(
    r"(logo|sprite|icon|avatar|placeholder|dummy|pixel|badge|favicon|"
    r"\.svg($|\?)|blank\.gif)",
    re.I,
)


def _datum(text: str):
    """'19.10.2026' oder '2026-10-19' -> date"""
    from datetime import date

    if (t := re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)):
        tag, monat, jahr = (int(g) for g in t.groups())
    elif (t := re.match(r"(\d{4})-(\d{2})-(\d{2})", text)):
        jahr, monat, tag = (int(g) for g in t.groups())
    else:
        return None
    try:
        return date(jahr, monat, tag)
    except ValueError:
        return None


def _betrag_hinter(text: str, beschriftung: str) -> float | None:
    """Sucht den ersten Geldbetrag hinter einer Beschriftung.

    Fenster von 40 Zeichen: lang genug für 'Nebenkosten: 250 €', kurz genug,
    um nicht den nächsten Tabelleneintrag mitzunehmen.
    """
    for treffer in re.finditer(beschriftung + r"\s*:?\s*", text, re.I):
        fenster = text[treffer.end(): treffer.end() + 40]
        if re.match(r"\s*(n\.?\s?a\.?|keine|auf anfrage|--)", fenster, re.I):
            continue                                  # ausdrücklich ohne Angabe
        betrag = euro(fenster)
        if betrag:
            return betrag
    return None


def _bilder(suppe: BeautifulSoup, basis_url: str) -> list[str]:
    gefunden: list[str] = []
    for bild in suppe.find_all("img"):
        for attribut in ("src", "data-src", "data-original", "data-lazy"):
            quelle = bild.get(attribut)
            if not quelle or _KEIN_FOTO.search(quelle):
                continue
            voll = urljoin(basis_url, quelle)
            if voll.startswith("http") and voll not in gefunden:
                gefunden.append(voll)
            break
    # Open-Graph-Bild ist meist das Titelfoto und gehört nach vorn
    og = suppe.find("meta", property="og:image")
    if og and og.get("content"):
        titelbild = urljoin(basis_url, og["content"])
        if titelbild in gefunden:
            gefunden.remove(titelbild)
        gefunden.insert(0, titelbild)
    return gefunden[:8]


def lies_detail(html: str, inserat: Inserat) -> Inserat:
    """Reichert ein Inserat mit den Angaben seiner Detailseite an.

    Vorhandene Werte werden überschrieben – die Detailseite ist genauer als
    die Trefferliste. Einzige Ausnahme sind Felder, die dort fehlen.
    """
    suppe = BeautifulSoup(html, "lxml")
    for stoerer in suppe(["script", "style", "nav", "footer"]):
        stoerer.decompose()
    text = " ".join(suppe.get_text(" ", strip=True).split())

    # --- Verfügbarkeitszeitraum: der eigentliche Grund für diesen Umweg ---
    if (t := _FREI_AB.search(text)) and (datum := _datum(t.group(1))):
        inserat.einzug_ab = datum
        inserat.einzug_rohtext = t.group(0)[:80]
    elif not inserat.einzug_ab:
        gefunden, rohtext = einzugsdatum(text)
        if gefunden:
            inserat.einzug_ab = gefunden
            inserat.einzug_rohtext = rohtext

    if (t := _FREI_BIS.search(text)) and (datum := _datum(t.group(1))):
        inserat.frei_bis = datum
        inserat.befristet = True
    elif _BEFRISTET.search(text):
        inserat.befristet = True

    # --- Geldbeträge ---
    for muster, feld in GELDFELDER:
        if getattr(inserat, feld) is None and (betrag := _betrag_hinter(text, muster)):
            setattr(inserat, feld, betrag)

    if inserat.warmmiete is None and inserat.kaltmiete and inserat.nebenkosten:
        inserat.warmmiete = round(inserat.kaltmiete + inserat.nebenkosten, 2)

    # --- Eckdaten nachtragen, wo sie fehlten ---
    if inserat.flaeche is None and (
        t := re.search(r"(?:gr[öo][ßs]e|wohnfl[äa]che)\s*:?\s*(\d{1,4}(?:[.,]\d+)?)\s*m", text, re.I)
    ):
        inserat.flaeche = float(t.group(1).replace(",", "."))
    if inserat.zimmer is None and (
        t := re.search(r"zimmer\s*:?\s*(\d{1,2}(?:[.,]\d)?)\b", text, re.I)
    ):
        inserat.zimmer = float(t.group(1).replace(",", "."))

    # --- Adresse ---
    if not inserat.adresse and (
        t := re.search(r"([A-ZÄÖÜ][\wäöüß.\- ]{4,40}(?:stra[ßs]e|str\.|weg|allee|platz|gasse))"
                       r"\s*(?:\d{1,4}[a-z]?)?\s*,?\s*(6[05]\d{3})", text)
    ):
        inserat.adresse = f"{t.group(1).strip()}, {t.group(2)}"
        inserat.plz = inserat.plz or t.group(2)

    # --- Bilder: in der Liste steht meist nur eins ---
    detailbilder = _bilder(suppe, inserat.url)
    if len(detailbilder) > len(inserat.bilder):
        inserat.bilder = detailbilder

    inserat.detail_gelesen = True
    return inserat
