"""Vermieter mit eigenem Bestand und kleinere Portale.

Diese Quellen haben eines gemeinsam: sie sind einzeln klein, technisch simpel
und ändern sich selten. Für jede ein eigenes Modul zu schreiben wäre viel
Wiederholung. Stattdessen beschreibt `QuellenBauplan` deklarativ, wo die
Daten liegen; die Auswertung teilen sich alle.

Drei Auswertungsarten werden unterstützt:

  json_api    – der Anbieter liefert JSON (Vonovia, teils GWH)
  json_ld     – die Seite trägt schema.org-Daten (viele TYPO3/WordPress-Seiten)
  css         – klassische Trefferliste über CSS-Selektoren

Eine neue Genossenschaft oder ein lokaler Makler ist damit ein Eintrag in
`BAUPLAENE`, kein neues Modul.

Sonderfall ABG: Deutschlands größter kommunaler Vermieter in Frankfurt
inseriert überhaupt nicht, sondern vergibt über Interessentenliste und
Losverfahren. Der "Scraper" liefert deshalb keine Treffer, sondern einmal pro
Lauf eine Erinnerung im Log. Ein Scraper, der so tut, als könnte er dort
suchen, wäre irreführend.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from ..models.domain import Inserat
from ..services.parsing import dezimal, euro, plz
from .base import Scraper, Suchseite


@dataclass(frozen=True, slots=True)
class QuellenBauplan:
    name: str
    label: str
    art: str                                   # "json_api" | "json_ld" | "css"
    urls: tuple[str, ...]
    # nur für art="css"
    karte: str = ""
    titel: str = ""
    link: str = ""
    preis: str = ""
    flaeche: str = ""
    zimmer: str = ""
    ort: str = ""
    # nur für art="json_api"
    pfad_zur_liste: tuple[str, ...] = ()
    feldnamen: dict[str, str] = field(default_factory=dict)
    hinweis: str = ""


BAUPLAENE: dict[str, QuellenBauplan] = {
    "vonovia": QuellenBauplan(
        name="vonovia",
        label="Vonovia",
        art="json_api",
        urls=(
            "https://www.vonovia.de/api/immosuche/search"
            "?city=Frankfurt%20am%20Main&radius=5&type=APARTMENT_RENT&size=60",
        ),
        pfad_zur_liste=("results", "items"),
        feldnamen={
            "id": "id", "titel": "title", "url": "url", "kaltmiete": "baseRent",
            "warmmiete": "totalRent", "flaeche": "livingSpace", "zimmer": "numberOfRooms",
            "stadtteil": "district", "plz": "zipCode", "adresse": "street",
            "beschreibung": "description", "verfuegbar": "availableFrom",
        },
    ),
    "gwh": QuellenBauplan(
        name="gwh",
        label="GWH Wohnungsgesellschaft",
        art="json_ld",
        urls=("https://www.gwh.de/wohnungssuche/?tx_gwhimmo_search[city]=Frankfurt am Main",),
    ),
    "nassauische_heimstaette": QuellenBauplan(
        name="nassauische_heimstaette",
        label="Nassauische Heimstätte",
        art="json_ld",
        urls=("https://www.naheimst.de/wohnen/wohnungssuche/",),
    ),
    "leg": QuellenBauplan(
        name="leg",
        label="LEG Immobilien",
        art="json_ld",
        urls=("https://www.leg-wohnen.de/mieten/wohnungssuche/",),
    ),
    "adler": QuellenBauplan(
        name="adler",
        label="Adler Group",
        art="json_ld",
        urls=("https://www.adler-group.com/wohnungssuche",),
    ),
    "wohnungsboerse": QuellenBauplan(
        name="wohnungsboerse",
        label="Wohnungsboerse",
        art="css",
        urls=("https://www.wohnungsboerse.net/mietwohnungen/Frankfurt-am-Main",),
        karte="div.estate-item, article.estate, div[class*='result-item']",
        titel="h2, h3, .estate-title",
        link="a[href]",
        preis="[class*='price'], .estate-price",
        flaeche="[class*='area'], [class*='size']",
        zimmer="[class*='room']",
        ort="[class*='location'], [class*='address']",
    ),
    "wohnung_jetzt": QuellenBauplan(
        name="wohnung_jetzt",
        label="Wohnung-jetzt",
        art="css",
        urls=("https://www.wohnung-jetzt.de/mietwohnungen/frankfurt-am-main",),
        karte="article, .listing-item, div[class*='object']",
        titel="h2, h3",
        link="a[href]",
        preis="[class*='price'], [class*='miete']",
        flaeche="[class*='flaeche'], [class*='area']",
        zimmer="[class*='zimmer'], [class*='room']",
        ort="[class*='ort'], [class*='location']",
    ),
    "abg": QuellenBauplan(
        name="abg",
        label="ABG Frankfurt Holding",
        art="hinweis",
        urls=(),
        hinweis=(
            "Die ABG (rund 54.000 Wohnungen in Frankfurt, häufig deutlich unter "
            "Marktmiete) inseriert keine Einzelwohnungen. Die Vergabe läuft über die "
            "Interessentenliste und ein Losverfahren: "
            "https://www.abg.de/mieten/faire-wohnungsvergabe/interessentenformular/ – "
            "einmalig ausfüllen, dann kommen die Angebote per E-Mail. Für den "
            "Einzugstermin Januar 2027 solltest du dort spätestens im Herbst 2026 "
            "eingetragen sein."
        ),
    ),
}


class BauplanScraper(Scraper):
    """Führt einen `QuellenBauplan` aus."""

    def __init__(self, bauplan: QuellenBauplan, *args, **kwargs) -> None:
        self.bauplan = bauplan
        self.name = bauplan.name
        self.label = bauplan.label
        self.basis_url = bauplan.urls[0] if bauplan.urls else ""
        super().__init__(*args, **kwargs)

    def suchseiten(self) -> Iterator[Suchseite]:
        for url in self.bauplan.urls:
            yield Suchseite(url, urlparse(url).netloc)

    def sammle(self) -> list[Inserat]:
        if self.bauplan.art == "hinweis":
            logger.info("{}: {}", self.label, self.bauplan.hinweis)
            return []
        if self.bauplan.art == "json_api":
            return self._json_api()
        return super().sammle()

    # ------------------------------------------------------------- json_api
    def _json_api(self) -> list[Inserat]:
        ergebnis: list[Inserat] = []
        for url in self.bauplan.urls:
            daten: Any = self.hole_json(url)
            for schritt in self.bauplan.pfad_zur_liste:
                if isinstance(daten, dict):
                    daten = daten.get(schritt, [])
            if not isinstance(daten, list):
                logger.warning("{}: unerwartete API-Struktur", self.label)
                continue
            felder = self.bauplan.feldnamen
            for eintrag in daten:
                if not isinstance(eintrag, dict):
                    continue
                roh_url = str(eintrag.get(felder.get("url", "url"), ""))
                ergebnis.append(
                    Inserat(
                        quelle=self.name,
                        externe_id=str(eintrag.get(felder.get("id", "id"), "")),
                        url=urljoin(url, roh_url) if roh_url else url,
                        titel=str(eintrag.get(felder.get("titel", "title"), "Ohne Titel")),
                        kaltmiete=_zahl(eintrag.get(felder.get("kaltmiete", ""))),
                        warmmiete=_zahl(eintrag.get(felder.get("warmmiete", ""))),
                        flaeche=_zahl(eintrag.get(felder.get("flaeche", ""))),
                        zimmer=_zahl(eintrag.get(felder.get("zimmer", ""))),
                        stadtteil=_text(eintrag.get(felder.get("stadtteil", ""))),
                        plz=_text(eintrag.get(felder.get("plz", ""))),
                        adresse=_text(eintrag.get(felder.get("adresse", ""))),
                        beschreibung=_text(eintrag.get(felder.get("beschreibung", ""))) or "",
                        anbieter=self.label,
                        inseriert_rohtext=_text(eintrag.get(felder.get("verfuegbar", ""))),
                    )
                )
        return ergebnis

    # -------------------------------------------------------- json_ld / css
    def parse_seite(self, html: str, seite: Suchseite) -> list[Inserat]:
        suppe = BeautifulSoup(html, "lxml")
        if self.bauplan.art == "json_ld":
            treffer = self._json_ld(suppe, seite.url)
            if treffer:
                return treffer
            logger.debug("{}: kein JSON-LD gefunden, versuche CSS", self.label)
        return self._css(suppe, seite.url)

    def _json_ld(self, suppe: BeautifulSoup, basis: str) -> list[Inserat]:
        relevante = {"apartment", "house", "residence", "accommodation", "offer", "product"}
        ergebnis: list[Inserat] = []
        for block in suppe.find_all("script", type="application/ld+json"):
            try:
                daten = json.loads(block.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            for knoten in _flach(daten):
                typ = knoten.get("@type", "")
                typen = {typ.lower()} if isinstance(typ, str) else {t.lower() for t in typ}
                if not typen & relevante:
                    continue
                name = knoten.get("name") or knoten.get("headline")
                if not name:
                    continue
                url = urljoin(basis, knoten.get("url") or basis)
                angebot = knoten.get("offers") or {}
                if isinstance(angebot, list):
                    angebot = angebot[0] if angebot else {}
                adresse = knoten.get("address") or {}
                if isinstance(adresse, str):
                    ort_text, postleitzahl = adresse, plz(adresse)
                else:
                    ort_text = adresse.get("addressLocality") or adresse.get("streetAddress")
                    postleitzahl = str(adresse.get("postalCode") or "") or None
                groesse = knoten.get("floorSize") or {}
                bild = knoten.get("image")
                if isinstance(bild, list):
                    bild = bild[0] if bild else None
                if isinstance(bild, dict):
                    bild = bild.get("url")

                ergebnis.append(
                    Inserat(
                        quelle=self.name,
                        externe_id=hashlib.sha1(url.encode()).hexdigest()[:16],
                        url=url,
                        titel=str(name),
                        kaltmiete=_zahl(angebot.get("price") or knoten.get("price")),
                        flaeche=dezimal(str(groesse.get("value") if isinstance(groesse, dict) else groesse)),
                        zimmer=_zahl(knoten.get("numberOfRooms")),
                        adresse=ort_text,
                        stadtteil=ort_text,
                        plz=postleitzahl,
                        beschreibung=str(knoten.get("description", ""))[:800],
                        bilder=[bild] if isinstance(bild, str) else [],
                        anbieter=self.label,
                    )
                )
        return ergebnis

    def _css(self, suppe: BeautifulSoup, basis: str) -> list[Inserat]:
        bp = self.bauplan
        if not bp.karte:
            return []
        ergebnis: list[Inserat] = []
        for karte in suppe.select(bp.karte):
            link = karte.select_one(bp.link) if bp.link else None
            if not (link and link.get("href")):
                continue
            url = urljoin(basis, link["href"])
            titel_el = karte.select_one(bp.titel) if bp.titel else None
            ergebnis.append(
                Inserat(
                    quelle=self.name,
                    externe_id=hashlib.sha1(url.encode()).hexdigest()[:16],
                    url=url,
                    titel=(titel_el or link).get_text(" ", strip=True)[:200] or "Ohne Titel",
                    warmmiete=euro(_text_von(karte, bp.preis)),
                    flaeche=dezimal(_text_von(karte, bp.flaeche)),
                    zimmer=dezimal(_text_von(karte, bp.zimmer)),
                    stadtteil=_text_von(karte, bp.ort),
                    beschreibung=karte.get_text(" ", strip=True)[:800],
                    anbieter=self.label,
                )
            )
        return ergebnis


# ------------------------------------------------------------------- Helfer

def _text_von(knoten, selektor: str) -> str | None:
    if not selektor:
        return None
    treffer = knoten.select_one(selektor)
    return treffer.get_text(" ", strip=True) if treffer else None


def _zahl(wert: Any) -> float | None:
    if wert is None:
        return None
    if isinstance(wert, (int, float)):
        return float(wert)
    return euro(str(wert))


def _text(wert: Any) -> str | None:
    if wert is None:
        return None
    return str(wert).strip() or None


def _flach(daten: Any) -> list[dict]:
    raus: list[dict] = []
    if isinstance(daten, list):
        for eintrag in daten:
            raus.extend(_flach(eintrag))
    elif isinstance(daten, dict):
        raus.append(daten)
        for schluessel in ("@graph", "itemListElement", "item", "offers", "mainEntity"):
            if schluessel in daten:
                raus.extend(_flach(daten[schluessel]))
    return raus
