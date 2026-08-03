"""Punktesystem.

Die Sternbewertung aus dem Suchprofil ist in Rohpunkte übersetzt (5★=25,
4★=18, 3★=10, konfigurierbar in `config/suchprofil.yml`). Jedes Kriterium ist
ein Prädikat; der Endscore ist der erreichte Anteil an den erreichbaren
Punkten, auf 0–100 normiert.

Normiert wird gegen die *erreichbaren*, nicht die maximalen Punkte: Kriterien,
die für ein Inserat mangels Daten gar nicht prüfbar sind, fallen aus Zähler
und Nenner heraus. Sonst würde ein sehr gutes Inserat mit knapper Beschreibung
allein deshalb schlecht ranken, weil das Portal wenig Text liefert. Die
Datenlücke selbst wird stattdessen über `ranking.malus_luecke` bepreist –
einmal und sichtbar, statt versteckt über den Nenner.
"""
from __future__ import annotations

from collections.abc import Callable

from ..config.profil import Suchprofil
from ..models.domain import Bewertung, Einzugsstatus, Inserat, Vermietertyp
from ..services.geo import normalisiere_stadtteil
from ..services.parsing import beschreibung_ist_gut, bilder_sind_gut

# Prädikat: True = erfüllt, False = nicht erfüllt, None = nicht prüfbar
Praedikat = Callable[[Inserat, Suchprofil], bool | None]


def _lage_bevorzugt(inserat: Inserat, profil: Suchprofil) -> bool | None:
    schluessel = normalisiere_stadtteil(inserat.stadtteil) or normalisiere_stadtteil(inserat.adresse)
    if schluessel is None:
        return None
    bevorzugt = {normalisiere_stadtteil(s) for s in profil.lage.bevorzugt}
    return schluessel in bevorzugt


def _distanz_nah(inserat: Inserat, profil: Suchprofil) -> bool | None:
    if inserat.distanz_km is None:
        return None
    return inserat.distanz_km <= profil.referenzpunkt.bonus_km


def _zimmer_ideal(inserat: Inserat, profil: Suchprofil) -> bool | None:
    if inserat.zimmer is None:
        return None
    return inserat.zimmer == profil.wohnung.zimmer_bevorzugt


def _budget_eingehalten(inserat: Inserat, profil: Suchprofil) -> bool | None:
    warm = inserat.warmmiete
    if warm is None and inserat.kaltmiete:
        warm = inserat.kaltmiete * profil.budget.warmmiete_schaetzfaktor
    if warm is None:
        return None
    return profil.budget.warmmiete_min <= warm <= profil.budget.warmmiete_max


def _einzug_bestaetigt(inserat: Inserat, _: Suchprofil) -> bool | None:
    if inserat.einzug_status is Einzugsstatus.PASST:
        return True
    if inserat.einzug_status is Einzugsstatus.UNBEKANNT:
        return None
    return False


def _flag(name: str) -> Praedikat:
    def praedikat(inserat: Inserat, _: Suchprofil) -> bool | None:
        return getattr(inserat.ausstattung, name)

    return praedikat


def _beschreibung(inserat: Inserat, _: Suchprofil) -> bool | None:
    if not inserat.beschreibung:
        return None
    return beschreibung_ist_gut(inserat.beschreibung)


def _bilder(inserat: Inserat, _: Suchprofil) -> bool | None:
    if not inserat.bilder:
        return None
    return bilder_sind_gut(inserat.bilder)


def _privat(inserat: Inserat, _: Suchprofil) -> bool | None:
    if inserat.vermietertyp is Vermietertyp.UNBEKANNT:
        return None
    return inserat.vermietertyp is Vermietertyp.PRIVAT


KRITERIEN: dict[str, tuple[str, Praedikat]] = {
    "lage_bevorzugt": ("Nordend / Westend-Nord", _lage_bevorzugt),
    "distanz_unter_bonus_km": ("nah an der Frankfurt School", _distanz_nah),
    "zimmer_ideal": ("3 Zimmer", _zimmer_ideal),
    "budget_eingehalten": ("Warmmiete im Budgetkorridor", _budget_eingehalten),
    "einzug_bestaetigt": ("Einzugstermin belegt ab Stichtag", _einzug_bestaetigt),
    "balkon": ("Balkon", _flag("balkon")),
    "keller": ("Keller", _flag("keller")),
    "einbaukueche": ("Einbauküche", _flag("einbaukueche")),
    "beschreibung_gut": ("aussagekräftige Beschreibung", _beschreibung),
    "bilder_gut": ("ausreichend Bilder", _bilder),
    "privatvermieter": ("privater Vermieter", _privat),
}

PFLICHTFELDER = ("warmmiete", "flaeche", "zimmer")


def bewerte(inserat: Inserat, profil: Suchprofil) -> Bewertung:
    gewichte = profil.ranking.gewichte
    erreicht = 0.0
    erreichbar = 0.0
    treffer: list[str] = []
    abzuege: list[str] = []

    for schluessel, (beschriftung, praedikat) in KRITERIEN.items():
        gewicht = gewichte.get(schluessel, 0)
        if not gewicht:
            continue
        ergebnis = praedikat(inserat, profil)
        if ergebnis is None:
            continue                       # nicht prüfbar: aus Zähler und Nenner
        erreichbar += gewicht
        if ergebnis:
            erreicht += gewicht
            treffer.append(f"{beschriftung} (+{gewicht})")

    # Distanz zusätzlich stufenlos honorieren, damit 1,2 km und 1,9 km
    # nicht identisch ranken.
    if inserat.distanz_km is not None and profil.referenzpunkt.max_km > 0:
        anteil = max(0.0, 1 - inserat.distanz_km / profil.referenzpunkt.max_km)
        erreicht += anteil * 6
        erreichbar += 6

    score = (erreicht / erreichbar * 100) if erreichbar else 0.0

    # Dämpfung nach Datenlage. Ohne sie bekäme ein Inserat, bei dem nur ein
    # einziges Kriterium prüfbar ist und zufällig zutrifft, denselben Score
    # wie ein durchgehend belegtes – der Nenner wäre ja ebenso klein. Die
    # Abdeckung ist der Anteil der überhaupt prüfbaren Gewichte.
    gesamtgewicht = sum(
        g for k, g in gewichte.items() if k in KRITERIEN and g
    ) + (6 if inserat.distanz_km is not None else 0)
    abdeckung = (erreichbar / gesamtgewicht) if gesamtgewicht else 0.0
    daempfung = 0.55 + 0.45 * min(1.0, abdeckung)
    if abdeckung < 0.75:
        abzuege.append(f"nur {abdeckung:.0%} der Kriterien belegbar (×{daempfung:.2f})")
    score *= daempfung

    # --- Abzüge -----------------------------------------------------------
    for feld in PFLICHTFELDER:
        if getattr(inserat, feld) is None:
            score -= profil.ranking.malus_luecke
            abzuege.append(f"{feld} fehlt (-{profil.ranking.malus_luecke})")

    if inserat.einzug_status is Einzugsstatus.UNBEKANNT:
        score -= profil.einzug.malus_unbekannt
        abzuege.append(f"Einzugstermin unbestätigt (-{profil.einzug.malus_unbekannt})")

    if inserat.ausstattung.wg_geeignet is False:
        score -= 15
        abzuege.append("WG ausgeschlossen (-15)")

    if inserat.flaeche is not None and inserat.flaeche < profil.wohnung.flaeche_min:
        fehlend = profil.wohnung.flaeche_min - inserat.flaeche
        malus = min(10.0, fehlend * 0.5)
        score -= malus
        abzuege.append(f"{fehlend:.0f} m² unter Wunschgröße (-{malus:.0f})")

    # --- KI-Korrektur -----------------------------------------------------
    if inserat.ki and inserat.ki.punkte_delta:
        grenze = profil.ranking.llm_einfluss_max
        delta = max(-grenze, min(grenze, inserat.ki.punkte_delta))
        score += delta
        (treffer if delta > 0 else abzuege).append(f"KI-Einschätzung ({delta:+})")

    return Bewertung(
        score=int(max(0, min(100, round(score)))),
        rohpunkte=round(erreicht, 2),
        maximalpunkte=round(erreichbar, 2),
        treffer=treffer,
        abzuege=abzuege,
    )
