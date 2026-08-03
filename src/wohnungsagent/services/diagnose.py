"""Diagnose einer stummen Quelle.

Für den häufigsten und ärgerlichsten Fehlerfall: HTTP 200, aber null Treffer.
Dafür gibt es drei sehr verschiedene Ursachen, die von außen gleich aussehen –
falsche URL, geänderte Selektoren, oder eine Bot-Wand, die eine leere Seite
ausliefert statt einen Fehlercode. Dieser Befehl trennt sie auseinander,
statt raten zu lassen.

    python -m wohnungsagent.cli diagnose --quelle kleinanzeigen
"""
from __future__ import annotations

import re

from loguru import logger

from ..config.profil import Suchprofil
from ..scrapers.registry import baue

# Textmarken, die auf eine Bot-Abwehrseite hindeuten statt auf ein leeres Ergebnis
BOT_MARKEN = [
    "captcha", "cf-challenge", "just a moment", "access denied", "bot detection",
    "unusual traffic", "zugriff verweigert", "sicherheitsabfrage", "datadome",
    "incapsula", "perimeterx", "awswaf", "verify you are human",
]

# Formulierungen, mit denen Portale ein legitim leeres Ergebnis melden
LEER_MARKEN = [
    "keine ergebnisse", "keine anzeigen", "0 ergebnisse", "nichts gefunden",
    "leider keine", "no results", "keine treffer", "keine passenden",
]


def diagnostiziere(profil: Suchprofil, quelle: str, browser_ua: bool = False) -> int:
    cfg = profil.quellen.get(quelle)
    if cfg is None:
        logger.error("Quelle '{}' steht nicht im Suchprofil", quelle)
        return 1

    scraper = baue(quelle, {**cfg, "aktiv": True}, profil)
    seiten = list(scraper.suchseiten())
    if not seiten:
        logger.info("{} baut keine Suchseiten (Hinweisquelle)", scraper.label)
        return 0

    if browser_ua:
        # Manche Portale liefern einem selbstdeklarierten Agenten eine leere
        # Seite statt eines Fehlercodes. Der Vergleich zeigt, ob das der Grund
        # ist – bewusst nur im Diagnosemodus und nicht im Normalbetrieb.
        scraper.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        logger.info("Diagnose mit Browser-Kennung statt eigener Agentenkennung")

    seite = seiten[0]
    logger.info("URL: {}", seite.url)

    try:
        html = scraper.hole(seite.url)
    except Exception as fehler:  # noqa: BLE001
        logger.error("Abruf fehlgeschlagen: {}", fehler)
        return 1

    flach = html.lower()
    logger.info("Antwortgröße: {} Zeichen", f"{len(html):,}".replace(",", "."))

    if (marke := next((m for m in BOT_MARKEN if m in flach), None)):
        logger.error(
            "Bot-Abwehr erkannt (Textmarke '{}'). Die Seite liefert keine Ergebnisse, "
            "sondern eine Prüfseite. Kein Selektorproblem.", marke,
        )
        return 2

    if (marke := next((m for m in LEER_MARKEN if m in flach), None)):
        logger.warning(
            "Die Seite meldet ausdrücklich ein leeres Ergebnis ('{}'). "
            "Die Filter sind vermutlich zu eng, oder die URL zeigt auf den falschen Ort.",
            marke,
        )

    treffer = scraper.parse_seite(html, seite)
    logger.info("Parser findet {} Inserate", len(treffer))
    if treffer:
        for inserat in treffer[:3]:
            logger.info(
                "  {} | {} | {} Zi. | {} m² | {}",
                inserat.titel[:45],
                f"{inserat.warmmiete or inserat.kaltmiete or '?'} €",
                inserat.zimmer or "?", inserat.flaeche or "?", inserat.url[:70],
            )
        return 0

    # Kein Treffer und keine Bot-Wand: dann liegt es an den Selektoren oder
    # daran, dass die Seite ihre Inhalte per Javascript nachlädt.
    logger.warning("Keine Inserate erkannt. Anhaltspunkte aus dem Rohtext:")
    for beschriftung, muster in [
        ("Preisangaben (€)", r"\d[\d.]*\s*€"),
        ("Flächenangaben (m²)", r"\d+\s*m²"),
        ("Zimmerangaben", r"\d[,\d]*\s*Zimmer"),
        ("Links auf Detailseiten", r'href="[^"]*(expose|/s-anzeige/|\.html)'),
    ]:
        logger.info("  {:26s} {}", beschriftung, len(re.findall(muster, html, re.I)))

    logger.info(
        "Stehen oben überall Nullen, kam kein Inhalt an – dann stimmt die URL nicht "
        "oder die Seite braucht einen Browser (playwright: true). Stehen dort Zahlen, "
        "sind die Selektoren in scrapers/{}.py veraltet.", quelle,
    )
    if not browser_ua:
        logger.info(
            "Nächster Schritt zum Eingrenzen: denselben Befehl mit --browser-ua "
            "wiederholen. Kommen dann Treffer, blockt das Portal die Agentenkennung."
        )
    return 3
