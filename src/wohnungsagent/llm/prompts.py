"""Prompts für die semantische Bewertung."""
from __future__ import annotations

from ..config.profil import Suchprofil
from ..models.domain import Inserat

ANWEISUNG = """\
Du prüfst Wohnungsinserate aus Frankfurt am Main für eine Person, die eine
Wohnung für eine Zweier-WG sucht.

Antworte ausschließlich mit einem JSON-Objekt, ohne Vorrede und ohne
Markdown-Backticks. Struktur:

{
  "zusammenfassung": "2-3 Sätze auf Deutsch, konkret und ohne Werbesprache",
  "wg_geeignet": true | false | null,
  "seriositaet": 1-5,
  "lagebewertung": 1-5,
  "warnsignale": ["kurze Stichpunkte"],
  "fehlende_angaben": ["kurze Stichpunkte"],
  "punkte_delta": -10 bis +10
}

Regeln:

- Bewerte nur, was im Text steht. Erfinde nichts – weder Ausstattung noch
  Lage noch Termine. Was fehlt, gehört unter "fehlende_angaben".
- Preis, Fläche, Zimmerzahl, Entfernung und Einzugstermin sind bereits
  automatisch geprüft. Wiederhole sie nicht und rechne sie nicht nach.
  Konzentriere dich auf das, was nur aus dem Text hervorgeht.
- "wg_geeignet": true nur bei annähernd gleich großen Zimmern, getrennten
  Räumen oder ausdrücklicher WG-Eignung. Bei durchgehendem Raum, Durchgangs-
  zimmer oder ausgeschlossener WG-Nutzung false. Sonst null.
- "seriositaet": niedrig bei Vorkasse-Aufforderung, Kontakt nur per
  WhatsApp/Auslandsnummer, Schlüsselversand, auffällig niedrigem Preis,
  Textbausteinen ohne konkrete Angaben oder gestohlen wirkenden Fotos.
- "warnsignale" nennt versteckte Nachteile: Erdgeschoss zur Straße,
  Hauptverkehrsachse, Sanierung angekündigt, Staffelmiete, Kurzzeitvertrag,
  Modernisierungsumlage, Gewerbe im Haus, Hellhörigkeit.
- "punkte_delta" ist eine Feinkorrektur, kein Gesamturteil: +10 nur bei
  wirklich außergewöhnlich guter Substanz, -10 bei begründetem Betrugsverdacht.
  Im Normalfall liegt der Wert zwischen -3 und +3.
"""


def baue_nutzeranfrage(inserat: Inserat, profil: Suchprofil) -> str:
    ausstattung = {k: v for k, v in inserat.ausstattung.als_dict().items() if v is not None}
    zeilen = [
        f"Suchprofil: Zweier-WG, {profil.wohnung.zimmer_bevorzugt:g} Zimmer bevorzugt, "
        f"ab {profil.wohnung.flaeche_min:g} m², Warmmiete {profil.budget.warmmiete_min:.0f}–"
        f"{profil.budget.warmmiete_max:.0f} €, Einzug frühestens "
        f"{profil.einzug.fruehestens:%d.%m.%Y}, Lage {', '.join(profil.lage.bevorzugt)}.",
        "",
        "--- Inserat ---",
        f"Titel: {inserat.titel}",
        f"Quelle: {inserat.quelle}",
        f"Anbieter: {inserat.anbieter or 'nicht genannt'}",
        f"Kaltmiete: {_geld(inserat.kaltmiete)} | Nebenkosten: {_geld(inserat.nebenkosten)} "
        f"| Warmmiete: {_geld(inserat.warmmiete)}",
        f"Zimmer: {inserat.zimmer or '?'} | Fläche: {inserat.flaeche or '?'} m² "
        f"| Etage: {inserat.etage or '?'}",
        f"Lage: {inserat.stadtteil or '?'}"
        + (f", {inserat.distanz_km:.1f} km zur {profil.referenzpunkt.name}"
           if inserat.distanz_km is not None else ""),
        f"Einzug laut Inserat: {inserat.einzug_rohtext or 'nicht angegeben'}",
        f"Erkannte Ausstattung: {ausstattung or 'keine Angabe'}",
        f"Anzahl Bilder: {len(inserat.bilder)}",
        "",
        "Beschreibungstext:",
        (inserat.beschreibung or "(keine Beschreibung im Inserat)")[:2500],
    ]
    return "\n".join(zeilen)


def _geld(wert: float | None) -> str:
    return f"{wert:.0f} €" if wert else "?"
