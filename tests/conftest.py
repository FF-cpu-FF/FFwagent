"""Gemeinsame Testbausteine."""
from __future__ import annotations

from datetime import date

import pytest

from wohnungsagent.config.profil import lade_profil
from wohnungsagent.models.domain import (
    Ausstattung,
    Einzugsstatus,
    Inserat,
    Vermietertyp,
)
from wohnungsagent.services import geo as geodienst

STICHTAG = date(2027, 1, 1)


@pytest.fixture
def profil():
    return lade_profil("config/suchprofil.yml")


def baue_inserat(**abweichungen) -> Inserat:
    """Ein Inserat, das alle Kriterien erfüllt. Abweichungen per Schlüsselwort.

    Dient als Referenz: jeder Test verändert genau das Feld, um das es geht,
    und alles andere bleibt garantiert im grünen Bereich.
    """
    stadtteil = abweichungen.pop("stadtteil", "Nordend-West")
    ort = geodienst.lokalisiere(stadtteil=stadtteil)

    standard = dict(
        quelle="testportal",
        externe_id="1",
        url="https://example.invalid/1",
        titel="Helle 3-Zimmer-Altbauwohnung im Nordend",
        kaltmiete=1200.0,
        nebenkosten=300.0,
        warmmiete=1500.0,
        zimmer=3.0,
        flaeche=85.0,
        stadtteil=stadtteil,
        plz="60322",
        geo=ort,
        distanz_km=geodienst.distanz_zu(ort, 50.132951, 8.680720),
        einzug_ab=date(2027, 2, 1),
        einzug_status=Einzugsstatus.PASST,
        einzug_rohtext="frei ab 01.02.2027",
        ausstattung=Ausstattung(
            balkon=True, keller=True, einbaukueche=True, wg_geeignet=True, oepnv_erwaehnt=True
        ),
        vermietertyp=Vermietertyp.PRIVAT,
        beschreibung="x" * 400 + " 3 Zimmer 85 m² Balkon Keller " + " ".join(
            f"wort{i}" for i in range(60)
        ),
        bilder=[f"bild{i}.jpg" for i in range(6)],
    )
    standard.update(abweichungen)
    return Inserat(**standard)


@pytest.fixture
def inserat():
    return baue_inserat()
