"""Tests für die Parser – vor allem für das wichtigste Kriterium, den Einzug."""
from __future__ import annotations

from datetime import date

import pytest

from wohnungsagent.models.domain import Einzugsstatus, Vermietertyp
from wohnungsagent.services import parsing

STICHTAG = date(2027, 1, 1)
SOFORT_SIGNALE = ["ab sofort", "sofort frei", "kurzfristig"]


# --------------------------------------------------------------- Einzugsdatum

@pytest.mark.parametrize(
    "text, erwartet",
    [
        ("Frei ab 01.03.2027", date(2027, 3, 1)),
        ("frei ab dem 15.01.2027", date(2027, 1, 15)),
        ("Verfügbar ab 1. Februar 2027", date(2027, 2, 1)),
        ("Bezugsfrei ab April 2027", date(2027, 4, 1)),
        ("Einzug ab 04/2027 möglich", date(2027, 4, 1)),
        ("Bezug Q2 2027", date(2027, 4, 1)),
        ("Mietbeginn 2027-05-01", date(2027, 5, 1)),
        ("Übergabe zum 01.09.2026", date(2026, 9, 1)),
        ("Verfügbarkeit: März 2027", date(2027, 3, 1)),
        ("Die Wohnung ist frei ab 1.1.2027.", date(2027, 1, 1)),
        ("Einzug 2027", date(2027, 1, 1)),
        ("zum 01.12.2026 zu vermieten", date(2026, 12, 1)),
    ],
)
def test_einzugsdatum_wird_erkannt(text, erwartet):
    gefunden, rohtext = parsing.einzugsdatum(text)
    assert gefunden == erwartet, f"{text!r} -> {gefunden}"
    assert rohtext


@pytest.mark.parametrize(
    "text",
    [
        "Baujahr 1968, letzte Sanierung 2019",
        "Schöne 3-Zimmer-Wohnung mit 85 m² Wohnfläche",
        "Die Immobilie wurde 2021 komplett modernisiert.",
        "",
        None,
    ],
)
def test_kein_falsches_datum_ohne_ankerwort(text):
    """Baujahr und Sanierungsjahr dürfen nicht als Einzugstermin durchgehen."""
    gefunden, _ = parsing.einzugsdatum(text)
    assert gefunden is None


def test_sofort_schlaegt_datum():
    """"Ab sofort" gewinnt gegen jede Jahreszahl im Text."""
    text = "Ab sofort frei. Das Haus wurde 2027 in die Planung aufgenommen."
    gefunden, rohtext = parsing.einzugsdatum(text)
    assert gefunden is None
    assert "sofort" in rohtext.lower()


def test_nach_vereinbarung_bleibt_unbekannt():
    gefunden, rohtext = parsing.einzugsdatum("Bezug nach Vereinbarung")
    assert gefunden is None
    assert rohtext == "nach Vereinbarung"


# --------------------------------------------------------------- Einzugsstatus

@pytest.mark.parametrize(
    "text, status",
    [
        ("Frei ab 01.03.2027", Einzugsstatus.PASST),
        ("Frei ab 01.01.2027", Einzugsstatus.PASST),
        ("Bezugsfrei ab Januar 2027", Einzugsstatus.PASST),
        ("Frei ab 31.12.2026", Einzugsstatus.ZU_FRUEH),
        ("Ab sofort beziehbar", Einzugsstatus.ZU_FRUEH),
        ("Die Wohnung ist ab sofort frei", Einzugsstatus.ZU_FRUEH),
        ("Kurzfristig verfügbar", Einzugsstatus.ZU_FRUEH),
        ("Einzug 2026", Einzugsstatus.ZU_FRUEH),
        ("Bezug nach Vereinbarung", Einzugsstatus.UNBEKANNT),
        ("Helle Altbauwohnung im Nordend", Einzugsstatus.UNBEKANNT),
    ],
)
def test_einzugsstatus(text, status):
    ergebnis, _, _ = parsing.einzugsstatus(text, STICHTAG, SOFORT_SIGNALE)
    assert ergebnis is status, f"{text!r} -> {ergebnis}"


def test_stichtag_ist_inklusiv():
    status, datum, _ = parsing.einzugsstatus("frei ab 01.01.2027", STICHTAG, [])
    assert status is Einzugsstatus.PASST
    assert datum == STICHTAG


def test_ungueltiges_datum_stuerzt_nicht_ab():
    gefunden, _ = parsing.einzugsdatum("frei ab 31.02.2027")
    assert gefunden is None


# ------------------------------------------------------------------- Beträge

@pytest.mark.parametrize(
    "text, erwartet",
    [
        ("1.250,50 €", 1250.5),
        ("1250 EUR", 1250.0),
        ("980,- €", 980.0),
        ("Warmmiete: 1.680 €", 1680.0),
        ("auf Anfrage", None),
        ("", None),
        (None, None),
    ],
)
def test_euro(text, erwartet):
    assert parsing.euro(text) == erwartet


@pytest.mark.parametrize(
    "text, erwartet",
    [("2,5 Zi.", 2.5), ("67,80 m²", 67.8), ("3 Zimmer", 3.0), ("keine Angabe", None)],
)
def test_dezimal(text, erwartet):
    assert parsing.dezimal(text) == erwartet


def test_plz_nur_frankfurt():
    assert parsing.plz("60316 Frankfurt am Main") == "60316"
    assert parsing.plz("10115 Berlin") is None


# ---------------------------------------------------------------- Ausstattung

def test_ausstattung_dreiwertig():
    """True, False und None müssen unterscheidbar bleiben."""
    a = parsing.ausstattung("Große Wohnung mit Balkon und Kellerabteil, ohne Einbauküche")
    assert a.balkon is True
    assert a.keller is True
    assert a.einbaukueche is False
    assert a.aufzug is None


def test_verneinung_schlaegt_erwaehnung():
    a = parsing.ausstattung("Kein Balkon vorhanden, dafür ein großer Garten")
    assert a.balkon is False


def test_wg_erkennung():
    assert parsing.ausstattung("Ideal für eine Zweier-WG").wg_geeignet is True
    assert parsing.ausstattung("Keine WG, bitte nur Paare").wg_geeignet is False
    assert parsing.ausstattung("Schöne Wohnung").wg_geeignet is None


def test_oepnv():
    assert parsing.ausstattung("2 Minuten zur U-Bahn Holzhausenstraße").oepnv_erwaehnt is True


# --------------------------------------------------------------- Vermietertyp

def test_vermietertyp():
    assert parsing.vermietertyp("Musterhaus Immobilien GmbH") is Vermietertyp.GEWERBLICH
    assert parsing.vermietertyp("Privatperson", "von privat, provisionsfrei") is Vermietertyp.PRIVAT
    assert parsing.vermietertyp(None, "Schöne Wohnung") is Vermietertyp.UNBEKANNT


def test_provisionsfrei_allein_macht_keinen_privatvermieter():
    """Makler werben ebenfalls mit "provisionsfrei"."""
    typ = parsing.vermietertyp("Wohnbau Verwaltung GmbH", "provisionsfrei für den Mieter")
    assert typ is Vermietertyp.GEWERBLICH


# ------------------------------------------------------------------ Qualität

def test_beschreibung_bewertung():
    duenn = "Schöne Wohnung. Bei Interesse melden."
    assert parsing.beschreibung_ist_gut(duenn) is False

    reich = (
        "Die Wohnung liegt im 3. Obergeschoss eines gepflegten Altbaus von 1912 in der "
        "Eysseneckstraße. Sie verfügt über 3 Zimmer auf 84 m², einen Südbalkon mit Blick "
        "über den Innenhof sowie ein separates Kellerabteil von etwa 6 m². Die Einbauküche "
        "aus dem Jahr 2021 wird übernommen. Böden sind durchgehend als Eichenparkett "
        "ausgeführt, die Fenster wurden 2019 erneuert. Zur U-Bahn Holzhausenstraße sind es "
        "rund 400 Meter, zur Frankfurt School etwa 15 Gehminuten. Heizung über Fernwärme, "
        "Energieausweis liegt vor. Haustiere nach Absprache."
    )
    assert parsing.beschreibung_ist_gut(reich) is True


def test_bilder_bewertung():
    assert parsing.bilder_sind_gut(["a.jpg", "b.jpg", "c.jpg", "d.jpg"]) is True
    assert parsing.bilder_sind_gut(["a.jpg", "placeholder.png", "no-image.svg"]) is False


def test_stichwort_achtet_auf_wortgrenzen():
    assert parsing.enthaelt_stichwort("wg-zimmer zur zwischenmiete", ["Zwischenmiete"]) == "Zwischenmiete"
    assert parsing.enthaelt_stichwort("wohnung von herrn wbschmidt", ["WBS"]) is None
    assert parsing.enthaelt_stichwort("wbs erforderlich", ["WBS erforderlich"]) == "WBS erforderlich"


@pytest.mark.parametrize(
    "text, feld",
    [
        ("Wohnung mit Südbalkon", "balkon"),
        ("Große Dachterrasse nach Westen", "balkon"),
        ("Eigenes Kellerabteil im Untergeschoss", "keller"),
        ("Personenaufzug im Haus", "aufzug"),
        ("Charmante Altbauwohnung", "altbau"),
        ("Gründerzeithaus von 1904", "altbau"),
    ],
)
def test_zusammensetzungen_werden_erkannt(text, feld):
    """Deutsche Komposita sind der Normalfall in Inseraten, nicht die Ausnahme."""
    assert getattr(parsing.ausstattung(text), feld) is True
