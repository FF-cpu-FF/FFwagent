"""Basisklasse für alle Quellenmodule.

Eine neue Quelle zu ergänzen heißt: von `Scraper` erben, `basis_url` und
`suchseiten()` definieren, `parse_seite()` implementieren, in
`registry.py` eintragen und in `config/suchprofil.yml` aktivieren. Mehr nicht –
robots.txt, Drosselung, Retry, Fehlerisolierung und Normalisierung liegen hier.

`hole()` respektiert robots.txt, wenn die Quelle mit `robots_pflicht: true`
konfiguriert ist. Quellen, die Javascript brauchen, setzen `playwright: true`
und bekommen einen gerenderten HTML-Text statt einer HTTP-Antwort.
"""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

import requests
from loguru import logger
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config.profil import Suchprofil
from ..models.domain import Inserat
from .robots import BROWSER_KENNUNG, Drossel, RobotsPruefer, baue_user_agent


class QuelleBlockiert(Exception):
    """Quelle hat die Anfrage abgelehnt (403/429) oder robots.txt untersagt sie."""


class QuelleKaputt(Exception):
    """Antwort kam an, ließ sich aber nicht auswerten – Markup hat sich geändert."""


@dataclass(slots=True)
class Suchseite:
    url: str
    beschriftung: str = ""


class Scraper(ABC):
    name: str = "basis"
    label: str = "Basis"
    basis_url: str = ""
    braucht_playwright: bool = False

    def __init__(
        self,
        quellen_cfg: dict,
        profil: Suchprofil,
        robots: RobotsPruefer | None = None,
        drossel: Drossel | None = None,
    ) -> None:
        self.cfg = quellen_cfg
        self.profil = profil
        # Standard ist die eigene, identifizierbare Kennung. Einzelne Quellen
        # liefern damit nachweislich leere Seiten – dort lässt sich per
        # `browser_kennung: true` umstellen. Bewusst pro Quelle und in der
        # Konfiguration sichtbar, nicht global und nicht versteckt.
        self.eigene_kennung = not quellen_cfg.get("browser_kennung", False)
        self.user_agent = (
            baue_user_agent(profil.betrieb.user_agent_kontakt)
            if self.eigene_kennung else BROWSER_KENNUNG
        )
        self.robots = robots or RobotsPruefer(self.user_agent, profil.betrieb.timeout_sekunden)
        self.drossel = drossel or Drossel(profil.betrieb.request_pause_sekunden[0])
        self.session = self._session()
        self.braucht_playwright = bool(quellen_cfg.get("playwright", self.braucht_playwright))

    # ------------------------------------------------------------- Infrastruktur
    def _session(self) -> requests.Session:
        sitzung = requests.Session()
        wiederholung = Retry(
            total=self.profil.betrieb.max_retries,
            backoff_factor=2.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )
        sitzung.mount("https://", HTTPAdapter(max_retries=wiederholung))

        if self.eigene_kennung:
            kopfzeilen = {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9",
            }
        else:
            # Eine Browser-Kennung allein genügt nicht: Portale prüfen den
            # gesamten Kopfzeilensatz auf Stimmigkeit. Ein "Chrome", der
            # nebenbei application/json akzeptiert und keine Sec-Fetch-Felder
            # sendet, fällt sofort auf. Deshalb hier ein vollständiger,
            # in sich schlüssiger Satz statt einer Mischform.
            kopfzeilen = {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
                "Accept-Encoding": "gzip, deflate, br",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Connection": "keep-alive",
            }
        sitzung.headers.update(kopfzeilen)
        return sitzung

    def robots_erlaubt(self, url: str) -> bool:
        if not self.cfg.get("robots_pflicht", True):
            return True
        urteil = self.robots.pruefe(url)
        if urteil.crawl_delay:
            self.drossel.setze(url, urteil.crawl_delay)
        if not urteil.erlaubt:
            logger.warning("{}: {}", self.label, urteil.begruendung)
        return urteil.erlaubt

    def hole(self, url: str, **kwargs) -> str:
        """Holt eine Seite und gibt den HTML-Text zurück."""
        if not self.robots_erlaubt(url):
            raise QuelleBlockiert(f"robots.txt untersagt {url}")

        self.drossel.warte(url)
        unten, oben = self.profil.betrieb.request_pause_sekunden
        time.sleep(random.uniform(0, max(0.0, oben - unten)))

        if self.braucht_playwright:
            return self._hole_gerendert(url)

        antwort = self.session.get(url, timeout=self.profil.betrieb.timeout_sekunden, **kwargs)
        if antwort.status_code in (401, 403, 429):
            raise QuelleBlockiert(
                f"{self.label} antwortet mit HTTP {antwort.status_code} – Bot-Schutz aktiv"
            )
        antwort.raise_for_status()
        return antwort.text

    def hole_json(self, url: str, **kwargs) -> dict | list:
        if not self.robots_erlaubt(url):
            raise QuelleBlockiert(f"robots.txt untersagt {url}")
        self.drossel.warte(url)
        antwort = self.session.get(url, timeout=self.profil.betrieb.timeout_sekunden, **kwargs)
        if antwort.status_code in (401, 403, 429):
            raise QuelleBlockiert(f"{self.label}: HTTP {antwort.status_code}")
        antwort.raise_for_status()
        return antwort.json()

    def _hole_gerendert(self, url: str) -> str:
        """Rendert die Seite mit Playwright.

        Wird nur für Portale genutzt, die Ergebnisse per Javascript nachladen.
        Playwright ist eine optionale Abhängigkeit; fehlt es, wird die Quelle
        übersprungen statt den Lauf abzubrechen.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as fehler:  # pragma: no cover
            raise QuelleBlockiert(
                f"{self.label} benötigt Playwright. Installation: "
                "pip install playwright && playwright install chromium"
            ) from fehler

        try:
            pw_kontext = sync_playwright()
        except Exception as fehler:
            raise QuelleBlockiert(f"{self.label}: Playwright startet nicht ({fehler})") from fehler

        with pw_kontext as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as fehler:
                # Häufigster Fall: Paket installiert, Browser fehlt.
                raise QuelleBlockiert(
                    f"{self.label} braucht den Chromium-Browser von Playwright. "
                    "Einmalig ausführen:  playwright install chromium"
                ) from fehler
            try:
                seite = browser.new_page(user_agent=self.user_agent, locale="de-DE")
                seite.goto(url, wait_until="domcontentloaded", timeout=self.profil.betrieb.timeout_sekunden * 1000)
                seite.wait_for_timeout(2500)
                return seite.content()
            finally:
                browser.close()

    # ---------------------------------------------------------------- Vertrag
    @abstractmethod
    def suchseiten(self) -> Iterator[Suchseite]:
        """Liefert die abzurufenden Ergebnisseiten-URLs, aus dem Profil gebaut."""

    @abstractmethod
    def parse_seite(self, html: str, seite: Suchseite) -> list[Inserat]:
        """Wandelt eine Ergebnisseite in Inserate um."""

    # ------------------------------------------------------------- Ausführung
    def sammle(self) -> list[Inserat]:
        gefunden: list[Inserat] = []
        leere_seiten = 0
        # Portale ignorieren Seitenparameter gern still und liefern immer
        # Seite 1. Ohne diese Prüfung sammelt der Agent dieselben Inserate
        # mehrfach ein, und der Duplikatabgleich verschleiert es hinterher.
        gesehene_ids: set[str] = set()
        gesehene_ids: set[str] = set()

        for seite in self.suchseiten():
            try:
                html = self.hole(seite.url)
            except QuelleBlockiert:
                raise
            except requests.RequestException as fehler:
                logger.warning("{}: {} nicht abrufbar ({})", self.label, seite.url, fehler)
                break

            treffer = self.parse_seite(html, seite)
            logger.info("{}: {} Inserate auf {}", self.label, len(treffer), seite.beschriftung or seite.url)

            if not treffer:
                leere_seiten += 1
                if leere_seiten >= 2:
                    break                      # zwei leere Seiten = Ende der Trefferliste
                continue
            leere_seiten = 0

            # Liefert eine Folgeseite ausschließlich schon bekannte Anzeigen,
            # greift die Blätterung nicht – etwa weil der Seitenparameter
            # ignoriert wird. Weiterzublättern würde dieselben Daten
            # vervielfachen und die Dublettenerkennung unnötig belasten.
            neue = [i for i in treffer if i.externe_id not in gesehene_ids]
            if not neue:
                logger.warning(
                    "{}: '{}' wiederholt nur bereits gelesene Anzeigen – die "
                    "Blätterung greift nicht. Weitere Seiten werden übersprungen.",
                    self.label, seite.beschriftung or seite.url,
                )
                break

            gesehene_ids.update(i.externe_id for i in neue)
            gefunden.extend(neue)

        if not gefunden:
            logger.warning(
                "{}: keine Treffer. Entweder passt nichts, oder die Selektoren sind veraltet "
                "(siehe README, Abschnitt 'Wenn eine Quelle nichts liefert').",
                self.label,
            )
        return gefunden
