"""Tests für Ausschlussregeln und Punktesystem."""
from __future__ import annotations

from datetime import date

import pytest

from wohnungsagent.config.profil import lade_profil
from wohnungsagent.models.domain import Ausstattung, Einzugsstatus, Geo, Vermietertyp
from wohnungsagent.ranking import regeln, scoring

from .conftest import baue_inserat


def _profil():
    return lade_profil("config/suchprofil.yml")


# ------------------------------------------------------------- Ausschluss

def test_perfektes_inserat_wird_nicht_ausgeschlossen():
    assert regeln.pruefe(baue_inserat(), _profil()) is None


def test_einzug_zu_frueh_wird_ausgeschlossen():
    inserat = baue_inserat(einzug_status=Einzugsstatus.ZU_FRUEH, einzug_ab=date(2026, 9, 1))
    grund = regeln.pruefe(inserat, _profil())
    assert grund is not None and "Einzug zu früh" in grund


def test_einzug_ist_die_erste_regel():
    """Wichtigstes Kriterium: der Einzugsgrund wird gemeldet, auch wenn
    gleichzeitig andere Regeln greifen würden."""
    inserat = baue_inserat(
        einzug_status=Einzugsstatus.ZU_FRUEH, zimmer=1.0, warmmiete=4000.0
    )
    grund = regeln.pruefe(inserat, _profil())
    assert "Einzug zu früh" in grund


def test_unbekannter_einzug_bleibt_im_modus_pruefen():
    inserat = baue_inserat(einzug_status=Einzugsstatus.UNBEKANNT, einzug_ab=None)
    assert regeln.pruefe(inserat, _profil()) is None


def test_unbekannter_einzug_fliegt_im_strengen_modus():
    import dataclasses

    profil = _profil()
    streng = dataclasses.replace(profil, einzug=dataclasses.replace(profil.einzug, unbekannt="ausschliessen"))
    inserat = baue_inserat(einzug_status=Einzugsstatus.UNBEKANNT, einzug_ab=None)
    assert regeln.pruefe(inserat, streng) is not None


@pytest.mark.parametrize("zimmer", [1.0, 1.5, 5.0])
def test_zimmerzahl_ausserhalb_der_spanne(zimmer):
    assert regeln.pruefe(baue_inserat(zimmer=zimmer), _profil()) is not None


def test_fehlende_zimmerzahl_fliegt_nicht_raus():
    """Unvollständig ist nicht dasselbe wie ungeeignet."""
    assert regeln.pruefe(baue_inserat(zimmer=None), _profil()) is None


def test_budget_deutlich_ueberschritten():
    grund = regeln.pruefe(baue_inserat(warmmiete=2100.0), _profil())
    assert grund is not None and "Warmmiete" in grund


def test_budget_knapp_darueber_bleibt_drin():
    """1800 € + 8 % Toleranz = 1944 €."""
    assert regeln.pruefe(baue_inserat(warmmiete=1900.0), _profil()) is None


def test_warmmiete_wird_aus_kaltmiete_geschaetzt():
    inserat = baue_inserat(warmmiete=None, kaltmiete=1700.0)   # 1700 * 1.25 = 2125
    assert regeln.pruefe(inserat, _profil()) is not None


def test_falscher_stadtteil():
    grund = regeln.pruefe(baue_inserat(stadtteil="Bornheim"), _profil())
    assert grund is not None and "Bornheim" in grund


def test_radius_wird_geprueft():
    inserat = baue_inserat(stadtteil=None, distanz_km=7.5, geo=Geo(lat=50.09, lon=8.55, quelle="plz", genauigkeit_m=1200))
    grund = regeln.pruefe(inserat, _profil())
    assert grund is not None and "km" in grund


def test_zentroid_ungenauigkeit_wird_toleriert():
    """3,5 km bei ±700 m Zentroidfehler darf nicht hart aussortiert werden."""
    inserat = baue_inserat(
        stadtteil=None,
        distanz_km=3.5,
        geo=Geo(lat=50.15, lon=8.68, quelle="stadtteil", genauigkeit_m=700),
    )
    assert regeln.regel_radius(inserat, _profil()) is None


def test_ausschlussstichwort():
    inserat = baue_inserat(titel="3-Zimmer-Wohnung zur Zwischenmiete")
    grund = regeln.pruefe(inserat, _profil())
    assert grund is not None and "Zwischenmiete" in grund


# ---------------------------------------------------------------- Ranking

def test_perfektes_inserat_rankt_sehr_hoch():
    bewertung = scoring.bewerte(baue_inserat(), _profil())
    assert bewertung.score >= 90, (bewertung.score, bewertung.abzuege)


def test_score_bleibt_in_den_grenzen():
    for inserat in [
        baue_inserat(),
        baue_inserat(zimmer=None, flaeche=None, warmmiete=None, kaltmiete=None),
        baue_inserat(stadtteil="Bornheim", einzug_status=Einzugsstatus.UNBEKANNT,
                     ausstattung=Ausstattung(balkon=False, keller=False, einbaukueche=False,
                                             wg_geeignet=False),
                     vermietertyp=Vermietertyp.GEWERBLICH, beschreibung="kurz", bilder=[]),
    ]:
        score = scoring.bewerte(inserat, _profil()).score
        assert 0 <= score <= 100


def test_bevorzugte_lage_schlaegt_nachbarlage():
    profil = _profil()
    nordend = scoring.bewerte(baue_inserat(stadtteil="Nordend-West"), profil).score
    bornheim = scoring.bewerte(baue_inserat(stadtteil="Bornheim"), profil).score
    assert nordend > bornheim


def test_belegter_einzug_schlaegt_unbekannten():
    profil = _profil()
    belegt = scoring.bewerte(baue_inserat(), profil).score
    offen = scoring.bewerte(
        baue_inserat(einzug_status=Einzugsstatus.UNBEKANNT, einzug_ab=None), profil
    ).score
    assert belegt > offen + 10, (belegt, offen)


def test_drei_zimmer_schlaegt_zwei():
    profil = _profil()
    assert scoring.bewerte(baue_inserat(zimmer=3.0), profil).score > \
           scoring.bewerte(baue_inserat(zimmer=2.0), profil).score


def test_ausstattung_wirkt():
    profil = _profil()
    mit = scoring.bewerte(baue_inserat(), profil).score
    ohne = scoring.bewerte(
        baue_inserat(ausstattung=Ausstattung(balkon=False, keller=False, einbaukueche=False)),
        profil,
    ).score
    assert mit > ohne


def test_fehlende_pflichtfelder_kosten_punkte():
    profil = _profil()
    bewertung = scoring.bewerte(baue_inserat(flaeche=None), profil)
    assert any("flaeche fehlt" in a for a in bewertung.abzuege)


def test_naehere_wohnung_rankt_besser():
    profil = _profil()
    nah = scoring.bewerte(baue_inserat(distanz_km=0.6), profil).score
    fern = scoring.bewerte(baue_inserat(distanz_km=2.8), profil).score
    assert nah > fern


def test_ki_einfluss_ist_gedeckelt():
    from wohnungsagent.models.domain import KiBewertung

    profil = _profil()
    basis = scoring.bewerte(baue_inserat(stadtteil="Nordend-Ost", zimmer=2.0), profil).score
    inserat = baue_inserat(stadtteil="Nordend-Ost", zimmer=2.0)
    inserat.ki = KiBewertung(punkte_delta=500)
    aufgeblasen = scoring.bewerte(inserat, profil).score
    assert aufgeblasen - basis <= profil.ranking.llm_einfluss_max


def test_begruendung_ist_nachvollziehbar():
    bewertung = scoring.bewerte(baue_inserat(), _profil())
    assert bewertung.treffer
    assert any("Nordend" in t or "Zimmer" in t for t in bewertung.treffer)


def test_datenarmes_inserat_wird_gedaempft():
    """Ein Inserat, bei dem fast nichts prüfbar ist, darf nicht wie ein
    vollständig belegtes ranken – auch wenn das eine prüfbare Kriterium passt."""
    profil = _profil()
    voll = scoring.bewerte(baue_inserat(), profil)
    leer = scoring.bewerte(
        baue_inserat(
            zimmer=None, flaeche=None, warmmiete=None, kaltmiete=None,
            einzug_status=Einzugsstatus.UNBEKANNT, einzug_ab=None,
            ausstattung=Ausstattung(), vermietertyp=Vermietertyp.UNBEKANNT,
            beschreibung="", bilder=[],
        ),
        profil,
    )
    assert leer.score < 55, leer.score
    assert voll.score - leer.score > 30
    assert any("belegbar" in a for a in leer.abzuege)
