"""immobilienscout24.de.

Wichtig vorweg – bitte vor dem ersten Lauf lesen:

ImmoScout24 untersagt in seiner robots.txt die Suchergebnispfade (`/Suche/`)
und setzt zusätzlich einen aktiven Bot-Schutz ein. Weil das Suchprofil
"robots.txt respektieren" verlangt, deaktiviert sich diese Quelle bei
`robots_pflicht: true` selbst und meldet das im Lauf. Das ist beabsichtigt
und kein Defekt.

Es bleiben zwei saubere Wege:

  1. Suchagent im eigenen ImmoScout-Konto anlegen und die
     Benachrichtigungsmails an eine Adresse leiten, die der Agent per IMAP
     liest. Der Parser dafür ist in `services/postfach.py` vorbereitet.
  2. Offiziellen API-Zugang beantragen (ImmoScout24 Partner-API). Liegt ein
     Schlüssel in `IS24_API_KEY` vor, nutzt dieses Modul die API statt HTML
     und ist von robots.txt gar nicht betroffen.

Der HTML-Parser bleibt implementiert, damit Weg 3 – bewusst `robots_pflicht:
false` setzen – technisch funktioniert. Diese Entscheidung liegt beim
Betreiber der Instanz, nicht beim Code.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup
from loguru import logger

from ..models.domain import Inserat
from ..services.parsing import dezimal, euro
from .base import Scraper, Suchseite

BASIS = "https://www.immobilienscout24.de"
API = "https://rest.immobilienscout24.de/restapi/api/search/v1.0/search/region"


class ImmobilienScout24(Scraper):
    name = "immobilienscout24"
    label = "ImmobilienScout24"
    basis_url = BASIS

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.api_key = os.getenv("IS24_API_KEY")

    def suchseiten(self) -> Iterator[Suchseite]:
        budget = self.profil.budget
        wohnung = self.profil.wohnung
        preis = f"{int(budget.warmmiete_min * 0.7)}-{int(budget.warmmiete_max)}"
        flaeche = f"{int(wohnung.flaeche_hart)}-"
        zimmer = f"{wohnung.zimmer_min:g}-{wohnung.zimmer_max:g}"

        for nummer in range(1, int(self.cfg.get("seiten", 2)) + 1):
            yield Suchseite(
                f"{BASIS}/Suche/de/hessen/frankfurt-am-main/wohnung-mieten"
                f"?price={preis}&livingspace={flaeche}&numberofrooms={zimmer}"
                f"&sorting=2&pagenumber={nummer}",
                f"Seite {nummer}",
            )

    def sammle(self) -> list[Inserat]:
        if self.api_key:
            logger.info("{}: nutze offizielle API", self.label)
            return self._ueber_api()
        return super().sammle()

    def _ueber_api(self) -> list[Inserat]:
        """Partner-API. Nur aktiv, wenn IS24_API_KEY gesetzt ist."""
        antwort = self.hole_json(
            API,
            params={
                "realestatetype": "apartmentrent",
                "geocodes": "1276003001",              # Frankfurt am Main
                "price": f"{int(self.profil.budget.warmmiete_min * 0.7)}-"
                         f"{int(self.profil.budget.warmmiete_max)}",
                "livingspace": f"{int(self.profil.wohnung.flaeche_hart)}-",
                "numberofrooms": f"{self.profil.wohnung.zimmer_min:g}-{self.profil.wohnung.zimmer_max:g}",
                "pagesize": 50,
            },
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        eintraege = (
            antwort.get("resultlist.resultlist", {})
            .get("resultlistEntries", [{}])[0]
            .get("resultlistEntry", [])
        )
        return [self._aus_api(e) for e in eintraege if isinstance(e, dict)]

    def _aus_api(self, eintrag: dict) -> Inserat:
        objekt = eintrag.get("resultlist.realEstate", {})
        adresse = objekt.get("address", {})
        koordinaten = adresse.get("wgs84Coordinate", {})
        inserat = Inserat(
            quelle=self.name,
            externe_id=str(eintrag.get("@id") or objekt.get("@id", "")),
            url=f"{BASIS}/expose/{eintrag.get('@id')}",
            titel=objekt.get("title", "Ohne Titel"),
            kaltmiete=_zahl(objekt.get("price", {}).get("value")),
            warmmiete=_zahl(objekt.get("calculatedTotalRent", {}).get("totalRent", {}).get("value")),
            zimmer=_zahl(objekt.get("numberOfRooms")),
            flaeche=_zahl(objekt.get("livingSpace")),
            adresse=" ".join(filter(None, [adresse.get("street"), adresse.get("houseNumber")])) or None,
            stadtteil=adresse.get("quarter"),
            plz=adresse.get("postcode"),
            anbieter=(objekt.get("contactDetails") or {}).get("company"),
            beschreibung=objekt.get("description", "") or "",
        )
        if koordinaten.get("latitude"):
            inserat.geo.lat = float(koordinaten["latitude"])
            inserat.geo.lon = float(koordinaten["longitude"])
            inserat.geo.quelle = "portal"
            inserat.geo.genauigkeit_m = 100
        return inserat

    def parse_seite(self, html: str, seite: Suchseite) -> list[Inserat]:
        """Die Ergebnisse liegen in einem eingebetteten JSON-Block, nicht im
        gerenderten Markup – das ist stabiler als CSS-Selektoren."""
        treffer = re.search(r"resultListModel\s*[:=]\s*(\{.*?\})\s*[,;]\s*\n", html, re.S)
        if treffer:
            try:
                daten = json.loads(treffer.group(1))
                eintraege = (
                    daten.get("searchResponseModel", {})
                    .get("resultlist.resultlist", {})
                    .get("resultlistEntries", [{}])[0]
                    .get("resultlistEntry", [])
                )
                return [self._aus_api(e) for e in eintraege if isinstance(e, dict)]
            except (json.JSONDecodeError, KeyError, IndexError):
                logger.debug("{}: eingebettetes JSON nicht lesbar, weiche auf HTML aus", self.label)

        suppe = BeautifulSoup(html, "lxml")
        ergebnis: list[Inserat] = []
        for karte in suppe.select("li[data-id], article[data-obid]"):
            objekt_id = karte.get("data-id") or karte.get("data-obid")
            link = karte.select_one("a[href*='/expose/']")
            if not (objekt_id and link):
                continue
            titel = karte.select_one("h2, .result-list-entry__brand-title")
            werte = [w.get_text(" ", strip=True) for w in karte.select("dd, .result-list-entry__primary-criterion dd")]
            ergebnis.append(
                Inserat(
                    quelle=self.name,
                    externe_id=str(objekt_id),
                    url=link["href"] if link["href"].startswith("http") else BASIS + link["href"],
                    titel=titel.get_text(" ", strip=True) if titel else "Ohne Titel",
                    warmmiete=euro(werte[0]) if werte else None,
                    flaeche=dezimal(werte[1]) if len(werte) > 1 else None,
                    zimmer=dezimal(werte[2]) if len(werte) > 2 else None,
                    beschreibung=karte.get_text(" ", strip=True)[:600],
                )
            )
        return ergebnis


def _zahl(wert) -> float | None:
    try:
        return float(wert) if wert is not None else None
    except (TypeError, ValueError):
        return None
