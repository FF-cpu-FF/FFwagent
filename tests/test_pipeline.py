"""Integrationstest: Rohinserat bis fertige Bewertung, ohne Netz und ohne Datenbank.

Prüft den Weg, den ein Inserat tatsächlich nimmt – Anreicherung (Einzug, Geo,
Ausstattung), Duplikatabgleich, Ausschluss, Ranking. Die Rohdaten sind so
gebaut, wie sie aus den Parsern der Quellenmodule herausfallen: viele Felder
leer, alles Wesentliche nur im Fließtext.
"""
from __future__ import annotations

from datetime import UTC, date

import pytest

from wohnungsagent.config.profil import lade_profil
from wohnungsagent.models.domain import Einzugsstatus, Inserat
from wohnungsagent.services import dedupe
from wohnungsagent.services.pipeline import Pipeline


def _pipeline() -> Pipeline:
    # Repository und LLM werden in diesem Test nicht angefasst.
    return Pipeline(lade_profil("config/suchprofil.yml"), repository=None, llm=None)


def roh(titel: str, text: str, **felder) -> Inserat:
    standard = dict(quelle="testportal", externe_id="x", url="https://example.invalid/x")
    standard.update(felder)
    return Inserat(titel=titel, beschreibung=text, **standard)


# ------------------------------------------------------------- Anreicherung

def test_anreicherung_zieht_alles_aus_dem_fliesstext():
    inserat = _pipeline().reichere_an(
        roh(
            "3-Zimmer-Altbauwohnung Nordend",
            "Schöne Wohnung im Frankfurter Nordend-West. Frei ab 01.02.2027. "
            "Mit Südbalkon und eigenem Kellerabteil. Die Einbauküche wird übernommen. "
            "Zur U-Bahn Holzhausenstraße sind es 400 Meter.",
            stadtteil="Nordend-West",
            kaltmiete=1200.0,
            nebenkosten=280.0,
            zimmer=3.0,
            flaeche=84.0,
        )
    )

    assert inserat.einzug_status is Einzugsstatus.PASST
    assert inserat.einzug_ab == date(2027, 2, 1)
    assert inserat.ausstattung.balkon is True
    assert inserat.ausstattung.keller is True
    assert inserat.ausstattung.einbaukueche is True
    assert inserat.ausstattung.oepnv_erwaehnt is True
    assert inserat.warmmiete == 1480.0            # aus Kalt + NK errechnet
    assert inserat.distanz_km is not None and inserat.distanz_km < 1.5
    assert inserat.stadtteil == "Nordend-West"


def test_sofort_frei_wird_als_zu_frueh_erkannt():
    inserat = _pipeline().reichere_an(
        roh("2-Zimmer Nordend", "Ab sofort frei, Nachmieter gesucht.", stadtteil="Nordend-West")
    )
    assert inserat.einzug_status is Einzugsstatus.ZU_FRUEH


def test_ohne_datum_bleibt_unbekannt():
    inserat = _pipeline().reichere_an(
        roh("3 Zimmer Nordend", "Gepflegte Wohnung in ruhiger Lage.", stadtteil="Nordend-Ost")
    )
    assert inserat.einzug_status is Einzugsstatus.UNBEKANNT


# ---------------------------------------------------------------- Durchlauf

def test_kompletter_durchlauf_sortiert_richtig():
    pipeline = _pipeline()

    rohdaten = [
        # 1) Idealtreffer
        roh(
            "Helle 3-Zimmer-Altbauwohnung im Nordend",
            "Frei ab 01.03.2027. Balkon nach Süden, Kellerabteil, Einbauküche vorhanden. "
            "Von privat, provisionsfrei. Die Wohnung liegt im 3. OG eines Altbaus von 1910 "
            "und eignet sich gut für eine Zweier-WG, da beide Zimmer nahezu gleich groß sind. "
            "Zur U-Bahn sind es 300 Meter, zur Frankfurt School 15 Gehminuten. Parkett, "
            "neue Fenster, Fernwärme, Energieausweis liegt vor.",
            externe_id="ideal", stadtteil="Nordend-West",
            kaltmiete=1150.0, nebenkosten=300.0, zimmer=3.0, flaeche=86.0,
            bilder=[f"b{i}.jpg" for i in range(6)],
        ),
        # 2) Passt, aber Termin unbestätigt
        roh(
            "3 Zimmer Westend-Nord",
            "Ruhige Lage, Balkon vorhanden. Bezug nach Vereinbarung.",
            externe_id="offen", stadtteil="Westend-Nord",
            kaltmiete=1300.0, nebenkosten=250.0, zimmer=3.0, flaeche=78.0,
        ),
        # 3) Muss raus: Einzug zu früh
        roh(
            "3 Zimmer Nordend ab sofort",
            "Ab sofort beziehbar, Balkon und Keller vorhanden.",
            externe_id="zufrueh", stadtteil="Nordend-West",
            kaltmiete=1200.0, nebenkosten=280.0, zimmer=3.0, flaeche=80.0,
        ),
        # 4) Muss raus: falscher Stadtteil
        roh(
            "3 Zimmer Bornheim",
            "Frei ab 01.04.2027, schöner Balkon.",
            externe_id="fernab", stadtteil="Bornheim",
            kaltmiete=1150.0, nebenkosten=300.0, zimmer=3.0, flaeche=82.0,
        ),
        # 5) Muss raus: zu teuer
        roh(
            "Luxus-Penthouse Westend-Nord",
            "Frei ab 01.02.2027, Dachterrasse, Concierge.",
            externe_id="teuer", stadtteil="Westend-Nord",
            kaltmiete=2900.0, nebenkosten=400.0, zimmer=3.0, flaeche=140.0,
        ),
        # 6) Muss raus: zu klein
        roh(
            "1-Zimmer-Apartment Nordend",
            "Frei ab 01.05.2027.",
            externe_id="klein", stadtteil="Nordend-West",
            kaltmiete=800.0, nebenkosten=150.0, zimmer=1.0, flaeche=32.0,
        ),
        # 7) Muss raus: Zwischenmiete
        roh(
            "3 Zimmer Nordend zur Zwischenmiete",
            "Frei ab 01.02.2027 bis 31.08.2027.",
            externe_id="zwischen", stadtteil="Nordend-West",
            kaltmiete=1200.0, nebenkosten=280.0, zimmer=3.0, flaeche=85.0,
        ),
    ]

    angereichert = [pipeline.reichere_an(i) for i in rohdaten]
    eindeutig = dedupe.entdoppeln(angereichert)
    treffer, verworfen = pipeline.bewerte(eindeutig)

    ids_treffer = {i.externe_id for i in treffer}
    ids_verworfen = {i.externe_id for i in verworfen}

    assert ids_treffer == {"ideal", "offen"}, ids_treffer
    assert ids_verworfen == {"zufrueh", "fernab", "teuer", "klein", "zwischen"}

    beste = max(treffer, key=lambda i: i.bewertung.score)
    assert beste.externe_id == "ideal"
    assert beste.bewertung.score > 75

    gruende = {i.externe_id: i.bewertung.ausschlussgrund for i in verworfen}
    assert "Einzug zu früh" in gruende["zufrueh"]
    assert "Bornheim" in gruende["fernab"]
    assert "Warmmiete" in gruende["teuer"]
    assert "Zimmer" in gruende["klein"]
    assert "Zwischenmiete" in gruende["zwischen"]


def test_dubletten_ueber_portale_werden_zusammengefasst():
    pipeline = _pipeline()
    gemeinsam = dict(
        stadtteil="Nordend-West", kaltmiete=1150.0, nebenkosten=300.0,
        zimmer=3.0, flaeche=86.0,
    )
    a = roh(
        "Helle 3-Zimmer-Altbauwohnung im Nordend",
        "Frei ab 01.03.2027. Balkon, Keller, Einbauküche.",
        quelle="immobilienscout24", externe_id="A",
        adresse="Eysseneckstraße 12, 60322 Frankfurt", **gemeinsam,
    )
    b = roh(
        "3 Zimmer Altbau Nordend hell",
        "Frei ab 01.03.2027. Balkon.",
        quelle="immowelt", externe_id="B",
        adresse="Eysseneckstr. 12, Frankfurt am Main", **gemeinsam,
    )

    eindeutig = dedupe.entdoppeln([pipeline.reichere_an(a), pipeline.reichere_an(b)])
    assert len(eindeutig) == 1
    assert any("auch auf" in m for m in eindeutig[0].merkmale)


def test_leerer_durchlauf_stuerzt_nicht_ab():
    pipeline = _pipeline()
    treffer, verworfen = pipeline.bewerte(dedupe.entdoppeln([]))
    assert treffer == [] and verworfen == []


def test_inserat_ohne_jede_angabe_wird_nicht_ausgeschlossen():
    """Unvollständige Inserate sollen sichtbar bleiben, nur schlechter ranken."""
    pipeline = _pipeline()
    duenn = pipeline.reichere_an(roh("Wohnung Nordend", "", stadtteil="Nordend-West"))
    treffer, _ = pipeline.bewerte([duenn])
    assert len(treffer) == 1
    assert treffer[0].bewertung.score < 60


# --------------------------------------------------- Tokenverbrauch/KI-Schalter

def test_ki_ist_standardmaessig_aus():
    """Ohne ausdrückliches Einschalten darf kein Modellaufruf entstehen."""
    from wohnungsagent.llm.client import LlmClient

    profil = lade_profil("config/suchprofil.yml")
    assert profil.ki.aktiv is False
    client = LlmClient(profil)
    assert client.aktiv is False
    assert client.aufrufe == 0
    assert client.bewerte(roh("Test", "Text")) is None


def test_ki_schalter_laesst_sich_pro_lauf_ueberstimmen():
    from wohnungsagent.llm.client import LlmClient

    profil = lade_profil("config/suchprofil.yml")
    # Eingeschaltet, aber ohne API-Schlüssel: aktiv bleibt False, kein Absturz.
    client = LlmClient(profil, eingeschaltet=True)
    assert client.eingeschaltet is True
    assert client.tokens_gesamt == 0


def test_lauf_ohne_ki_meldet_null_tokens():
    pipeline = _pipeline()
    aufrufe, tokens = pipeline.bewerte_mit_ki([roh("Test", "Text")])
    assert (aufrufe, tokens) == (0, 0)


# ------------------------------------------------------------ Kommandozeile

@pytest.mark.parametrize(
    "argv",
    [
        ["scan", "--dry-run", "-v"],           # Option hinter dem Unterbefehl
        ["-v", "scan", "--dry-run"],           # Option davor
        ["scan", "--mit-ki", "--ki-limit", "3"],
        ["scan", "--quelle", "kleinanzeigen"],
        ["pruefe", "-v"],
        ["pruefe"],
        ["top", "-n", "5"],
        ["export", "--export", "/tmp/x.json"],
        ["--profil", "config/suchprofil.yml", "scan"],
        ["scan", "--profil", "config/suchprofil.yml"],
        ["dienst", "--mit-ki"],
    ],
)
def test_cli_akzeptiert_beide_optionsreihenfolgen(argv):
    """Globale Optionen müssen vor UND hinter dem Unterbefehl stehen dürfen.

    Regression: --verbose und --profil hingen nur am Hauptparser. Dadurch war
    "scan -v" ein Fehler und nur "-v scan" erlaubt – eine Reihenfolge, die
    sich niemand merkt und die im Makefile prompt falsch stand.
    """
    from wohnungsagent.cli import parse_argumente

    try:
        args = parse_argumente(argv)
    except SystemExit as fehler:
        raise AssertionError(f"argparse lehnte {argv} ab (Code {fehler.code})") from fehler
    assert callable(args.funktion)


def test_cli_globale_option_wird_nicht_ueberschrieben():
    """Ein vor dem Unterbefehl gesetzter Wert darf nicht vom geerbten
    Standardwert des Unterbefehls überschrieben werden."""
    from wohnungsagent.cli import parse_argumente

    assert parse_argumente(["-v", "scan"]).verbose is True
    assert parse_argumente(["scan", "-v"]).verbose is True
    assert parse_argumente(["--profil", "/tmp/eigen.yml", "scan"]).profil == "/tmp/eigen.yml"
    assert parse_argumente(["scan", "--profil", "/tmp/eigen.yml"]).profil == "/tmp/eigen.yml"

    args = parse_argumente(["scan"])
    assert args.profil == "config/suchprofil.yml"
    assert args.verbose is False


# ------------------------------------------------------- Qualitätsschranke

def test_leere_huelle_ist_unbrauchbar():
    """Regression: Immowelt lieferte 62 Einträge pro Seite, aus denen der
    Parser nur die URL zog. Ohne Titel und ohne Zahlen kann keine
    Ausschlussregel greifen – solche Einträge landeten als "Treffer" im
    Dashboard und wären per Telegram gemeldet worden."""
    huelle = roh("Ohne Titel", "", quelle="immowelt", externe_id="x")
    assert huelle.ist_brauchbar is False

    nur_titel = roh("Schöne 3-Zimmer-Wohnung im Nordend", "", quelle="immowelt")
    assert nur_titel.ist_brauchbar is False       # Titel allein genügt nicht

    vollstaendig = roh("Schöne Wohnung", "", quelle="immowelt", warmmiete=1500.0)
    assert vollstaendig.ist_brauchbar is True


def test_brauchbare_inserate_bleiben_erhalten():
    from .conftest import baue_inserat

    assert baue_inserat().ist_brauchbar is True
    assert baue_inserat(warmmiete=None, kaltmiete=None, flaeche=None).ist_brauchbar is True


def test_cli_kennt_diagnose():
    from wohnungsagent.cli import parse_argumente

    args = parse_argumente(["diagnose", "kleinanzeigen"])
    assert args.quelle == "kleinanzeigen"


# ------------------------------------------------- Kennung pro Quelle

def test_kennung_ist_pro_quelle_steuerbar():
    """Standard ist die eigene, identifizierbare Kennung. Nur Quellen mit
    ausdrücklichem browser_kennung: true weichen davon ab – nachweisbar in
    der Konfiguration, nicht global im Code versteckt."""
    from wohnungsagent.config.profil import lade_profil
    from wohnungsagent.scrapers.registry import baue

    profil = lade_profil("config/suchprofil.yml")

    eigene = baue("wg_gesucht", {"aktiv": True, "city_id": 41}, profil)
    assert eigene.eigene_kennung is True
    assert "wohnungsagent" in eigene.user_agent

    browser = baue("wg_gesucht", {"aktiv": True, "browser_kennung": True}, profil)
    assert browser.eigene_kennung is False
    assert browser.user_agent.startswith("Mozilla/")


def test_nur_kleinanzeigen_nutzt_browser_kennung():
    """Wenn jemand den Schalter versehentlich breit setzt, soll das auffallen."""
    from wohnungsagent.config.profil import lade_profil

    profil = lade_profil("config/suchprofil.yml")
    mit_browser = {
        name for name, cfg in profil.quellen.items() if cfg.get("browser_kennung")
    }
    assert mit_browser == {"kleinanzeigen"}, mit_browser


def test_probelauf_und_echtlauf_nehmen_denselben_weg():
    """Regression: der Probelauf umging die Qualitätsschranke und zeigte
    leere Hüllen als Treffer an, die ein echter Lauf verworfen hätte."""
    from wohnungsagent.models.domain import Laufergebnis

    pipeline = _pipeline()
    rohdaten = [
        roh("Ohne Titel", "", quelle="immowelt", externe_id="leer1", warmmiete=1850.0),
        roh("Ohne Titel", "", quelle="immowelt", externe_id="leer2", warmmiete=1900.0),
        roh("3-Zimmer-Wohnung Nordend", "Frei ab 01.03.2027, Balkon.",
            quelle="wg_gesucht", externe_id="echt", stadtteil="Nordend-West",
            warmmiete=1500.0, zimmer=3.0, flaeche=82.0),
    ]
    ergebnis = Laufergebnis()
    eindeutig = pipeline.aufbereiten(rohdaten, ergebnis)

    assert ergebnis.unbrauchbar == 2
    assert [i.externe_id for i in eindeutig] == ["echt"]


def test_browser_kennung_sendet_stimmige_kopfzeilen():
    """Eine Browser-Kennung mit application/json im Accept-Header fällt auf.
    Ist der Schalter an, muss der ganze Satz zusammenpassen."""
    from wohnungsagent.config.profil import lade_profil
    from wohnungsagent.scrapers.registry import baue

    profil = lade_profil("config/suchprofil.yml")

    browser = baue("wg_gesucht", {"aktiv": True, "browser_kennung": True}, profil)
    kopf = browser.session.headers
    assert "application/json" not in kopf["Accept"]
    assert kopf["Sec-Fetch-Mode"] == "navigate"
    assert kopf["Upgrade-Insecure-Requests"] == "1"

    eigene = baue("wg_gesucht", {"aktiv": True}, profil)
    assert "Sec-Fetch-Mode" not in eigene.session.headers
    assert "wohnungsagent" in eigene.session.headers["User-Agent"]


def test_ausschlussgruende_werden_zusammengefasst(capsys=None):
    """Bei 51 von 56 aussortierten Inseraten ist die Verteilung der Gründe
    die wichtigste Information des Laufs – sie muss auch im Probelauf
    erscheinen, nicht nur im echten Durchlauf."""
    from wohnungsagent.services.pipeline import protokolliere_ausschluesse

    pipeline = _pipeline()
    rohdaten = [
        roh("3 Zimmer Nordend", "Ab sofort frei.", externe_id="a",
            stadtteil="Nordend-West", warmmiete=1500.0, zimmer=3.0, flaeche=80.0),
        roh("3 Zimmer Bornheim", "Frei ab 01.03.2027.", externe_id="b",
            stadtteil="Bornheim", warmmiete=1500.0, zimmer=3.0, flaeche=80.0),
    ]
    angereichert = [pipeline.reichere_an(i) for i in rohdaten]
    _, verworfen = pipeline.bewerte(angereichert)

    assert len(verworfen) == 2
    protokolliere_ausschluesse(verworfen)      # darf nicht abstürzen
    gruende = {i.bewertung.ausschlussgrund.split(" (")[0] for i in verworfen}
    assert any("Einzug zu früh" in g for g in gruende)


# ------------------------------------------------------------- Zeitzonen

def test_zeitstempel_ohne_zeitzone_werden_angeglichen():
    """Regression aus dem ersten echten GitHub-Lauf.

    SQLite speichert Zeitstempel ohne Zeitzone, im Programm wird mit UTC
    gerechnet. Beim Export nach dem ersten Schreiben in die Datenbank
    scheiterte deshalb `ist_neu` mit
    "can't subtract offset-naive and offset-aware datetimes".
    Der Probelauf berührte die Datenbank nie und übersah das.
    """
    from datetime import datetime, timedelta

    from wohnungsagent.models.domain import als_utc

    naiv = datetime(2026, 8, 3, 10, 0, 0)
    assert als_utc(naiv).tzinfo is UTC
    assert als_utc(None) is None

    behaftet = datetime(2026, 8, 3, 10, 0, 0, tzinfo=UTC)
    assert als_utc(behaftet) == behaftet

    # So kommt ein Inserat aus der Datenbank zurück: ohne Zeitzone.
    frisch = roh("Testwohnung", "", warmmiete=1500.0)
    frisch.erstmals_gesehen = datetime.now(UTC).replace(tzinfo=None)
    assert frisch.ist_neu is True
    assert frisch.als_dict()["erstmals_gesehen"]          # darf nicht werfen

    alt = roh("Alte Wohnung", "", warmmiete=1500.0)
    alt.erstmals_gesehen = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3)
    assert alt.ist_neu is False


def test_laufergebnis_dauer_mit_naiven_zeitstempeln():
    from datetime import UTC, datetime

    from wohnungsagent.models.domain import Laufergebnis

    ergebnis = Laufergebnis()
    ergebnis.gestartet = datetime.now(UTC).replace(tzinfo=None)
    ergebnis.beendet = None
    assert ergebnis.dauer_s >= 0                # ohne Zeitzone, darf nicht werfen
