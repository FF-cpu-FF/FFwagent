"""Duplikaterkennung.

Dieselbe Wohnung erscheint regelmäßig auf ImmoScout, Immowelt und Immonet
gleichzeitig – teils vom Makler eingestellt, teils von Portal-Partnerschaften
gespiegelt. Titel und Beschreibung weichen dabei leicht ab, die harten Zahlen
nicht.

Verfahren:
  1. Blockbildung über `Inserat.dedupe_schluessel` (gerundete Warmmiete,
     Fläche, Zimmer). Nur innerhalb eines Blocks wird verglichen – sonst wäre
     der Vergleich quadratisch.
  2. Innerhalb des Blocks: gewichtete Ähnlichkeit aus Zahlenabgleich,
     Titelähnlichkeit und Adressabgleich.
  3. Aus einer Gruppe gewinnt das vollständigste Inserat; die anderen werden
     als Dubletten mit Verweis auf den Gewinner gespeichert.

RapidFuzz wird genutzt, wenn installiert (deutlich schneller). Ohne
RapidFuzz greift `difflib` aus der Standardbibliothek – identische Ergebnisse
bei kleinen Mengen, nur langsamer.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..models.domain import Inserat

try:  # pragma: no cover - reine Optimierung
    from rapidfuzz.fuzz import token_set_ratio as _ratio

    def _titelaehnlichkeit(a: str, b: str) -> float:
        return _ratio(a, b) / 100.0

except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def _titelaehnlichkeit(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        wa, wb = set(a.split()), set(b.split())
        jaccard = len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0
        sequenz = SequenceMatcher(None, a, b).ratio()
        return max(jaccard, sequenz)


SCHWELLE = 0.82


def _zahlen_passen(a: Inserat, b: Inserat) -> float | None:
    """Ähnlichkeit der harten Zahlen. None, wenn zu wenig Daten vorliegen."""
    paare: list[tuple[float, float, float]] = []   # (wert_a, wert_b, toleranz)
    if a.flaeche and b.flaeche:
        paare.append((a.flaeche, b.flaeche, 0.03))
    if a.warmmiete and b.warmmiete:
        paare.append((a.warmmiete, b.warmmiete, 0.03))
    if a.kaltmiete and b.kaltmiete:
        paare.append((a.kaltmiete, b.kaltmiete, 0.03))
    if a.zimmer and b.zimmer:
        paare.append((a.zimmer, b.zimmer, 0.0))
    if len(paare) < 2:
        return None
    punkte = 0.0
    for wert_a, wert_b, toleranz in paare:
        grenze = max(wert_a, wert_b) * toleranz
        punkte += 1.0 if abs(wert_a - wert_b) <= grenze else 0.0
    return punkte / len(paare)


def _adresse_passt(a: Inserat, b: Inserat) -> float | None:
    if not (a.adresse and b.adresse):
        return None
    return _titelaehnlichkeit(a.adresse.lower(), b.adresse.lower())


def aehnlichkeit(a: Inserat, b: Inserat) -> float:
    """0.0 bis 1.0. Gewichtet: Zahlen 55 %, Titel 30 %, Adresse 15 %."""
    if a.quelle == b.quelle and a.externe_id == b.externe_id:
        return 1.0

    zahlen = _zahlen_passen(a, b)
    if zahlen is not None and zahlen < 0.5:
        return 0.0                       # Zahlen widersprechen sich klar

    adresse = _adresse_passt(a, b)

    # Ohne belastbare Zahlen und ohne Adresse bleibt nur der Titel – und
    # allein darauf zwei Inserate zusammenzuwerfen ist zu riskant. Portale
    # liefern reihenweise Titel wie "2-Zimmer-Wohnung in Frankfurt"; die
    # würden sonst zu einem einzigen Eintrag verschmelzen und echte
    # Angebote verschwinden lassen. Im Zweifel lieber ein Duplikat zeigen
    # als ein Inserat unterschlagen.
    if zahlen is None and adresse is None:
        return 0.0

    beitraege: list[tuple[float, float]] = []
    if zahlen is not None:
        beitraege.append((zahlen, 0.55))
    beitraege.append((_titelaehnlichkeit(a.normalisierter_titel, b.normalisierter_titel), 0.30))
    if adresse is not None:
        beitraege.append((adresse, 0.15))

    gewicht = sum(g for _, g in beitraege)
    return round(sum(w * g for w, g in beitraege) / gewicht, 4) if gewicht else 0.0


def _vollstaendigkeit(inserat: Inserat) -> tuple[int, int, int]:
    """Sortierschlüssel für die Wahl des besten Inserats einer Gruppe."""
    felder = [
        inserat.warmmiete, inserat.kaltmiete, inserat.nebenkosten, inserat.flaeche,
        inserat.zimmer, inserat.adresse, inserat.stadtteil, inserat.einzug_ab,
        inserat.etage, inserat.kaution,
    ]
    gefuellt = sum(1 for f in felder if f not in (None, ""))
    ausstattung = sum(1 for v in inserat.ausstattung.als_dict().values() if v is not None)
    return gefuellt, ausstattung, len(inserat.beschreibung) + 40 * len(inserat.bilder)


@dataclass(slots=True)
class Gruppe:
    beste: Inserat
    dubletten: list[Inserat]

    @property
    def quellen(self) -> list[str]:
        return sorted({self.beste.quelle} | {d.quelle for d in self.dubletten})


def gruppiere(inserate: list[Inserat], schwelle: float = SCHWELLE) -> list[Gruppe]:
    """Fasst Dubletten zusammen und wählt je Gruppe das beste Inserat."""
    bloecke: dict[str, list[Inserat]] = defaultdict(list)
    for inserat in inserate:
        bloecke[inserat.dedupe_schluessel].append(inserat)

    gruppen: list[Gruppe] = []
    for kandidaten in bloecke.values():
        offen = list(kandidaten)
        while offen:
            aktuell = offen.pop(0)
            zusammen = [aktuell]
            rest: list[Inserat] = []
            for anderer in offen:
                if aehnlichkeit(aktuell, anderer) >= schwelle:
                    zusammen.append(anderer)
                else:
                    rest.append(anderer)
            offen = rest
            zusammen.sort(key=_vollstaendigkeit, reverse=True)
            gruppen.append(Gruppe(beste=zusammen[0], dubletten=zusammen[1:]))
    return gruppen


def entdoppeln(inserate: list[Inserat], schwelle: float = SCHWELLE) -> list[Inserat]:
    """Bequemlichkeitsfunktion: nur die besten Inserate, Dubletten verworfen.

    Die Quellen der verworfenen Dubletten werden am Gewinner unter
    `merkmale` vermerkt, damit im Dashboard sichtbar bleibt, auf wie vielen
    Portalen ein Angebot läuft.
    """
    ergebnis: list[Inserat] = []
    for gruppe in gruppiere(inserate, schwelle):
        if gruppe.dubletten:
            weitere = sorted({d.quelle for d in gruppe.dubletten} - {gruppe.beste.quelle})
            if weitere:
                gruppe.beste.merkmale = list(gruppe.beste.merkmale) + [
                    f"auch auf: {', '.join(weitere)}"
                ]
        ergebnis.append(gruppe.beste)
    return ergebnis
