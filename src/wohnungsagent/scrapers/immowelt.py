"""immowelt.de und immonet.de.

Beide Portale gehören zur selben Gruppe und liefern seit dem Relaunch
strukturgleiche Seiten: die Trefferliste steckt in einem Next.js-Datenblock
(`__NEXT_DATA__`). Den auszulesen ist deutlich haltbarer als CSS-Selektoren,
weil sich Klassennamen bei jedem Deploy ändern, die Datenstruktur aber selten.

Immonet ist deshalb nur eine Unterklasse mit anderer Basis-URL.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup
from loguru import logger

from ..models.domain import Inserat
from ..services.parsing import euro
from .base import Scraper, Suchseite


class Immowelt(Scraper):
    name = "immowelt"
    label = "Immowelt"
    basis_url = "https://www.immowelt.de"
    braucht_playwright = True
    _such_pfad = "/suche/frankfurt-am-main/wohnungen/mieten"

    def suchseiten(self) -> Iterator[Suchseite]:
        budget = self.profil.budget
        wohnung = self.profil.wohnung
        params = (
            f"?d=true&sd=DESC&sf=TIMESTAMP"
            f"&pma={int(budget.warmmiete_max)}&pmi={int(budget.warmmiete_min * 0.7)}"
            f"&ami={int(wohnung.flaeche_hart)}"
            f"&rmi={wohnung.zimmer_min:g}&rma={wohnung.zimmer_max:g}"
        )
        for nummer in range(1, int(self.cfg.get("seiten", 2)) + 1):
            yield Suchseite(
                f"{self.basis_url}{self._such_pfad}{params}&sp={nummer}", f"Seite {nummer}"
            )

    def parse_seite(self, html: str, seite: Suchseite) -> list[Inserat]:
        suppe = BeautifulSoup(html, "lxml")
        block = suppe.find("script", id="__NEXT_DATA__")
        if block and block.string:
            try:
                objekte = list(_finde_objekte(json.loads(block.string)))
                if objekte:
                    return [self._aus_json(o) for o in objekte]
            except json.JSONDecodeError:
                logger.debug("{}: __NEXT_DATA__ nicht lesbar", self.label)

        return self._aus_markup(suppe)

    def _aus_json(self, objekt: dict[str, Any]) -> Inserat:
        preise = objekt.get("prices") or {}
        flaechen = objekt.get("areas") or objekt.get("area") or {}
        ort = objekt.get("place") or objekt.get("address") or {}
        koordinaten = ort.get("point") or ort.get("coordinates") or {}

        inserat = Inserat(
            quelle=self.name,
            externe_id=str(objekt.get("id") or objekt.get("onlineId") or ""),
            url=self._expose_url(objekt),
            titel=objekt.get("title") or objekt.get("headline") or "Ohne Titel",
            kaltmiete=_wert(preise.get("rentBase") or preise.get("basePrice")),
            nebenkosten=_wert(preise.get("serviceCharge")),
            warmmiete=_wert(preise.get("rentTotal") or preise.get("totalPrice")),
            zimmer=_wert(objekt.get("roomsMin") or objekt.get("numberOfRooms") or objekt.get("rooms")),
            flaeche=_wert(flaechen.get("livingArea") or objekt.get("livingSpace")),
            adresse=" ".join(filter(None, [ort.get("street"), ort.get("houseNumber")])) or None,
            stadtteil=ort.get("district") or ort.get("quarter"),
            plz=str(ort.get("postcode") or ort.get("zipCode") or "") or None,
            anbieter=(objekt.get("broker") or objekt.get("company") or {}).get("name")
            if isinstance(objekt.get("broker") or objekt.get("company"), dict) else None,
            beschreibung=objekt.get("description") or objekt.get("teaser") or "",
            bilder=[b.get("url") for b in (objekt.get("pictures") or []) if isinstance(b, dict) and b.get("url")],
        )
        if koordinaten.get("lat"):
            inserat.geo.lat = float(koordinaten["lat"])
            inserat.geo.lon = float(koordinaten.get("lon") or koordinaten.get("lng"))
            inserat.geo.quelle = "portal"
            inserat.geo.genauigkeit_m = 150
        return inserat

    def _expose_url(self, objekt: dict) -> str:
        kennung = objekt.get("id") or objekt.get("onlineId")
        return f"{self.basis_url}/expose/{kennung}"

    def _aus_markup(self, suppe: BeautifulSoup) -> list[Inserat]:
        """Rückfallebene, falls der Datenblock fehlt.

        Bewusst ohne Klassennamen: Immowelt vergibt sie generiert neu und sie
        halten keinen Deploy lang. Stattdessen dient der Exposé-Link als Anker;
        von dort geht es so weit nach oben, bis ein Container genug Text für
        Preis, Fläche und Zimmerzahl enthält. Ausgelesen wird dann über
        deutsche Sprachmuster, die sich seltener ändern als das Markup.
        """
        ergebnis: list[Inserat] = []
        gesehen: set[str] = set()

        for link in suppe.select("a[href*='/expose/']"):
            href = link.get("href") or ""
            kennung = href.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            if not kennung or kennung in gesehen:
                continue
            gesehen.add(kennung)

            karte = _karte_um(link)
            volltext = " ".join(karte.get_text(" ", strip=True).split())

            titel = ""
            for kandidat in (karte.select_one("h2"), karte.select_one("h3"), link):
                if kandidat and (text := kandidat.get_text(" ", strip=True)) and len(text) > 8:
                    titel = text[:200]
                    break

            ergebnis.append(
                Inserat(
                    quelle=self.name,
                    externe_id=kennung,
                    url=href if href.startswith("http") else self.basis_url + href,
                    titel=titel or "Ohne Titel",
                    warmmiete=_muster(volltext, r"([\d.,]+)\s*€"),
                    flaeche=_muster(volltext, r"([\d.,]+)\s*m²"),
                    zimmer=_muster(volltext, r"([\d,]+)\s*(?:Zimmer|Zi\.)"),
                    beschreibung=volltext[:800],
                )
            )
        return ergebnis


class Immonet(Immowelt):
    name = "immonet"
    label = "Immonet"
    basis_url = "https://www.immonet.de"
    _such_pfad = "/suche/frankfurt-am-main/wohnungen/mieten"


def _karte_um(link, hoechstens: int = 5):
    """Steigt vom Link so weit auf, bis der Container genug Inhalt hat."""
    knoten = link
    for _ in range(hoechstens):
        eltern = knoten.parent
        if eltern is None or eltern.name in ("body", "html", "[document]"):
            break
        knoten = eltern
        text = knoten.get_text(" ", strip=True)
        if "€" in text and ("m²" in text or "Zimmer" in text):
            break
    return knoten


def _muster(text: str, regex: str) -> float | None:
    import re

    treffer = re.search(regex, text)
    return euro(treffer.group(1)) if treffer else None


def _wert(roh: Any) -> float | None:
    """Preisfelder kommen mal als Zahl, mal als {'amount': ..., 'currency': ...}."""
    if roh is None:
        return None
    if isinstance(roh, dict):
        roh = roh.get("amount") or roh.get("value") or roh.get("min")
    if isinstance(roh, (int, float)):
        return float(roh)
    if isinstance(roh, str):
        return euro(roh)
    return None


def _finde_objekte(daten: Any, tiefe: int = 0):
    """Sucht rekursiv nach Listen von Exposé-Objekten im Next.js-Datenblock.

    Erkennungsmerkmal: ein Dict mit einer ID und mindestens einem
    wohnungstypischen Feld. Robuster als ein fester Pfad, weil sich die
    Verschachtelung zwischen Deploys verschiebt.
    """
    if tiefe > 10:
        return
    if isinstance(daten, dict):
        kennung = daten.get("id") or daten.get("onlineId")
        merkmale = {"prices", "areas", "livingSpace", "numberOfRooms", "roomsMin", "rooms"}
        if kennung and merkmale & set(daten):
            yield daten
            return
        for wert in daten.values():
            yield from _finde_objekte(wert, tiefe + 1)
    elif isinstance(daten, list):
        for eintrag in daten:
            yield from _finde_objekte(eintrag, tiefe + 1)
