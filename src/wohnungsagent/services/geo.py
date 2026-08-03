"""Geodienst: Entfernung zur Frankfurt School und Stadtteilauflösung.

Der Agent geocodiert in drei Stufen, absteigend nach Genauigkeit:

  1. Vollständige Adresse  -> Nominatim (optional, siehe GEOCODING_AKTIV)
  2. Stadtteilname         -> eingebaute Zentroidtabelle, ±700 m
  3. Nur PLZ               -> PLZ-Tabelle, ±1200 m

Stufe 2 und 3 laufen ohne Netz und ohne Wartezeit. Das genügt für den
3-km-Radius, solange die Ungenauigkeit mitgeführt wird: bei einem Treffer
knapp jenseits der Grenze steht `geo.quelle` auf "stadtteil", und die
Ausschlussregel arbeitet dann mit einem Toleranzaufschlag statt hart zu
verwerfen (siehe ranking/regeln.py).
"""
from __future__ import annotations

import math
import re
import unicodedata

from ..models.domain import Geo

ERDRADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Luftlinie in Kilometern."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return round(2 * ERDRADIUS_KM * math.asin(math.sqrt(a)), 3)


# --------------------------------------------------------------------------
#  Stadtteilzentroide Frankfurt am Main
#  Quelle: Ortsbezirksgrenzen der Stadt Frankfurt, Schwerpunkt gerundet.
#  Genauigkeit rund ±700 m; für den 3-km-Radius ausreichend.
# --------------------------------------------------------------------------
STADTTEIL_ZENTROIDE: dict[str, tuple[float, float]] = {
    "nordend-west": (50.1287, 8.6742),
    "nordend-ost": (50.1301, 8.6968),
    "westend-nord": (50.1258, 8.6640),
    "westend-sued": (50.1174, 8.6591),
    "innenstadt": (50.1132, 8.6800),
    "bahnhofsviertel": (50.1082, 8.6664),
    "altstadt": (50.1106, 8.6820),
    "gallus": (50.1063, 8.6338),
    "bockenheim": (50.1233, 8.6425),
    "dornbusch": (50.1428, 8.6690),
    "eschersheim": (50.1553, 8.6559),
    "ginnheim": (50.1418, 8.6482),
    "praunheim": (50.1516, 8.6160),
    "roedelheim": (50.1268, 8.6135),
    "bornheim": (50.1301, 8.7104),
    "ostend": (50.1147, 8.7050),
    "riederwald": (50.1290, 8.7285),
    "seckbach": (50.1408, 8.7268),
    "sachsenhausen-nord": (50.1023, 8.6851),
    "sachsenhausen-sued": (50.0873, 8.6832),
    "niederrad": (50.0862, 8.6294),
    "schwanheim": (50.0793, 8.5820),
    "hoechst": (50.0983, 8.5459),
    "griesheim": (50.0932, 8.6021),
    "nied": (50.0999, 8.5806),
    "unterliederbach": (50.1074, 8.5306),
    "sindlingen": (50.0754, 8.5169),
    "zeilsheim": (50.0937, 8.5087),
    "sossenheim": (50.1157, 8.5651),
    "hausen": (50.1354, 8.6284),
    "heddernheim": (50.1584, 8.6413),
    "niederursel": (50.1673, 8.6376),
    "kalbach": (50.1855, 8.6555),
    "bonames": (50.1719, 8.6614),
    "berkersheim": (50.1748, 8.6900),
    "preungesheim": (50.1541, 8.6903),
    "eckenheim": (50.1487, 8.6801),
    "fechenheim": (50.1220, 8.7521),
    "bergen-enkheim": (50.1552, 8.7660),
    "harheim": (50.1949, 8.6772),
    "nieder-erlenbach": (50.2117, 8.6890),
    "nieder-eschbach": (50.1930, 8.6537),
    "sindlingen-nord": (50.0790, 8.5210),
    "flughafen": (50.0379, 8.5622),
    "oberrad": (50.0989, 8.7133),
}

# PLZ-Schwerpunkte für die Fälle ohne Stadtteilangabe
PLZ_ZENTROIDE: dict[str, tuple[float, float]] = {
    "60313": (50.1157, 8.6829), "60314": (50.1157, 8.7018), "60316": (50.1240, 8.7005),
    "60318": (50.1235, 8.6862), "60320": (50.1417, 8.6698), "60322": (50.1268, 8.6789),
    "60323": (50.1215, 8.6660), "60325": (50.1176, 8.6588), "60326": (50.1030, 8.6285),
    "60327": (50.1040, 8.6483), "60329": (50.1073, 8.6644), "60385": (50.1301, 8.7104),
    "60386": (50.1220, 8.7521), "60388": (50.1552, 8.7660), "60389": (50.1420, 8.7100),
    "60431": (50.1428, 8.6690), "60433": (50.1541, 8.6903), "60435": (50.1487, 8.6801),
    "60437": (50.1855, 8.6555), "60438": (50.1673, 8.6376), "60439": (50.1584, 8.6413),
    "60486": (50.1233, 8.6425), "60487": (50.1268, 8.6470), "60488": (50.1516, 8.6160),
    "60489": (50.1268, 8.6135), "60528": (50.0862, 8.6294), "60529": (50.0700, 8.6100),
    "60594": (50.1023, 8.6851), "60596": (50.0950, 8.6700), "60598": (50.0873, 8.6832),
    "60599": (50.0989, 8.7133), "65929": (50.1074, 8.5306), "65931": (50.0754, 8.5169),
    "65933": (50.0932, 8.6021), "65934": (50.0999, 8.5806), "65936": (50.1157, 8.5651),
}

# Schreibvarianten der Portale -> kanonischer Schlüssel
STADTTEIL_ALIASE: dict[str, str] = {
    "nordend": "nordend-west",          # unspezifisch – konservativ auf West
    "frankfurt-nordend": "nordend-west",
    "nordend west": "nordend-west",
    "nordend ost": "nordend-ost",
    "westend": "westend-nord",
    "westend nord": "westend-nord",
    "westend sued": "westend-sued",
    "westend süd": "westend-sued",
    "sachsenhausen": "sachsenhausen-nord",
    "alt-sachsenhausen": "sachsenhausen-nord",
    "innenstadt i": "innenstadt",
    "innenstadt ii": "innenstadt",
    "innenstadt iii": "nordend-west",   # amtl. Bezirk, umfasst die Adickesallee
    "city": "innenstadt",
    "zentrum": "innenstadt",
    "roedelheim": "roedelheim",
    "höchst": "hoechst",
    "hoechst": "hoechst",
    "bergen enkheim": "bergen-enkheim",
    "nieder eschbach": "nieder-eschbach",
    "nieder erlenbach": "nieder-erlenbach",
}


def normalisiere_stadtteil(name: str | None) -> str | None:
    """'Frankfurt am Main - Nordend-West' -> 'nordend-west'"""
    if not name:
        return None
    text = unicodedata.normalize("NFKC", name).lower().strip()
    text = re.sub(r"\b(frankfurt(\s+am\s+main)?|ffm|frankfurt/main)\b", " ", text)
    text = re.sub(r"\b6[05]\d{3}\b", " ", text)
    text = text.replace("ß", "ss").replace("ü", "ue").replace("ö", "oe").replace("ä", "ae")
    text = re.sub(r"[^a-z0-9\- ]+", " ", text)
    text = " ".join(text.split()).strip(" -")
    if not text:
        return None
    if text in STADTTEIL_ZENTROIDE:
        return text
    if text in STADTTEIL_ALIASE:
        return STADTTEIL_ALIASE[text]
    mit_strich = text.replace(" ", "-")
    if mit_strich in STADTTEIL_ZENTROIDE:
        return mit_strich
    if mit_strich in STADTTEIL_ALIASE:
        return STADTTEIL_ALIASE[mit_strich]
    # Teilstring-Treffer: "wohnung im nordend-west mit balkon".
    # An Segmentgrenzen gebunden, sonst würde "hausen" in "sachsenhausen"
    # treffen. Längere Schlüssel zuerst, damit "nordend-west" gegen den
    # kürzeren Alias "nordend" gewinnt.
    kandidaten = [(s, s) for s in STADTTEIL_ZENTROIDE]
    kandidaten += [(a.replace(" ", "-"), z) for a, z in STADTTEIL_ALIASE.items()]
    for muster, ziel in sorted(kandidaten, key=lambda p: len(p[0]), reverse=True):
        if re.search(rf"(?:^|[- ]){re.escape(muster)}(?:$|[- ])", mit_strich):
            return ziel
    return None


def anzeigename(schluessel: str | None) -> str | None:
    """'nordend-west' -> 'Nordend-West'"""
    if not schluessel:
        return None
    ersatz = {"sued": "Süd", "hoechst": "Höchst", "roedelheim": "Rödelheim"}
    teile = [ersatz.get(t, t.capitalize()) for t in schluessel.split("-")]
    return "-".join(teile)


def lokalisiere(
    adresse: str | None = None,
    stadtteil: str | None = None,
    postleitzahl: str | None = None,
    geocoder=None,
) -> Geo:
    """Bestimmt Koordinaten in der bestmöglichen verfügbaren Genauigkeit."""
    if adresse and geocoder is not None:
        treffer = geocoder(adresse)
        if treffer:
            lat, lon = treffer
            return Geo(lat=lat, lon=lon, quelle="adresse", genauigkeit_m=80)

    schluessel = normalisiere_stadtteil(stadtteil) or normalisiere_stadtteil(adresse)
    if schluessel and schluessel in STADTTEIL_ZENTROIDE:
        lat, lon = STADTTEIL_ZENTROIDE[schluessel]
        return Geo(lat=lat, lon=lon, quelle="stadtteil", genauigkeit_m=700)

    if postleitzahl and postleitzahl in PLZ_ZENTROIDE:
        lat, lon = PLZ_ZENTROIDE[postleitzahl]
        return Geo(lat=lat, lon=lon, quelle="plz", genauigkeit_m=1200)

    return Geo()


def distanz_zu(geo: Geo, ziel_lat: float, ziel_lon: float) -> float | None:
    if geo.lat is None or geo.lon is None:
        return None
    return haversine_km(geo.lat, geo.lon, ziel_lat, ziel_lon)


def nominatim_geocoder(kontakt: str, cache: dict[str, tuple[float, float]] | None = None):
    """Baut einen Geocoder gegen Nominatim.

    Nominatim erlaubt maximal eine Anfrage pro Sekunde und verlangt einen
    identifizierenden User-Agent. Beides wird hier eingehalten; Ergebnisse
    werden im übergebenen Cache gehalten, damit wiederkehrende Adressen
    keine erneute Anfrage auslösen.
    """
    import time

    import requests

    speicher = cache if cache is not None else {}
    letzter_ruf = [0.0]

    def geocode(adresse: str) -> tuple[float, float] | None:
        if adresse in speicher:
            return speicher[adresse]
        wartezeit = 1.05 - (time.monotonic() - letzter_ruf[0])
        if wartezeit > 0:
            time.sleep(wartezeit)
        letzter_ruf[0] = time.monotonic()
        try:
            antwort = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": adresse, "format": "json", "limit": 1, "countrycodes": "de"},
                headers={"User-Agent": kontakt},
                timeout=15,
            )
            antwort.raise_for_status()
            daten = antwort.json()
        except Exception:
            return None
        if not daten:
            return None
        punkt = (float(daten[0]["lat"]), float(daten[0]["lon"]))
        speicher[adresse] = punkt
        return punkt

    return geocode
