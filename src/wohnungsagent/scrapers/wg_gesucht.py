"""wg-gesucht.de.

Laut Suchprofil auch für komplette Wohnungen, nicht nur WG-Zimmer. Die
Kategorien werden über `typen` gesteuert: 0 = WG-Zimmer, 1 = 1-Zimmer-Wohnung,
2 = Wohnung, 3 = Haus. Standard sind 2 und 1.

Eigenheit der Quelle: WG-Gesucht nennt fast immer ein konkretes Datum unter
"frei ab", teils auch "frei bis". Ein "frei bis" ist ein starkes Signal für
eine Zwischenmiete – das Stichwort steht ohnehin auf der Ausschlussliste,
wird hier aber zusätzlich als Merkmal übernommen.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from ..models.domain import Inserat
from ..services.parsing import dezimal, euro
from .base import Scraper, Suchseite

BASIS = "https://www.wg-gesucht.de"
TYP_PFAD = {
    0: "wg-zimmer",
    1: "1-zimmer-wohnungen",
    2: "wohnungen",
    3: "haeuser",
}


class WgGesucht(Scraper):
    name = "wg_gesucht"
    label = "WG-Gesucht"
    basis_url = BASIS

    def suchseiten(self) -> Iterator[Suchseite]:
        city_id = self.cfg.get("city_id", 41)
        budget = self.profil.budget
        wohnung = self.profil.wohnung

        for typ in self.cfg.get("typen", [2]):
            for nummer in range(1, int(self.cfg.get("seiten", 2)) + 1):
                pfad = f"{TYP_PFAD.get(typ, 'wohnungen')}-in-Frankfurt-am-Main.{city_id}.{typ}.1.{nummer - 1}.html"
                params = "&".join([
                    "offer_filter=1",
                    f"city_id={city_id}",
                    "noDeact=1",
                    "sort_order=0",
                    f"categories%5B%5D={typ}",
                    f"rMax={int(budget.warmmiete_max)}",
                    f"sMin={int(wohnung.flaeche_hart)}",
                ])
                yield Suchseite(f"{BASIS}/{pfad}?{params}", f"Typ {typ}, Seite {nummer}")

    def parse_seite(self, html: str, seite: Suchseite) -> list[Inserat]:
        suppe = BeautifulSoup(html, "lxml")
        ergebnis: list[Inserat] = []

        for karte in suppe.select("div.wgg_card.offer_list_item, div.offer_list_item"):
            link = karte.select_one("h3.truncate_title a, a.detailansicht")
            if not (link and link.get("href")):
                continue
            href = link["href"]
            url = href if href.startswith("http") else f"{BASIS}/{href.lstrip('/')}"
            kennung = re.search(r"\.(\d+)\.html", url)
            if not kennung:
                continue

            volltext = karte.get_text(" ", strip=True)

            preis_el = karte.select_one("b.noprint, .card_price b, .detail-size-price-wrapper b")
            warmmiete = euro(preis_el.get_text() if preis_el else None)

            flaeche = None
            if (qm := re.search(r"(\d+)\s*m²", volltext)):
                flaeche = float(qm.group(1))

            zimmer = None
            if (zi := re.search(r"(\d+(?:[.,]\d)?)\s*Zimmer", volltext)):
                zimmer = dezimal(zi.group(1))
            elif "1-zimmer" in url.lower():
                zimmer = 1.0

            stadtteil = None
            if (ort := re.search(r"Frankfurt am Main\s+([A-ZÄÖÜ][\wäöüß\- ]{2,30})", volltext)):
                stadtteil = ort.group(1).strip(" ,|")

            merkmale = []
            if re.search(r"frei bis", volltext, re.I):
                merkmale.append("befristet (frei bis)")

            bild_el = karte.select_one("img.card_image, .card_image img")
            bilder = []
            if bild_el:
                quelle = bild_el.get("src") or bild_el.get("data-original")
                if quelle:
                    bilder.append(quelle)

            ergebnis.append(
                Inserat(
                    quelle=self.name,
                    externe_id=kennung.group(1),
                    url=url,
                    titel=link.get_text(" ", strip=True) or "Ohne Titel",
                    warmmiete=warmmiete,
                    zimmer=zimmer,
                    flaeche=flaeche,
                    stadtteil=stadtteil,
                    beschreibung=volltext[:800],
                    bilder=bilder,
                    merkmale=merkmale,
                )
            )
        return ergebnis
