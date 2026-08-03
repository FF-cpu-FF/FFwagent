"""Tests der Quellen-Parser gegen gespeichertes Markup.

Bisher waren die Scraper der am schwächsten abgedeckte Teil (rund 25 %), und
genau dort saßen die Fehler: falscher Ortscode, Zimmerzahl und Fläche nicht
erkannt, leere Immowelt-Hüllen. Diese Tests halten die Struktur fest, wie sie
im echten Betrieb angekommen ist.

Wenn ein Portal sein Markup ändert, schlagen diese Tests **nicht** fehl – sie
prüfen den Parser gegen die gespeicherte Fassung. Das Werkzeug für den
Ernstfall ist `wohnungsagent diagnose <quelle>`, das die aktuelle Seite holt
und ablegt.
"""
from __future__ import annotations

import pytest

from wohnungsagent.config.profil import lade_profil
from wohnungsagent.scrapers.base import Suchseite
from wohnungsagent.scrapers.registry import baue


@pytest.fixture
def profil():
    return lade_profil("config/suchprofil.yml")


# --------------------------------------------------------------- Kleinanzeigen

KLEINANZEIGEN_HTML = """
<html><body>
<article class="aditem" data-adid="3141592653"
         data-href="/s-anzeige/helle-3-zimmer-altbauwohnung-nordend/3141592653-203-4292">
  <div class="aditem-image"><div class="imagebox">
    <img src="https://img.kleinanzeigen.de/api/v1/prod-ads/images/aa/bb.JPG">
  </div></div>
  <div class="aditem-main">
    <div class="aditem-main--top">
      <div class="aditem-main--top--left">60322 Frankfurt am Main - Nordend-West</div>
      <div class="aditem-main--top--right">Heute, 09:14</div>
    </div>
    <div class="aditem-main--middle">
      <h2 class="text-module-begin">
        <a class="ellipsis" href="/s-anzeige/helle-3-zimmer/3141592653-203-4292">
          Lichtdurchflutete Balkonwohnung mit Gartenmitnutzung
        </a>
      </h2>
      <p class="aditem-main--middle--description">
        Provisionsfrei, Altbau, Einbaukueche vorhanden. Frei ab 01.03.2027.
      </p>
      <p class="aditem-main--middle--price-shipping--price">1.350 € VB</p>
    </div>
    <div class="aditem-main--bottom">
      <p class="text-module-end">
        <span class="simpletag tag-small">84 m²</span>
        <span class="simpletag tag-small">3 Zi.</span>
        <span class="simpletag tag-small">Etagenwohnung</span>
      </p>
    </div>
  </div>
</article>

<article class="aditem" data-adid="2718281828"
         data-href="/s-anzeige/2-zimmer-bornheim/2718281828-203-4292">
  <div class="aditem-main">
    <div class="aditem-main--top">
      <div class="aditem-main--top--left">60385 Frankfurt am Main - Bornheim</div>
    </div>
    <div class="aditem-main--middle">
      <h2><a class="ellipsis" href="/s-anzeige/2-zimmer/2718281828-203-4292">
        Gemuetliche 2,5-Zimmer-Wohnung
      </a></h2>
      <p class="aditem-main--middle--price-shipping--price">980 €</p>
    </div>
    <div class="aditem-main--bottom">
      <span class="simpletag tag-small">62,5 m²</span>
      <span class="simpletag tag-small">2,5 Zimmer</span>
    </div>
  </div>
</article>
</body></html>
"""


@pytest.fixture
def kleinanzeigen(profil):
    return baue("kleinanzeigen", dict(profil.quellen["kleinanzeigen"]), profil)


def test_kleinanzeigen_liest_alle_felder(kleinanzeigen):
    treffer = kleinanzeigen.parse_seite(KLEINANZEIGEN_HTML, Suchseite("x"))
    assert len(treffer) == 2

    erstes = treffer[0]
    assert erstes.externe_id == "3141592653"
    assert erstes.titel.startswith("Lichtdurchflutete Balkonwohnung")
    assert erstes.kaltmiete == 1350.0
    assert erstes.zimmer == 3.0          # Regression: kam als None durch
    assert erstes.flaeche == 84.0        # Regression: kam als None durch
    assert erstes.url.startswith("https://www.kleinanzeigen.de/")
    assert erstes.bilder and erstes.bilder[0].endswith(".JPG")
    assert erstes.ist_brauchbar


def test_kleinanzeigen_versteht_dezimalkomma(kleinanzeigen):
    zweites = kleinanzeigen.parse_seite(KLEINANZEIGEN_HTML, Suchseite("x"))[1]
    assert zweites.zimmer == 2.5
    assert zweites.flaeche == 62.5
    assert zweites.kaltmiete == 980.0


def test_kleinanzeigen_ort_wird_uebernommen(kleinanzeigen):
    erstes = kleinanzeigen.parse_seite(KLEINANZEIGEN_HTML, Suchseite("x"))[0]
    # Der Rohtext reicht – die Zuordnung zum Stadtteil macht services/geo.
    from wohnungsagent.services.geo import normalisiere_stadtteil

    quelle = erstes.stadtteil or erstes.adresse
    assert normalisiere_stadtteil(quelle) == "nordend-west"


def test_kleinanzeigen_ohne_treffer_stuerzt_nicht_ab(kleinanzeigen):
    assert kleinanzeigen.parse_seite("<html><body>nichts</body></html>", Suchseite("x")) == []


def test_kleinanzeigen_url_enthaelt_richtigen_ortscode(kleinanzeigen):
    """Regression: l6018 war geraten, korrekt ist l4292 mit Slug
    frankfurt-am-main. Falsche Kombination liefert HTTP 200 und null Treffer."""
    urls = [s.url for s in kleinanzeigen.suchseiten()]
    assert urls, "keine Suchseiten erzeugt"
    for url in urls:
        assert "l4292" in url
        assert "frankfurt-am-main" in url
        assert "l6018" not in url


# ------------------------------------------------------------------ WG-Gesucht

WG_HTML = """
<html><body>
<div class="wgg_card offer_list_item">
  <div class="card_body">
    <h3 class="truncate_title">
      <a href="/wohnungen-in-Frankfurt-am-Main-Nordend.13847559.html">
        Geraeumige Dachgeschosswohnung in Bestlage
      </a>
    </h3>
    <div class="col-xs-11">
      <span>3 Zimmer | Frankfurt am Main Nordend-West | Eysseneckstrasse</span>
    </div>
    <div class="detail-size-price-wrapper">
      <b>1.480 €</b> 84 m²
    </div>
  </div>
</div>
</body></html>
"""


def test_wg_gesucht_liest_kernfelder(profil):
    scraper = baue("wg_gesucht", dict(profil.quellen["wg_gesucht"]), profil)
    treffer = scraper.parse_seite(WG_HTML, Suchseite("x"))
    assert len(treffer) == 1

    inserat = treffer[0]
    assert inserat.externe_id == "13847559"
    assert "Dachgeschosswohnung" in inserat.titel
    assert inserat.warmmiete == 1480.0
    assert inserat.flaeche == 84.0
    assert inserat.zimmer == 3.0
    assert inserat.ist_brauchbar


# -------------------------------------------------------------------- Immowelt

def test_immowelt_leere_karten_gelten_als_unbrauchbar(profil):
    """Regression: Immowelt lieferte 32 Karten, aus denen nur die URL
    lesbar war. Ohne diese Schranke landeten sie als Treffer im Dashboard."""
    html = """
    <html><body>
      <a href="/expose/8a96d7a8-27cf-4b37-852a-9ab57019527e">&nbsp;</a>
      <a href="/expose/428141be-3d37-4e51-8d04-435c378c9306">&nbsp;</a>
    </body></html>
    """
    scraper = baue("immowelt", dict(profil.quellen["immowelt"]), profil)
    treffer = scraper.parse_seite(html, Suchseite("x"))
    assert all(not i.ist_brauchbar for i in treffer), \
        "leere Exposé-Hüllen dürfen nicht als verwertbar gelten"


WG_HTML_OHNE_PREIS_TAG = """
<html><body>
<div class="wgg_card offer_list_item">
  <div class="card_body">
    <h3 class="truncate_title">
      <a href="/wohnungen-in-Frankfurt-am-Main-Westend.13847559.html">
        Geraeumige Dachgeschosswohnung in Bestlage
      </a>
    </h3>
    <div class="col-xs-11"><span>Frankfurt am Main Westend-Nord</span></div>
    <div class="card_footer">
      <span>Groesse: 90m²</span>
      <span>Gesamtmiete: 1.600 €</span>
      <span>Zimmer: 3</span>
    </div>
  </div>
</div>
</body></html>
"""


def test_wg_gesucht_liest_preis_auch_ohne_bekannten_tag(profil):
    """Regression: die Gesamtmiete stand nicht in <b>, sondern im Fusstext.
    Dadurch blieb warmmiete leer und im Dashboard stand ueberall '– €'."""
    scraper = baue("wg_gesucht", dict(profil.quellen["wg_gesucht"]), profil)
    inserat = scraper.parse_seite(WG_HTML_OHNE_PREIS_TAG, Suchseite("x"))[0]

    assert inserat.warmmiete == 1600.0
    assert inserat.flaeche == 90.0
    assert inserat.zimmer == 3.0
    assert inserat.ist_brauchbar


def test_wg_gesucht_tausenderpunkt(profil):
    scraper = baue("wg_gesucht", dict(profil.quellen["wg_gesucht"]), profil)
    html = WG_HTML_OHNE_PREIS_TAG.replace("1.600 €", "980 €")
    assert scraper.parse_seite(html, Suchseite("x"))[0].warmmiete == 980.0
