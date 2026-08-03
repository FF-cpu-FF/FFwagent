"""robots.txt-Prüfung.

Das Suchprofil verlangt ausdrücklich, robots.txt zu respektieren. Diese
Prüfung ist deshalb keine Formalie, sondern ein Schalter: eine Quelle mit
`robots_pflicht: true` wird stillgelegt, sobald ihre robots.txt den Suchpfad
untersagt. Der Lauf bricht nicht ab, die Quelle taucht im Ergebnis unter
`quellen_robots_blockiert` auf.

Praktische Folge, die man kennen sollte: mehrere große Portale untersagen
genau die Pfade, unter denen Suchergebnisse liegen. Diese Quellen liefern
dann dauerhaft nichts – das ist kein Fehler, sondern die Regel, die das
Suchprofil selbst gesetzt hat. Wer die Sperre umgehen möchte, muss
`robots_pflicht` bewusst auf false setzen und trägt die Verantwortung dafür.
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import requests


@dataclass(slots=True)
class RobotsUrteil:
    erlaubt: bool
    begruendung: str
    crawl_delay: float | None = None
    # Warum das Urteil so ausfällt. Wichtig, weil "erlaubt" zwei sehr
    # verschiedene Dinge heißen kann: robots.txt gestattet den Pfad
    # ausdrücklich – oder die Datei war nicht abrufbar und wir lassen im
    # Zweifel durch. Ohne diese Unterscheidung sieht ein Bot-Schutz, der
    # schon die robots.txt blockt, wie eine Freigabe aus.
    status: str = "erlaubt"      # erlaubt | keine_datei | unerreichbar | gesperrt


class RobotsPruefer:
    """Cached robots.txt je Host für die Dauer eines Laufs."""

    def __init__(self, user_agent: str, timeout: int = 15) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._grund: dict[str, str] = {}

    def _parser(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        teile = urlparse(url)
        host = f"{teile.scheme}://{teile.netloc}"
        if host in self._cache:
            return self._cache[host]

        parser = urllib.robotparser.RobotFileParser()
        try:
            antwort = requests.get(
                f"{host}/robots.txt",
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
            )
            if antwort.status_code == 404:
                # Keine robots.txt = keine Einschränkung
                parser.parse([])
                self._cache[host] = parser
                self._grund[host] = "keine_datei"
                return parser
            if antwort.ok:
                parser.parse(antwort.text.splitlines())
                self._cache[host] = parser
                self._grund[host] = "erlaubt"
                return parser
            self._cache[host] = None
            self._grund[host] = f"HTTP {antwort.status_code}"
            return None
        except requests.RequestException as fehler:
            self._cache[host] = None
            self._grund[host] = type(fehler).__name__
            return None

    def pruefe(self, url: str) -> RobotsUrteil:
        host = urlparse(url).netloc
        parser = self._parser(url)

        if parser is None:
            # robots.txt nicht abrufbar. Wir lassen durch, damit ein
            # Serverfehler die Suche nicht dauerhaft lahmlegt – melden es
            # aber deutlich, weil ein 401/403 auf die robots.txt selbst ein
            # starkes Zeichen für aktiven Bot-Schutz ist.
            return RobotsUrteil(
                True,
                f"robots.txt von {host} nicht abrufbar ({self._grund.get(host, 'unbekannt')}) "
                "– nicht geprüft, gedrosselt",
                5.0,
                status="unerreichbar",
            )

        # Kein "or can_fetch('*')": robotparser wertet die Wildcard-Gruppe
        # bereits aus, wenn es für unseren User-Agent keine eigene gibt. Ein
        # zusätzliches "or" würde eine gezielte Sperre für uns aushebeln.
        if parser.can_fetch(self.user_agent, url):
            delay = None
            try:
                roh = parser.crawl_delay(self.user_agent) or parser.crawl_delay("*")
                delay = float(roh) if roh else None
            except Exception:
                delay = None
            status = self._grund.get(host, "erlaubt")
            return RobotsUrteil(
                True,
                "keine robots.txt vorhanden" if status == "keine_datei" else "durch robots.txt erlaubt",
                delay,
                status=status,
            )

        return RobotsUrteil(
            False, f"robots.txt von {host} untersagt diesen Pfad", status="gesperrt"
        )


# Gängige Browser-Kennung für Quellen, die alles andere stumm abweisen.
# Wird nur verwendet, wenn eine Quelle in config/suchprofil.yml
# `browser_kennung: true` gesetzt hat.
BROWSER_KENNUNG = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def baue_user_agent(kontakt: str, version: str = "1.0") -> str:
    """Identifizierender User-Agent.

    Sich als gewöhnlicher Browser auszugeben und gleichzeitig robots.txt zu
    respektieren wäre widersprüchlich. Der Agent nennt sich beim Namen; das
    führt bei manchen Portalen zu einer Ablehnung, ist aber die ehrliche
    Variante und die, die das Suchprofil verlangt.
    """
    return f"wohnungsagent/{version} (+{kontakt})"


class Drossel:
    """Hält je Host den vorgeschriebenen Mindestabstand zwischen Anfragen ein."""

    def __init__(self, standard_sekunden: float = 2.0) -> None:
        self.standard = standard_sekunden
        self._letzter: dict[str, float] = {}
        self._abstand: dict[str, float] = {}

    def setze(self, url: str, sekunden: float | None) -> None:
        if sekunden:
            self._abstand[urlparse(url).netloc] = max(sekunden, self.standard)

    def warte(self, url: str) -> None:
        host = urlparse(url).netloc
        abstand = self._abstand.get(host, self.standard)
        vergangen = time.monotonic() - self._letzter.get(host, 0.0)
        if vergangen < abstand:
            time.sleep(abstand - vergangen)
        self._letzter[host] = time.monotonic()
