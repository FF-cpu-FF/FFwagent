"""Harte Ausschlussregeln.

Jede Regel ist eine reine Funktion `(Inserat, Profil) -> str | None`. Kein
Ausschlussgrund heißt None. Die Reihenfolge in `REGELN` bestimmt, welcher
Grund gemeldet wird, wenn mehrere greifen – die aussagekräftigste zuerst.

Grundhaltung: fehlende Angaben führen nicht zum Ausschluss. Ein Inserat ohne
Flächenangabe ist unvollständig, nicht ungeeignet; das kostet Punkte im
Ranking, fliegt aber nicht raus. Einzige Ausnahme ist der Einzugstermin,
weil das laut Suchprofil das wichtigste Kriterium ist – dort ist das
Verhalten über `einzug.unbekannt` konfigurierbar.
"""
from __future__ import annotations

from collections.abc import Callable

from ..config.profil import Suchprofil
from ..models.domain import Einzugsstatus, Inserat
from ..services.geo import normalisiere_stadtteil
from ..services.parsing import enthaelt_stichwort

Regel = Callable[[Inserat, Suchprofil], str | None]


def regel_einzug(inserat: Inserat, profil: Suchprofil) -> str | None:
    """Wichtigstes Kriterium: Einzug frühestens zum Stichtag."""
    if inserat.einzug_status is Einzugsstatus.ZU_FRUEH:
        wann = inserat.einzug_ab.strftime("%d.%m.%Y") if inserat.einzug_ab else (
            inserat.einzug_rohtext or "sofort"
        )
        return f"Einzug zu früh ({wann}, gefordert ab {profil.einzug.fruehestens:%d.%m.%Y})"
    if inserat.einzug_status is Einzugsstatus.UNBEKANNT and profil.einzug.unbekannt == "ausschliessen":
        return "Einzugstermin nicht angegeben (Profil verlangt Nachweis)"
    return None


def regel_zimmer(inserat: Inserat, profil: Suchprofil) -> str | None:
    if inserat.zimmer is None:
        return None
    if inserat.zimmer < profil.wohnung.zimmer_min:
        return f"nur {inserat.zimmer:g} Zimmer (mindestens {profil.wohnung.zimmer_min:g})"
    if profil.wohnung.zimmer_max and inserat.zimmer > profil.wohnung.zimmer_max:
        return f"{inserat.zimmer:g} Zimmer (höchstens {profil.wohnung.zimmer_max:g})"
    return None


def regel_flaeche(inserat: Inserat, profil: Suchprofil) -> str | None:
    if inserat.flaeche is None:
        return None
    if inserat.flaeche < profil.wohnung.flaeche_hart:
        return f"{inserat.flaeche:g} m² unter harter Untergrenze ({profil.wohnung.flaeche_hart} m²)"
    return None


def regel_budget(inserat: Inserat, profil: Suchprofil) -> str | None:
    """Deutlich über Budget = Ausschluss. Leicht darüber bleibt drin und
    verliert nur Ranking-Punkte."""
    warm = inserat.warmmiete
    if warm is None and inserat.kaltmiete:
        warm = inserat.kaltmiete * profil.budget.warmmiete_schaetzfaktor
    if warm is None:
        return None
    grenze = profil.budget.warmmiete_max * (1 + profil.budget.toleranz_prozent / 100)
    if warm > grenze:
        return f"Warmmiete {warm:.0f} € über Toleranzgrenze ({grenze:.0f} €)"
    return None


def regel_radius(inserat: Inserat, profil: Suchprofil) -> str | None:
    """Radius um den Referenzpunkt.

    Bei grob geschätzten Koordinaten (Stadtteil- oder PLZ-Zentroid) wird die
    Ungenauigkeit als Toleranz aufgeschlagen, damit ein Inserat am Rand des
    Nordends nicht an einem Zentroidfehler scheitert.
    """
    if inserat.distanz_km is None:
        return None
    toleranz_km = (inserat.geo.genauigkeit_m or 0) / 1000
    if inserat.distanz_km - toleranz_km > profil.referenzpunkt.max_km:
        return (
            f"{inserat.distanz_km:.1f} km zur {profil.referenzpunkt.name} "
            f"(höchstens {profil.referenzpunkt.max_km:g} km)"
        )
    return None


def regel_stadtteil(inserat: Inserat, profil: Suchprofil) -> str | None:
    erlaubt = {normalisiere_stadtteil(s) for s in profil.lage.erlaubt}
    erlaubt.discard(None)
    if not erlaubt:
        return None

    schluessel = normalisiere_stadtteil(inserat.stadtteil) or normalisiere_stadtteil(inserat.adresse)
    if schluessel is None:
        if profil.lage.ohne_stadtteil_ueber_radius:
            return None  # der Radius hat bereits entschieden
        return "Stadtteil nicht bestimmbar"
    if schluessel not in erlaubt:
        from ..services.geo import anzeigename

        return f"Stadtteil {anzeigename(schluessel)} nicht auf der Wunschliste"
    return None


def regel_stichwoerter(inserat: Inserat, profil: Suchprofil) -> str | None:
    treffer = enthaelt_stichwort(inserat.volltext, profil.ausschluss_stichwoerter)
    return f"Ausschlussstichwort: {treffer}" if treffer else None


REGELN: list[tuple[str, Regel]] = [
    ("einzug", regel_einzug),
    ("stichwoerter", regel_stichwoerter),
    ("zimmer", regel_zimmer),
    ("stadtteil", regel_stadtteil),
    ("radius", regel_radius),
    ("budget", regel_budget),
    ("flaeche", regel_flaeche),
]


def pruefe(inserat: Inserat, profil: Suchprofil) -> str | None:
    """Erster greifender Ausschlussgrund oder None."""
    for _, regel in REGELN:
        grund = regel(inserat, profil)
        if grund:
            return grund
    return None
