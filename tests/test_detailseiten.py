"""Tests für die Detailseiten-Auswertung.

Der Anlass: Ein WG-Gesucht-Inserat mit "frei ab 19.10.2026" und
"frei bis 31.01.2027" landete als Treffer in der Liste. In der Trefferliste
steht keines der beiden Daten – erst die Detailseite verrät, dass es sich um
eine Zwischenmiete handelt, die elf Tage nach dem gewünschten Einzugstermin
endet.
"""
from __future__ import annotations

from datetime import date

import pytest

from wohnungsagent.config.profil import lade_profil
from wohnungsagent.models.domain import Inserat
from wohnungsagent.ranking import regeln
from wohnungsagent.services.detailseiten import lies_detail

# Nachbau der echten Seite: Anzeigennummer 13847559, Westend-Nord.
WG_DETAIL = """
<html><head>
  <meta property="og:image" content="https://img.wg-gesucht.de/media/up/2026/31/titel.jpg">
</head><body>
  <img src="https://www.wg-gesucht.de/img/logo.svg" alt="Logo">
  <div class="gallery">
    <img src="https://img.wg-gesucht.de/media/up/2026/31/foto1.jpg">
    <img data-src="https://img.wg-gesucht.de/media/up/2026/31/foto2.jpg">
    <img data-src="https://img.wg-gesucht.de/media/up/2026/31/foto3.jpg">
  </div>
  <h1>Geräumige Dachgeschosswohnung in Bestlage</h1>
  <div class="key-facts">
    <span>Größe: 90m²</span><span>Gesamtmiete: 1600€</span><span>Zimmer: 3</span>
  </div>
  <section><h3>Kosten</h3>
    <p>Miete: 1600€</p>
    <p>Nebenkosten: n.a.</p>
    <p>Sonstige Kosten: n.a.</p>
    <p>Kaution: n.a.</p>
  </section>
  <section><h3>Adresse</h3>
    <p>Eschersheimer Landstraße<br>60322 Frankfurt am Main Westend-Nord</p>
  </section>
  <section><h3>Verfügbarkeit</h3>
    <p>frei ab: 19.10.2026</p>
    <p>frei bis: 31.01.2027</p>
    <p>Online: 4 Stunden</p>
  </section>
</body></html>
"""


def basis_inserat(**abweichungen) -> Inserat:
    standard = dict(
        quelle="wg_gesucht",
        externe_id="13847559",
        url="https://www.wg-gesucht.de/wohnungen-in-Frankfurt-am-Main.13847559.html",
        titel="Geräumige Dachgeschosswohnung in Bestlage",
        warmmiete=1600.0,
        flaeche=90.0,
        stadtteil="Westend-Nord",
        bilder=["https://img.wg-gesucht.de/media/up/2026/31/titel.jpg"],
    )
    standard.update(abweichungen)
    return Inserat(**standard)


@pytest.fixture
def profil():
    return lade_profil("config/suchprofil.yml")


# ----------------------------------------------------- Der eigentliche Fall

def test_zwischenmiete_wird_erkannt():
    inserat = lies_detail(WG_DETAIL, basis_inserat())
    assert inserat.einzug_ab == date(2026, 10, 19)
    assert inserat.frei_bis == date(2027, 1, 31)
    assert inserat.befristet is True
    assert inserat.detail_gelesen is True


def test_zwischenmiete_fliegt_raus(profil):
    inserat = lies_detail(WG_DETAIL, basis_inserat())
    grund = regeln.pruefe(inserat, profil)
    assert grund is not None
    assert "befristet bis 31.01.2027" in grund


def test_ohne_detailseite_bliebe_es_unentdeckt(profil):
    """Belegt, warum der zusätzliche Abruf nötig ist: aus der Trefferliste
    allein ist das Inserat nicht als Zwischenmiete erkennbar."""
    aus_liste = basis_inserat()
    assert aus_liste.frei_bis is None
    assert regeln.regel_befristung(aus_liste, profil) is None


def test_unbefristetes_inserat_bleibt_drin(profil):
    html = WG_DETAIL.replace("<p>frei bis: 31.01.2027</p>", "")
    inserat = lies_detail(html, basis_inserat())
    assert inserat.frei_bis is None
    assert inserat.befristet is False
    assert regeln.regel_befristung(inserat, profil) is None


def test_einzug_ab_2027_wird_uebernommen(profil):
    html = WG_DETAIL.replace("frei ab: 19.10.2026", "frei ab: 01.02.2027") \
                    .replace("<p>frei bis: 31.01.2027</p>", "")
    inserat = lies_detail(html, basis_inserat())
    assert inserat.einzug_ab == date(2027, 2, 1)


# ------------------------------------------------------------ Weitere Felder

def test_bilder_werden_vollstaendig_uebernommen():
    """In der Trefferliste steht ein Bild, auf der Detailseite alle."""
    inserat = lies_detail(WG_DETAIL, basis_inserat())
    assert len(inserat.bilder) >= 4
    assert inserat.bilder[0].endswith("titel.jpg")          # og:image zuerst
    assert not any("logo.svg" in b for b in inserat.bilder)  # kein Beiwerk


def test_na_wird_nicht_als_betrag_gelesen():
    """"Nebenkosten: n.a." darf nicht zu einem Betrag werden – sonst wandert
    eine Zahl aus der nächsten Zeile ins Feld."""
    inserat = lies_detail(WG_DETAIL, basis_inserat())
    assert inserat.nebenkosten is None
    assert inserat.kaution is None


def test_adresse_wird_ergaenzt():
    inserat = lies_detail(WG_DETAIL, basis_inserat())
    assert inserat.adresse and "Eschersheimer Landstraße" in inserat.adresse
    assert inserat.plz == "60322"


def test_fehlende_eckdaten_werden_nachgetragen():
    inserat = lies_detail(WG_DETAIL, basis_inserat(flaeche=None, zimmer=None, warmmiete=None))
    assert inserat.flaeche == 90.0
    assert inserat.zimmer == 3.0
    assert inserat.warmmiete == 1600.0


def test_stichwort_befristet_ohne_datum():
    html = WG_DETAIL.replace("frei bis: 31.01.2027", "Das Mietverhältnis ist befristet.")
    inserat = lies_detail(html, basis_inserat())
    assert inserat.befristet is True


def test_kaputte_seite_stuerzt_nicht_ab():
    inserat = lies_detail("<html><body>Fehler 500</body></html>", basis_inserat())
    assert inserat.detail_gelesen is True
    assert inserat.warmmiete == 1600.0          # Werte aus der Liste bleiben


def test_kleinanzeigen_detailseite():
    """Derselbe Parser muss auch für Kleinanzeigen funktionieren – deshalb
    wird über Beschriftungen gelesen, nicht über CSS-Klassen."""
    html = """
    <html><body>
      <h1>Helle 3-Zimmer-Altbauwohnung im Nordend</h1>
      <ul>
        <li>Kaltmiete: 1.350 €</li>
        <li>Nebenkosten: 280 €</li>
        <li>Kaution: 4.050 €</li>
        <li>Wohnfläche: 84 m²</li>
        <li>Zimmer: 3</li>
        <li>Verfügbar ab: 01.03.2027</li>
      </ul>
    </body></html>
    """
    inserat = lies_detail(html, Inserat(
        quelle="kleinanzeigen", externe_id="1", url="https://www.kleinanzeigen.de/x",
        titel="Helle 3-Zimmer-Altbauwohnung im Nordend"))
    assert inserat.kaltmiete == 1350.0
    assert inserat.nebenkosten == 280.0
    assert inserat.kaution == 4050.0
    assert inserat.warmmiete == 1630.0          # aus Kalt + NK errechnet
    assert inserat.einzug_ab == date(2027, 3, 1)
    assert inserat.frei_bis is None
