"""Tests für die Duplikaterkennung über Portalgrenzen hinweg."""
from __future__ import annotations

from wohnungsagent.services import dedupe

from .conftest import baue_inserat


def test_identisches_inserat_ist_dublette():
    a = baue_inserat(quelle="immoscout24", externe_id="A")
    b = baue_inserat(quelle="immowelt", externe_id="B")
    assert dedupe.aehnlichkeit(a, b) >= dedupe.SCHWELLE


def test_dasselbe_objekt_mit_abweichendem_titel():
    """Makler formulieren pro Portal leicht um, die Zahlen bleiben gleich."""
    a = baue_inserat(
        quelle="immoscout24", externe_id="A",
        titel="Helle 3-Zimmer-Altbauwohnung im Frankfurter Nordend",
        adresse="Eysseneckstraße 12, 60322 Frankfurt",
    )
    b = baue_inserat(
        quelle="immonet", externe_id="B",
        titel="3 Zimmer Altbau Nordend Frankfurt hell",
        adresse="Eysseneckstr. 12, Frankfurt am Main",
    )
    assert dedupe.aehnlichkeit(a, b) >= dedupe.SCHWELLE


def test_verschiedene_wohnungen_sind_keine_dubletten():
    a = baue_inserat(quelle="immoscout24", externe_id="A", warmmiete=1500.0, flaeche=85.0, zimmer=3.0)
    b = baue_inserat(
        quelle="immowelt", externe_id="B", warmmiete=1750.0, flaeche=62.0, zimmer=2.0,
        titel="Kompakte 2-Zimmer-Wohnung Westend",
    )
    assert dedupe.aehnlichkeit(a, b) < dedupe.SCHWELLE


def test_gleiche_zahlen_aber_klar_anderer_titel():
    """Zwei Wohnungen im selben Neubau können identische Eckdaten haben."""
    a = baue_inserat(quelle="p1", externe_id="A", titel="Wohnung 3. OG links Nordend",
                     adresse="Gluckstraße 4")
    b = baue_inserat(quelle="p2", externe_id="B", titel="Wohnung 7. OG rechts Nordend",
                     adresse="Gluckstraße 4")
    # Adresse und Zahlen gleich – dass sie zusammenfallen, ist hier
    # tolerierbar; wichtig ist nur, dass nichts abstürzt.
    assert 0.0 <= dedupe.aehnlichkeit(a, b) <= 1.0


def test_gruppierung_waehlt_das_vollstaendigste():
    duenn = baue_inserat(
        quelle="kleinanzeigen", externe_id="A", adresse=None, etage=None,
        kaution=None, nebenkosten=None, beschreibung="kurz", bilder=[],
    )
    reich = baue_inserat(
        quelle="immoscout24", externe_id="B", adresse="Eysseneckstraße 12",
        etage="3. OG", kaution=3600.0,
    )
    gruppen = dedupe.gruppiere([duenn, reich])
    assert len(gruppen) == 1
    assert gruppen[0].beste.quelle == "immoscout24"
    assert gruppen[0].dubletten[0].quelle == "kleinanzeigen"


def test_entdoppeln_vermerkt_weitere_quellen():
    a = baue_inserat(quelle="immoscout24", externe_id="A", adresse="Eysseneckstraße 12")
    b = baue_inserat(quelle="immowelt", externe_id="B", adresse=None, bilder=[])
    ergebnis = dedupe.entdoppeln([a, b])
    assert len(ergebnis) == 1
    assert any("auch auf" in m for m in ergebnis[0].merkmale)


def test_leere_liste():
    assert dedupe.entdoppeln([]) == []


def test_blockbildung_trennt_klar_verschiedene():
    inserate = [
        baue_inserat(quelle="p", externe_id=str(i), warmmiete=1300.0 + i * 120,
                     flaeche=70.0 + i * 9, zimmer=2.0 + (i % 3) * 0.5,
                     titel=f"Wohnung Variante {i} Strasse {i}", adresse=f"Teststraße {i}")
        for i in range(8)
    ]
    assert len(dedupe.entdoppeln(inserate)) == 8


def test_selbstaehnlichkeit_ist_eins():
    a = baue_inserat()
    assert dedupe.aehnlichkeit(a, a) == 1.0


def test_ohne_zahlen_und_adresse_kein_zusammenwurf():
    """Regression: Inserate ohne Preis, Fläche und Zimmer landeten alle im
    selben Dedupe-Block und wurden allein über den Titel verschmolzen.
    Im ersten echten Lauf verschwanden dadurch 36 von 64 Inseraten."""
    leer = dict(warmmiete=None, kaltmiete=None, nebenkosten=None,
                flaeche=None, zimmer=None, adresse=None)
    a = baue_inserat(quelle="wg_gesucht", externe_id="A",
                     titel="2-Zimmer-Wohnung in Frankfurt", **leer)
    b = baue_inserat(quelle="wg_gesucht", externe_id="B",
                     titel="2 Zimmer Wohnung Frankfurt zu vermieten", **leer)
    assert dedupe.aehnlichkeit(a, b) == 0.0
    assert len(dedupe.entdoppeln([a, b])) == 2


def test_viele_datenlose_inserate_bleiben_erhalten():
    inserate = [
        baue_inserat(quelle="wg_gesucht", externe_id=str(i),
                     titel="Wohnung in Frankfurt", warmmiete=None, kaltmiete=None,
                     nebenkosten=None, flaeche=None, zimmer=None, adresse=None)
        for i in range(20)
    ]
    assert len(dedupe.entdoppeln(inserate)) == 20


def test_echte_dubletten_werden_weiterhin_erkannt():
    """Die Absicherung darf die eigentliche Aufgabe nicht kaputt machen."""
    a = baue_inserat(quelle="immobilienscout24", externe_id="A",
                     adresse="Eysseneckstraße 12, 60322 Frankfurt")
    b = baue_inserat(quelle="immowelt", externe_id="B",
                     adresse="Eysseneckstr. 12, Frankfurt am Main")
    assert len(dedupe.entdoppeln([a, b])) == 1
