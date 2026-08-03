"""Registry: Name aus `config/suchprofil.yml` -> Scraper-Instanz.

Eine neue Quelle ergänzt man entweder als eigene Klasse (dann hier in
`KLASSEN` eintragen) oder – bei einfachem Aufbau – als Eintrag in
`vermieter.BAUPLAENE`, ganz ohne Code.
"""
from __future__ import annotations

from ..config.profil import Suchprofil
from .base import Scraper
from .immoscout24 import ImmobilienScout24
from .immowelt import Immonet, Immowelt
from .kleinanzeigen import Kleinanzeigen
from .robots import Drossel, RobotsPruefer
from .vermieter import BAUPLAENE, BauplanScraper
from .wg_gesucht import WgGesucht

KLASSEN: dict[str, type[Scraper]] = {
    ImmobilienScout24.name: ImmobilienScout24,
    Immowelt.name: Immowelt,
    Immonet.name: Immonet,
    Kleinanzeigen.name: Kleinanzeigen,
    WgGesucht.name: WgGesucht,
}

# Reihenfolge der Ausführung: erst die Quellen mit dem größten Angebot.
PRIORITAETSFOLGE = {"A": 0, "B": 1, "C": 2}


def verfuegbare_quellen() -> list[str]:
    return sorted(set(KLASSEN) | set(BAUPLAENE))


def baue(
    name: str,
    quellen_cfg: dict,
    profil: Suchprofil,
    robots: RobotsPruefer | None = None,
    drossel: Drossel | None = None,
) -> Scraper:
    if name in KLASSEN:
        return KLASSEN[name](quellen_cfg, profil, robots, drossel)
    if name in BAUPLAENE:
        return BauplanScraper(BAUPLAENE[name], quellen_cfg, profil, robots, drossel)
    raise KeyError(
        f"Unbekannte Quelle '{name}'. Verfügbar: {', '.join(verfuegbare_quellen())}"
    )


def baue_alle(profil: Suchprofil, nur: str | None = None) -> list[Scraper]:
    """Erzeugt alle aktiven Scraper, sortiert nach Priorität.

    Robots-Prüfer und Drossel werden geteilt, damit robots.txt je Host nur
    einmal geladen wird und die Wartezeiten quellenübergreifend gelten.
    """
    from .robots import baue_user_agent

    robots = RobotsPruefer(
        baue_user_agent(profil.betrieb.user_agent_kontakt), profil.betrieb.timeout_sekunden
    )
    drossel = Drossel(profil.betrieb.request_pause_sekunden[0])

    aktive = profil.aktive_quellen()
    if nur:
        aktive = {n: c for n, c in aktive.items() if n == nur}
        if not aktive:
            raise KeyError(f"Quelle '{nur}' ist nicht aktiv oder existiert nicht")

    sortiert = sorted(aktive.items(), key=lambda p: PRIORITAETSFOLGE.get(p[1].get("prioritaet", "C"), 3))
    return [baue(name, cfg, profil, robots, drossel) for name, cfg in sortiert]
