"""Tests für Entfernungsberechnung und Stadtteilauflösung."""
from __future__ import annotations

import pytest

from wohnungsagent.services import geo

# Frankfurt School, Adickesallee 32-34
FS_LAT, FS_LON = 50.132951, 8.680720


def test_haversine_gegen_bekannte_strecke():
    """Frankfurt Hauptbahnhof -> Frankfurt School, rund 3,2 km Luftlinie."""
    hbf = (50.1069, 8.6638)
    strecke = geo.haversine_km(hbf[0], hbf[1], FS_LAT, FS_LON)
    assert 2.8 < strecke < 3.6, strecke


def test_haversine_nullstrecke():
    assert geo.haversine_km(FS_LAT, FS_LON, FS_LAT, FS_LON) == 0.0


def test_haversine_ist_symmetrisch():
    a = geo.haversine_km(50.10, 8.60, 50.20, 8.70)
    b = geo.haversine_km(50.20, 8.70, 50.10, 8.60)
    assert a == b


@pytest.mark.parametrize(
    "eingabe, erwartet",
    [
        ("Nordend-West", "nordend-west"),
        ("nordend west", "nordend-west"),
        ("Frankfurt am Main - Nordend-West", "nordend-west"),
        ("60322 Frankfurt am Main Nordend", "nordend-west"),
        ("Westend-Nord", "westend-nord"),
        ("Westend", "westend-nord"),
        ("FFM Bornheim", "bornheim"),
        ("Frankfurt (Main) Sachsenhausen", "sachsenhausen-nord"),
        ("Höchst", "hoechst"),
        ("Innenstadt III", "nordend-west"),
        ("Schöne Wohnung im Nordend-Ost", "nordend-ost"),
        ("Berlin Mitte", None),
        ("", None),
        (None, None),
    ],
)
def test_stadtteil_normalisierung(eingabe, erwartet):
    assert geo.normalisiere_stadtteil(eingabe) == erwartet


def test_anzeigename():
    assert geo.anzeigename("nordend-west") == "Nordend-West"
    assert geo.anzeigename("westend-sued") == "Westend-Süd"
    assert geo.anzeigename(None) is None


def test_lokalisierung_ueber_stadtteil():
    ort = geo.lokalisiere(stadtteil="Nordend-West")
    assert ort.quelle == "stadtteil"
    assert ort.genauigkeit_m == 700
    assert geo.distanz_zu(ort, FS_LAT, FS_LON) < 1.0


def test_lokalisierung_ueber_plz_wenn_stadtteil_fehlt():
    ort = geo.lokalisiere(postleitzahl="60316")
    assert ort.quelle == "plz"
    assert ort.lat is not None


def test_lokalisierung_ohne_daten():
    ort = geo.lokalisiere()
    assert ort.lat is None
    assert geo.distanz_zu(ort, FS_LAT, FS_LON) is None


def test_geocoder_schlaegt_stadtteil():
    """Eine echte Adresse hat Vorrang vor dem Zentroid."""
    ort = geo.lokalisiere(
        adresse="Eysseneckstraße 1, Frankfurt",
        stadtteil="Bornheim",
        geocoder=lambda _: (50.1300, 8.6800),
    )
    assert ort.quelle == "adresse"
    assert ort.lat == 50.1300


def test_geocoder_faellt_auf_stadtteil_zurueck():
    ort = geo.lokalisiere(adresse="Unauffindbar 1", stadtteil="Bornheim", geocoder=lambda _: None)
    assert ort.quelle == "stadtteil"


def test_wunschlagen_liegen_im_radius():
    """Nordend und Westend-Nord müssen die 3-km-Grenze einhalten,
    sonst wäre das Suchprofil in sich widersprüchlich."""
    for stadtteil in ("Nordend-West", "Nordend-Ost", "Westend-Nord"):
        ort = geo.lokalisiere(stadtteil=stadtteil)
        strecke = geo.distanz_zu(ort, FS_LAT, FS_LON)
        assert strecke is not None and strecke <= 3.0, f"{stadtteil}: {strecke} km"


def test_ferne_stadtteile_liegen_ausserhalb():
    for stadtteil in ("Höchst", "Zeilsheim", "Bergen-Enkheim", "Schwanheim"):
        ort = geo.lokalisiere(stadtteil=stadtteil)
        assert geo.distanz_zu(ort, FS_LAT, FS_LON) > 3.0, stadtteil
