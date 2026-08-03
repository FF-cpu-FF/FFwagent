"""kleinanzeigen.de – Mietwohnungen.

Höchster Anteil privater Inserate aller Priorität-A-Quellen und damit die
Quelle, die beim Kriterium "privater Vermieter" am meisten liefert.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup
from loguru import logger

from ..models.domain import Inserat
from ..services.parsing import euro
from .base import Scraper, Suchseite

BASIS = "https://www.kleinanzeigen.de"

# "3 Zi.", "2,5 Zimmer", "1 Zi" – aber nicht die Hausnummer in "Zimmerstr. 4"
_ZIMMER = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*Zi(?:\.|mmer)?\b", re.I)
# "75 m²", "67,8 m2", "80 qm"
_FLAECHE = re.compile(r"(\d{1,4}(?:[.,]\d+)?)\s*(?:m²|m2|qm)\b", re.I)


def _erstes_muster(muster: re.Pattern[str], text: str) -> float | None:
    treffer = muster.search(text)
    if not treffer:
        return None
    try:
        wert = float(treffer.group(1).replace(",", "."))
    except ValueError:
        return None
    return wert if 0 < wert < 2000 else None


class Kleinanzeigen(Scraper):
    name = "kleinanzeigen"
    label = "Kleinanzeigen"
    basis_url = BASIS

    def urlvarianten(self) -> list[tuple[str, str]]:
        """URL-Formen von streng nach locker.

        Kleinanzeigen quittiert eine unpassende Kombination aus Ort, Kategorie
        und Filtersegmenten mit HTTP 200 und einer leeren Liste – man sieht
        also nicht, welches Segment stört. Statt darauf zu wetten, welche
        Form gerade gilt, probiert der Scraper sie der Reihe nach durch und
        behält die erste, die Inserate liefert. Welche das war, steht im Log;
        sie lässt sich dann in `variante` festschreiben.
        """
        budget, wohnung = self.profil.budget, self.profil.wohnung
        ort = self.cfg.get("ort_slug", "frankfurt-am-main")
        code = f"{self.cfg.get('kategorie', 'c203')}{self.cfg.get('ort_code', 'l4292')}"

        kalt_min = int(budget.warmmiete_min / budget.warmmiete_schaetzfaktor * 0.75)
        kalt_max = int(budget.warmmiete_max / budget.warmmiete_schaetzfaktor * 1.1)
        attribute = (
            f"+wohnung_mieten.qm_d:{int(wohnung.flaeche_hart)},400"
            f"+wohnung_mieten.zimmer_d:{math.floor(wohnung.zimmer_min)},"
            f"{math.ceil(wohnung.zimmer_max)}"
        )

        return [
            ("vollstaendig", f"{BASIS}/s-wohnung-mieten/{ort}/anzeige:angebote/"
                             f"preis:{kalt_min}:{kalt_max}/{code}{attribute}"),
            ("ohne_attribute", f"{BASIS}/s-wohnung-mieten/{ort}/anzeige:angebote/"
                               f"preis:{kalt_min}:{kalt_max}/{code}"),
            ("nur_preis", f"{BASIS}/s-wohnung-mieten/{ort}/preis:{kalt_min}:{kalt_max}/{code}"),
            ("nur_ort", f"{BASIS}/s-wohnung-mieten/{ort}/{code}"),
            ("mit_k0", f"{BASIS}/s-wohnung-mieten/{ort}/wohnung-mieten/k0{code}"),
        ]

    def _seitenurl(self, basis_url: str, nummer: int) -> str:
        """Fügt das Seitensegment vor dem Kategoriecode ein."""
        if nummer <= 1:
            return basis_url
        teile = basis_url.rsplit("/", 1)
        return f"{teile[0]}/seite:{nummer}/{teile[1]}"

    def sammle(self) -> list[Inserat]:
        varianten = self.urlvarianten()
        gewuenscht = self.cfg.get("variante")
        if gewuenscht:
            varianten = [(n, u) for n, u in varianten if n == gewuenscht] or varianten

        for name, basis_url in varianten:
            self._aktive_url = basis_url
            gefunden = super().sammle()
            if gefunden:
                if name != varianten[0][0]:
                    logger.info(
                        "{}: URL-Variante '{}' liefert Ergebnisse. Zum Festschreiben "
                        "in config/suchprofil.yml unter kleinanzeigen eintragen: variante: {}",
                        self.label, name, name,
                    )
                return gefunden
            logger.debug("{}: Variante '{}' ohne Treffer, probiere nächste", self.label, name)

        logger.warning(
            "{}: keine der {} URL-Formen liefert Inserate. Das deutet auf eine "
            "Sperre oder geändertes Markup hin – prüfen mit: "
            "wohnungsagent diagnose kleinanzeigen --browser-ua",
            self.label, len(varianten),
        )
        return []

    def suchseiten(self) -> Iterator[Suchseite]:
        basis_url = getattr(self, "_aktive_url", None) or self.urlvarianten()[0][1]
        for nummer in range(1, int(self.cfg.get("seiten", 3)) + 1):
            yield Suchseite(self._seitenurl(basis_url, nummer), f"Seite {nummer}")

    def _alte_suchseiten(self) -> Iterator[Suchseite]:
        # Vorgängerfassung, nur noch zur Nachvollziehbarkeit. Ersetzt durch
        # urlvarianten() – siehe dort.
        budget = self.profil.budget
        wohnung = self.profil.wohnung
        kalt_min = int(budget.warmmiete_min / budget.warmmiete_schaetzfaktor * 0.75)
        kalt_max = int(budget.warmmiete_max / budget.warmmiete_schaetzfaktor * 1.1)

        attribute = f"{self.cfg.get('kategorie', 'c203')}{self.cfg.get('ort_code', 'l6018')}"
        attribute += f"+wohnung_mieten.qm_d:{int(wohnung.flaeche_hart)},400"
        # Komma trennt hier min/max – halbe Zimmer würden die URL zerreißen.
        attribute += (
            f"+wohnung_mieten.zimmer_d:{math.floor(wohnung.zimmer_min)},"
            f"{math.ceil(wohnung.zimmer_max)}"
        )

        for nummer in range(1, int(self.cfg.get("seiten", 3)) + 1):
            teile = [BASIS, "s-wohnung-mieten",
                     self.cfg.get("ort_slug", "frankfurt-am-main"),
                     "anzeige:angebote", f"preis:{kalt_min}:{kalt_max}"]
            if nummer > 1:
                teile.append(f"seite:{nummer}")
            teile.append(attribute)
            yield Suchseite("/".join(teile), f"Seite {nummer}")

    def parse_seite(self, html: str, seite: Suchseite) -> list[Inserat]:
        suppe = BeautifulSoup(html, "lxml")
        ergebnis: list[Inserat] = []

        for eintrag in suppe.select("article.aditem"):
            ad_id = eintrag.get("data-adid")
            pfad = eintrag.get("data-href")
            if not (ad_id and pfad):
                continue

            titel_el = eintrag.select_one("a.ellipsis")
            preis_el = eintrag.select_one(
                "p.aditem-main--middle--price-shipping--price, .aditem-main--middle--price"
            )
            ort_el = eintrag.select_one("div.aditem-main--top--left")
            datum_el = eintrag.select_one("div.aditem-main--top--right")
            text_el = eintrag.select_one("p.aditem-main--middle--description")

            merkmale = [t.get_text(strip=True) for t in eintrag.select("span.simpletag")]

            # Zimmerzahl und Fläche aus dem gesamten Kartentext lesen statt
            # aus einer bestimmten CSS-Klasse. Die Klassennamen ändern sich
            # bei jedem Deploy, die Schreibweise "3 Zi." und "75 m²" nicht.
            kartentext = eintrag.get_text(" ", strip=True)
            zimmer = _erstes_muster(_ZIMMER, kartentext)
            flaeche = _erstes_muster(_FLAECHE, kartentext)

            bilder = []
            for bild in eintrag.select("div.imagebox img, .aditem-image img"):
                quelle = bild.get("src") or bild.get("data-imgsrc")
                if quelle:
                    bilder.append(quelle)

            ort = " ".join(ort_el.get_text(strip=True).split()) if ort_el else None
            ergebnis.append(
                Inserat(
                    quelle=self.name,
                    externe_id=str(ad_id),
                    url=pfad if pfad.startswith("http") else BASIS + pfad,
                    titel=titel_el.get_text(strip=True) if titel_el else "Ohne Titel",
                    kaltmiete=euro(preis_el.get_text() if preis_el else None),
                    zimmer=zimmer,
                    flaeche=flaeche,
                    adresse=ort,
                    stadtteil=_stadtteil_aus(ort),
                    beschreibung=text_el.get_text(" ", strip=True) if text_el else "",
                    bilder=bilder,
                    merkmale=merkmale,
                    inseriert_rohtext=datum_el.get_text(strip=True) if datum_el else None,
                )
            )
        return ergebnis


def _stadtteil_aus(ort: str | None) -> str | None:
    """'60316 Frankfurt am Main - Bornheim' -> 'Bornheim'"""
    if not ort:
        return None
    if " - " in ort:
        return ort.rsplit(" - ", 1)[-1].strip()
    return None
