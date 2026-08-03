"""Kommandozeile.

    python -m wohnungsagent.cli scan            einmal durchlaufen
    python -m wohnungsagent.cli scan --dry-run  nichts speichern, nichts melden
    python -m wohnungsagent.cli scan --quelle kleinanzeigen
    python -m wohnungsagent.cli pruefe          Profil und Quellen validieren
    python -m wohnungsagent.cli top -n 10       beste Treffer anzeigen
    python -m wohnungsagent.cli export          JSON für das Pages-Dashboard
    python -m wohnungsagent.cli dienst          Dauerbetrieb mit Scheduler
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from .config.profil import ProfilFehler, lade_profil
from .llm.client import LlmClient
from .services.pipeline import Pipeline

# Repository und Export ziehen SQLAlchemy nach. Sie werden erst im jeweiligen
# Befehl geladen, damit `pruefe` und `--help` auch dann funktionieren, wenn an
# der Datenbankschicht etwas klemmt.


def _logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<dim>{time:HH:mm:ss}</dim> <level>{level: <8}</level> {message}",
    )
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/agent_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days", level="DEBUG")


def _pipeline(args) -> Pipeline:
    from .database.repository import Repository

    profil = lade_profil(args.profil)
    repo = Repository(args.datenbank)
    # Die KI ist der einzige Teil, der Tokens kostet, deshalb ist sie
    # ausdrücklich anzuschalten: über --mit-ki oder ki.aktiv im Profil.
    llm = LlmClient(profil, eingeschaltet=True if getattr(args, "mit_ki", False) else None)
    return Pipeline(profil, repo, llm, geocoding=getattr(args, "geocoding", False))


def befehl_scan(args) -> int:
    pipeline = _pipeline(args)
    if args.dry_run:
        roh, ergebnis = pipeline.sammle(args.quelle)
        eindeutig = pipeline.aufbereiten(roh, ergebnis)
        treffer, verworfen = pipeline.bewerte(eindeutig)
        logger.info(
            "Probelauf: {} roh -> {} ohne Inhalt verworfen -> {} eindeutig "
            "-> {} Treffer, {} aussortiert (keine Tokens, nichts gespeichert)",
            len(roh), ergebnis.unbrauchbar, len(eindeutig),
            len(treffer), len(verworfen),
        )
        from .services.pipeline import protokolliere_ausschluesse

        protokolliere_ausschluesse(verworfen)

        for inserat in sorted(treffer, key=lambda i: i.bewertung.score, reverse=True)[:15]:
            # Kleinanzeigen nennt die Kaltmiete, WG-Gesucht die Warmmiete.
            # Nur eine davon anzuzeigen ließ jede zweite Zeile wie ein
            # Datenfehler aussehen.
            if inserat.warmmiete:
                preis = f"{inserat.warmmiete:.0f} € warm"
            elif inserat.kaltmiete:
                preis = f"{inserat.kaltmiete:.0f} € kalt"
            else:
                preis = "Preis offen"
            groesse = " ".join(
                teil for teil in (
                    f"{inserat.zimmer:g} Zi." if inserat.zimmer else "",
                    f"{inserat.flaeche:.0f} m²" if inserat.flaeche else "",
                    f"{inserat.distanz_km:.1f} km" if inserat.distanz_km is not None else "",
                ) if teil
            )
            logger.info(
                "  [{:3d}] {:52s} | {:14s} | {:22s} | {}",
                inserat.bewertung.score, inserat.titel[:52], preis, groesse, inserat.url,
            )
        return 0

    ergebnis = pipeline.laufe(
        nur_quelle=args.quelle,
        melden=not args.ohne_meldung,
        mindestscore=args.mindestscore,
        ki_limit=args.ki_limit,
    )
    if ergebnis.quellen_robots_blockiert:
        logger.warning(
            "Durch robots.txt gesperrt: {}. Das ist erwartetes Verhalten, siehe README.",
            ", ".join(ergebnis.quellen_robots_blockiert),
        )
    from .services.export import exportiere

    exportiere(pipeline.repo, Path(args.export))
    return 0


def befehl_pruefe(args) -> int:
    from .scrapers.registry import baue_alle, verfuegbare_quellen

    profil = lade_profil(args.profil)
    logger.success("Profil gültig: {}", args.profil)
    logger.info(
        "Referenzpunkt {} bei {:.4f}/{:.4f}, Radius {} km",
        profil.referenzpunkt.name, profil.referenzpunkt.lat, profil.referenzpunkt.lon,
        profil.referenzpunkt.max_km,
    )
    logger.info("Einzug frühestens {:%d.%m.%Y}, unbekannt -> {}",
                profil.einzug.fruehestens, profil.einzug.unbekannt)
    logger.info("Verfügbare Quellen: {}", ", ".join(verfuegbare_quellen()))

    unerreichbar = []
    for scraper in baue_alle(profil):
        seiten = list(scraper.suchseiten())
        if not seiten:
            logger.info("  {:28s}  – kein Scraper (Hinweisquelle)", scraper.label)
            continue
        if not scraper.cfg.get("robots_pflicht", True):
            logger.info("  {:28s} {:>2} Seiten  robots-Prüfung abgeschaltet",
                        scraper.label, len(seiten))
            continue

        urteil = scraper.robots.pruefe(seiten[0].url)
        beschriftung = {
            "erlaubt": "robots.txt erlaubt diesen Pfad",
            "keine_datei": "keine robots.txt – keine Einschränkung",
            "unerreichbar": "robots.txt NICHT ABRUFBAR – ungeprüft",
            "gesperrt": "durch robots.txt GESPERRT",
        }[urteil.status]
        logger.info("  {:28s} {:>2} Seiten  {}", scraper.label, len(seiten), beschriftung)
        if urteil.status == "unerreichbar":
            unerreichbar.append(scraper.label)

    if unerreichbar:
        logger.warning(
            "Bei diesen Quellen war schon die robots.txt nicht abrufbar: {}. "
            "Das ist meist aktiver Bot-Schutz – erwarte dort auch bei der Suche "
            "HTTP 401/403. Der Agent versucht es trotzdem und drosselt stark.",
            ", ".join(unerreichbar),
        )
    return 0


def befehl_diagnose(args) -> int:
    """Prüft eine einzelne Quelle und sagt, woran es liegt.

    Holt die erste Suchseite, meldet Statuscode und Größe, zählt die
    Treffer des erwarteten Selektors und legt das HTML unter logs/ ab.
    Damit lässt sich der häufigste Fall – "HTTP 200, aber null Inserate" –
    ohne Ratespiel eingrenzen.
    """
    from pathlib import Path as Pfad

    from .scrapers.registry import baue, verfuegbare_quellen

    profil = lade_profil(args.profil)
    quellen_cfg = dict(profil.quellen.get(args.quelle) or {})
    if not quellen_cfg:
        logger.error("Unbekannte Quelle '{}'. Verfügbar: {}",
                     args.quelle, ", ".join(verfuegbare_quellen()))
        return 2
    quellen_cfg["aktiv"] = True          # auch abgeschaltete Quellen prüfbar

    scraper = baue(args.quelle, quellen_cfg, profil)
    seiten = list(scraper.suchseiten())
    if not seiten:
        logger.info("{} ist eine Hinweisquelle ohne Scraper.", scraper.label)
        return 0

    urteil = scraper.robots.pruefe(seiten[0].url)
    logger.info("Quelle:   {}", scraper.label)
    logger.info("URL:      {}", seiten[0].url)
    logger.info("robots:   {} ({})", urteil.status, urteil.begruendung)
    logger.info("Rendern:  {}", "Playwright" if scraper.braucht_playwright else "einfacher HTTP-Abruf")
    logger.info("Kennung:  {} ({})", scraper.user_agent,
                "eigene" if scraper.eigene_kennung else "Browser, per browser_kennung: true")

    try:
        html = scraper.hole(seiten[0].url)
    except Exception as fehler:  # noqa: BLE001
        logger.error("Abruf fehlgeschlagen: {}: {}", type(fehler).__name__, fehler)
        return 1

    logger.info("Antwort:  {} Zeichen HTML", len(html))
    for hinweis, muster in (
        ("Zugriff verweigert", "access denied"),
        ("Bot-Abwehr (Captcha)", "captcha"),
        ("Cloudflare-Zwischenseite", "cf-browser-verification"),
        ("Cookie-Abfrage", "consent"),
    ):
        if muster in html.lower():
            logger.warning("HTML enthält '{}' – Hinweis auf {}", muster, hinweis)

    treffer = scraper.parse_seite(html, seiten[0])
    brauchbare = [i for i in treffer if i.ist_brauchbar]
    logger.info("Parser:   {} Inserate, davon {} mit verwertbaren Angaben",
                len(treffer), len(brauchbare))

    for inserat in brauchbare[:3]:
        logger.info("   {} | {} € | {} Zi. | {} m² | {}",
                    inserat.titel[:50],
                    f"{inserat.warmmiete or inserat.kaltmiete:.0f}"
                    if (inserat.warmmiete or inserat.kaltmiete) else "?",
                    inserat.zimmer or "?", inserat.flaeche or "?", inserat.stadtteil or "?")

    if getattr(args, "browser_ua", False) and not scraper.braucht_playwright:
        # Gegenprobe: dieselbe URL mit einer gewöhnlichen Browser-Kennung.
        # Nur zur Diagnose – der Agent selbst bleibt bei seiner eigenen
        # Kennung, weil er robots.txt respektiert und sich nicht verstecken soll.
        import requests as _requests

        vergleich = _requests.get(
            seiten[0].url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/126.0.0.0 Safari/537.36",
                "Accept-Language": "de-DE,de;q=0.9",
            },
            timeout=profil.betrieb.timeout_sekunden,
        )
        mit_browser = scraper.parse_seite(vergleich.text, seiten[0])
        logger.info("Gegenprobe mit Browser-Kennung: HTTP {}, {} Inserate",
                    vergleich.status_code, len(mit_browser))
        if len(mit_browser) > len(treffer):
            logger.warning(
                "Die Seite antwortet auf unsere Kennung mit weniger Inhalt. "
                "Das ist ein stiller Block – kein Parserfehler."
            )

    ziel = Pfad("logs") / f"diagnose_{args.quelle}.html"
    ziel.parent.mkdir(exist_ok=True)
    ziel.write_text(html, encoding="utf-8")
    logger.info("HTML gespeichert: {} – im Browser öffnen und Selektoren vergleichen", ziel)

    if not treffer:
        logger.warning(
            "Null Inserate bei HTTP 200. Entweder passt die URL nicht zum Ort, "
            "oder das Markup hat sich geändert. Die gespeicherte Datei zeigt, was ankam."
        )
    elif not brauchbare:
        logger.warning(
            "Links werden gefunden, Felder nicht. Der Parser braucht neue Selektoren – "
            "siehe scrapers/{}.py, Methode parse_seite.", args.quelle,
        )
    return 0


def befehl_top(args) -> int:
    from .database.repository import Repository

    repo = Repository(args.datenbank)
    treffer = repo.treffer(limit=args.anzahl)
    if not treffer:
        logger.info("Noch keine Treffer. Erst 'scan' laufen lassen.")
        return 0
    for rang, inserat in enumerate(treffer, 1):
        logger.info(
            "{:2d}. [{:3d}] {:52s} {:>7} {:>5} {:>6} {:>7}  {}",
            rang, inserat.bewertung.score, inserat.titel[:52],
            f"{inserat.warmmiete:.0f}€" if inserat.warmmiete else "–",
            f"{inserat.zimmer:g}Zi" if inserat.zimmer else "–",
            f"{inserat.flaeche:.0f}m²" if inserat.flaeche else "–",
            f"{inserat.distanz_km:.1f}km" if inserat.distanz_km is not None else "–",
            inserat.url,
        )
        if inserat.ki and inserat.ki.zusammenfassung:
            logger.info("     {}", inserat.ki.zusammenfassung)
    return 0


def befehl_export(args) -> int:
    from .database.repository import Repository
    from .services.export import exportiere

    repo = Repository(args.datenbank)
    pfad = exportiere(repo, Path(args.export))
    logger.success("Exportiert nach {}", pfad)
    return 0


def befehl_dienst(args) -> int:
    from .scheduler import starte_dienst

    starte_dienst(args)
    return 0


def baue_parser() -> argparse.ArgumentParser:
    """Baut den Argumentparser. Getrennt von main(), damit die
    Kommandozeile testbar ist, ohne einen Befehl auszuführen."""
    # Die allgemeinen Optionen liegen in einem gemeinsamen Elternparser und
    # werden an jeden Unterbefehl vererbt. Sonst wäre "scan -v" ein Fehler und
    # nur "-v scan" erlaubt – eine Reihenfolge, die sich niemand merkt.
    #
    # default=SUPPRESS ist dabei entscheidend: ohne das würde der geerbte
    # Standardwert des Unterbefehls den vor dem Unterbefehl gesetzten Wert
    # wieder überschreiben.
    gemeinsam = argparse.ArgumentParser(add_help=False)
    gemeinsam.add_argument("--profil", default=argparse.SUPPRESS)
    gemeinsam.add_argument("--datenbank", default=argparse.SUPPRESS)
    gemeinsam.add_argument("--export", default=argparse.SUPPRESS)
    gemeinsam.add_argument("--verbose", "-v", action="store_true", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="wohnungsagent", description="Wohnungsagent Frankfurt", parents=[gemeinsam]
    )
    # Kein set_defaults hier: parents=[...] übernimmt dieselben Action-Objekte
    # in jeden Unterbefehl. set_defaults mutiert das Objekt und würde damit
    # auch dort das SUPPRESS überschreiben – genau der Effekt, den wir
    # vermeiden wollen. Die Standardwerte kommen deshalb nach dem Parsen
    # in parse_argumente() dazu.

    unter = parser.add_subparsers(dest="befehl", required=True)

    scan = unter.add_parser("scan", help="einen Durchlauf ausführen", parents=[gemeinsam])
    scan.add_argument("--quelle", help="nur diese Quelle")
    scan.add_argument("--dry-run", action="store_true", help="nichts speichern, nichts melden")
    scan.add_argument("--ohne-meldung", action="store_true")
    scan.add_argument(
        "--mit-ki", action="store_true",
        help="KI-Zusammenfassungen erzeugen. Verbraucht LLM-Tokens, standardmäßig aus.",
    )
    scan.add_argument(
        "--ki-limit", type=int, default=None,
        help="höchstens so viele Inserate an das Modell geben (Standard: ki.max_inserate_pro_lauf)",
    )
    scan.add_argument("--geocoding", action="store_true", help="Adressen über Nominatim auflösen")
    scan.add_argument("--mindestscore", type=int, default=0)
    scan.set_defaults(funktion=befehl_scan)

    pruefe = unter.add_parser("pruefe", parents=[gemeinsam], help="Profil und Quellen validieren, ohne zu scrapen")
    pruefe.set_defaults(funktion=befehl_pruefe)

    diagnose = unter.add_parser(
        "diagnose", parents=[gemeinsam],
        help="eine Quelle einzeln prüfen und das HTML zur Ansicht speichern",
    )
    diagnose.add_argument("quelle", help="z. B. kleinanzeigen, immowelt, wg_gesucht")
    diagnose.add_argument(
        "--browser-ua", action="store_true",
        help="zusätzlich mit Browser-Kennung abfragen. Liefert das plötzlich "
             "Treffer, blockt die Seite stillschweigend unsere ehrliche Kennung.",
    )
    diagnose.set_defaults(funktion=befehl_diagnose)

    top = unter.add_parser("top", parents=[gemeinsam], help="beste Treffer anzeigen")
    top.add_argument("-n", "--anzahl", type=int, default=10)
    top.set_defaults(funktion=befehl_top)

    export = unter.add_parser("export", parents=[gemeinsam], help="JSON für das Dashboard schreiben")
    export.set_defaults(funktion=befehl_export)

    dienst = unter.add_parser("dienst", parents=[gemeinsam], help="Dauerbetrieb mit Scheduler")
    dienst.add_argument("--mit-ki", action="store_true")
    dienst.add_argument("--geocoding", action="store_true")
    dienst.add_argument("--mindestscore", type=int, default=0)
    dienst.set_defaults(funktion=befehl_dienst)

    return parser


STANDARDWERTE = {
    "profil": "config/suchprofil.yml",
    "datenbank": "sqlite:///data/wohnungen.db",
    "export": "docs/data/listings.json",
    "verbose": False,
}


def parse_argumente(argv: list[str] | None = None) -> argparse.Namespace:
    """Parst und ergänzt die Standardwerte für alles, was nicht angegeben war."""
    args = baue_parser().parse_args(argv)
    for name, wert in STANDARDWERTE.items():
        if not hasattr(args, name):
            setattr(args, name, wert)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_argumente(argv)
    _logging(args.verbose)

    try:
        return args.funktion(args)
    except ProfilFehler as fehler:
        logger.error("Suchprofil fehlerhaft: {}", fehler)
        return 2
    except KeyboardInterrupt:
        logger.info("Abgebrochen.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
