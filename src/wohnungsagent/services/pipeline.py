"""Pipeline: der Ablauf eines Durchlaufs.

    sammeln -> anreichern -> entdoppeln -> ausschließen -> bewerten
            -> KI -> speichern -> melden -> exportieren

Wichtig an der Reihenfolge: entdoppelt wird vor dem Ausschluss. Sonst könnte
die vollständigere Fassung eines Inserats verworfen werden, weil die dünnere
Fassung eines anderen Portals zuerst aussortiert wurde. Und die KI läuft
zuletzt und nur auf den Kandidaten, die die harten Regeln überstanden haben –
das spart den Großteil der Kosten.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import requests
from loguru import logger

from typing import TYPE_CHECKING

from ..config.profil import Suchprofil
from ..llm.client import LlmClient
from ..models.domain import Inserat, Laufergebnis
from ..notifier.kanaele import benachrichtige
from ..ranking import regeln, scoring
from ..scrapers.base import QuelleBlockiert, QuelleKaputt
from ..scrapers.registry import baue_alle
from . import dedupe, parsing
from . import geo as geodienst

if TYPE_CHECKING:  # Repository nur als Typ – hält SQLAlchemy aus dieser Schicht heraus
    from ..database.repository import Repository


class Pipeline:
    def __init__(
        self,
        profil: Suchprofil,
        repository: "Repository",
        llm: LlmClient | None = None,
        geocoding: bool = False,
    ) -> None:
        self.profil = profil
        self.repo = repository
        self.llm = llm
        self._geo_cache: dict[str, tuple[float, float]] = {}
        self.geocoder = (
            geodienst.nominatim_geocoder(profil.betrieb.user_agent_kontakt, self._geo_cache)
            if geocoding
            else None
        )

    # ------------------------------------------------------------- Sammeln
    def sammle(self, nur_quelle: str | None = None) -> tuple[list[Inserat], Laufergebnis]:
        ergebnis = Laufergebnis()
        roh: list[Inserat] = []

        for scraper in baue_alle(self.profil, nur_quelle):
            try:
                treffer = scraper.sammle()
                roh.extend(treffer)
                ergebnis.quellen_ok.append(scraper.name)
            except QuelleBlockiert as fehler:
                logger.warning("{} übersprungen: {}", scraper.label, fehler)
                if "robots" in str(fehler).lower():
                    ergebnis.quellen_robots_blockiert.append(scraper.name)
                else:
                    ergebnis.quellen_fehler[scraper.name] = str(fehler)
            except QuelleKaputt as fehler:
                logger.error("{} liefert unbrauchbares Markup: {}", scraper.label, fehler)
                ergebnis.quellen_fehler[scraper.name] = str(fehler)
            except requests.HTTPError as fehler:
                # Erwartbarer Fall: falsche oder veraltete URL. Ein voller
                # Traceback hilft hier niemandem, die Statuszeile schon.
                code = fehler.response.status_code if fehler.response is not None else "?"
                logger.warning(
                    "{}: HTTP {} – URL vermutlich veraltet, Quelle übersprungen",
                    scraper.label, code,
                )
                ergebnis.quellen_fehler[scraper.name] = f"HTTP {code}"
            except Exception as fehler:  # noqa: BLE001 – eine Quelle kippt nie den Lauf
                logger.exception("{} unerwartet fehlgeschlagen", scraper.label)
                ergebnis.quellen_fehler[scraper.name] = f"{type(fehler).__name__}: {fehler}"

        ergebnis.roh_gefunden = len(roh)
        return roh, ergebnis

    # ---------------------------------------------------------- Anreichern
    def reichere_an(self, inserat: Inserat) -> Inserat:
        """Füllt alles, was aus dem Rohtext ableitbar ist."""
        volltext = f"{inserat.titel}\n{inserat.beschreibung}\n{' '.join(inserat.merkmale)}"
        if inserat.inseriert_rohtext:
            volltext += f"\n{inserat.inseriert_rohtext}"

        status, datum, rohtext = parsing.einzugsstatus(
            volltext, self.profil.einzug.fruehestens, self.profil.einzug.sofort_signale
        )
        inserat.einzug_status = status
        inserat.einzug_ab = datum
        inserat.einzug_rohtext = rohtext

        erkannt = parsing.ausstattung(volltext)
        for feld, wert in erkannt.als_dict().items():
            if getattr(inserat.ausstattung, feld) is None and wert is not None:
                setattr(inserat.ausstattung, feld, wert)

        inserat.vermietertyp = parsing.vermietertyp(inserat.anbieter, volltext)

        if not inserat.plz:
            inserat.plz = parsing.plz(f"{inserat.adresse or ''} {inserat.stadtteil or ''} {inserat.titel}")

        if inserat.warmmiete is None and inserat.kaltmiete and inserat.nebenkosten:
            inserat.warmmiete = round(inserat.kaltmiete + inserat.nebenkosten, 2)

        if inserat.geo.lat is None:
            inserat.geo = geodienst.lokalisiere(
                adresse=inserat.adresse,
                stadtteil=inserat.stadtteil,
                postleitzahl=inserat.plz,
                geocoder=self.geocoder,
            )
        inserat.distanz_km = geodienst.distanz_zu(
            inserat.geo, self.profil.referenzpunkt.lat, self.profil.referenzpunkt.lon
        )

        if (schluessel := geodienst.normalisiere_stadtteil(inserat.stadtteil or inserat.adresse)):
            inserat.stadtteil = geodienst.anzeigename(schluessel)

        inserat.zuletzt_gesehen = datetime.now(timezone.utc)
        return inserat

    def aufbereiten(self, roh: list[Inserat], ergebnis: Laufergebnis) -> list[Inserat]:
        """Anreichern, leere Hüllen verwerfen, entdoppeln.

        Bewusst als eigene Methode: Probelauf und echter Lauf müssen exakt
        denselben Weg nehmen, sonst zeigt der Probelauf Treffer an, die im
        Ernstfall aussortiert werden – und man debuggt an einer Anzeige,
        die es so gar nicht gibt.
        """
        angereichert = [self.reichere_an(i) for i in roh]

        brauchbar = [i for i in angereichert if i.ist_brauchbar]
        ergebnis.unbrauchbar = len(angereichert) - len(brauchbar)
        if ergebnis.unbrauchbar:
            je_quelle = Counter(i.quelle for i in angereichert if not i.ist_brauchbar)
            logger.warning(
                "{} Inserate ohne verwertbare Angaben verworfen ({}). "
                "Bei einer Quelle mit hohem Anteil greift der Parser nicht mehr – "
                "prüfen mit: wohnungsagent diagnose <quelle>",
                ergebnis.unbrauchbar, dict(je_quelle),
            )

        eindeutig = dedupe.entdoppeln(brauchbar)
        ergebnis.nach_dedupe = len(eindeutig)
        logger.info("{} nach Duplikatabgleich", len(eindeutig))
        return eindeutig

    # ----------------------------------------------------------- Bewerten
    def bewerte(self, inserate: list[Inserat]) -> tuple[list[Inserat], list[Inserat]]:
        treffer: list[Inserat] = []
        verworfen: list[Inserat] = []

        for inserat in inserate:
            grund = regeln.pruefe(inserat, self.profil)
            if grund:
                inserat.bewertung.ausgeschlossen = True
                inserat.bewertung.ausschlussgrund = grund
                verworfen.append(inserat)
            else:
                inserat.bewertung = scoring.bewerte(inserat, self.profil)
                treffer.append(inserat)
        return treffer, verworfen

    def bewerte_mit_ki(self, inserate: list[Inserat], hoechstens: int | None = None) -> tuple[int, int]:
        """KI-Bewertung nur für die vielversprechendsten Kandidaten.

        Der einzige Schritt, der LLM-Tokens verbraucht. Deshalb dreifach
        begrenzt: er läuft erst nach dem Ausschluss, nur auf den besten N
        Inseraten, und überspringt alles, was bereits eine Bewertung hat.
        Rückgabe: (Aufrufe, Tokens) dieses Laufs.
        """
        if not (self.llm and self.llm.aktiv):
            return 0, 0

        grenze = hoechstens if hoechstens is not None else self.profil.ki.max_inserate_pro_lauf
        kandidaten = sorted(inserate, key=lambda i: i.bewertung.score, reverse=True)

        if not self.profil.ki.erneut_bewerten:
            bekannt = self._bereits_bewertet({i.uid for i in kandidaten})
            kandidaten = [i for i in kandidaten if i.uid not in bekannt]

        kandidaten = kandidaten[:grenze]
        if not kandidaten:
            logger.info("KI: nichts Neues zu bewerten, keine Tokens verbraucht")
            return 0, 0

        for inserat in kandidaten:
            if (urteil := self.llm.bewerte(inserat)):
                inserat.ki = urteil
                inserat.bewertung = scoring.bewerte(inserat, self.profil)   # Delta einrechnen

        logger.info(
            "KI: {} Inserate bewertet, {} Aufrufe, rund {} Tokens",
            len(kandidaten), self.llm.aufrufe, self.llm.tokens_gesamt,
        )
        return self.llm.aufrufe, self.llm.tokens_gesamt

    def _bereits_bewertet(self, uids: set[str]) -> set[str]:
        """UIDs, für die schon eine KI-Zusammenfassung in der Datenbank steht."""
        if self.repo is None:
            return set()
        try:
            return {
                i.uid for i in self.repo.alle(limit=2000)
                if i.uid in uids and i.ki and i.ki.zusammenfassung
            }
        except Exception:  # noqa: BLE001 – im Zweifel lieber neu bewerten
            return set()

    # ---------------------------------------------------------- Durchlauf
    def laufe(self, nur_quelle: str | None = None, melden: bool = True,
              mindestscore: int = 0, ki_limit: int | None = None) -> Laufergebnis:
        roh, ergebnis = self.sammle(nur_quelle)
        logger.info("{} Rohinserate aus {} Quellen", len(roh), len(ergebnis.quellen_ok))

        eindeutig = self.aufbereiten(roh, ergebnis)

        treffer, verworfen = self.bewerte(eindeutig)
        ergebnis.ausgeschlossen = len(verworfen)
        ergebnis.treffer = len(treffer)
        _protokolliere_ausschluesse(verworfen)

        ergebnis.ki_aufrufe, ergebnis.ki_tokens = self.bewerte_mit_ki(treffer, ki_limit)

        neu, aktualisiert, aenderungen = self.repo.speichere(treffer + verworfen)
        neue_treffer = [i for i in neu if not i.bewertung.ausgeschlossen]
        ergebnis.neu = len(neue_treffer)
        ergebnis.aktualisiert = len(aktualisiert)
        ergebnis.preisaenderungen = aenderungen

        if aenderungen:
            for aenderung in aenderungen:
                logger.info(
                    "Preisänderung {}: {} {} -> {}",
                    aenderung.uid, aenderung.feld, aenderung.alt, aenderung.neu,
                )

        self.repo.markiere_verschwundene(
            {i.uid for i in eindeutig}, ergebnis.quellen_ok
        )

        if melden:
            zu_melden = [
                i for i in self.repo.ungemeldete_treffer(mindestscore)
                if i.uid in {n.uid for n in neue_treffer}
            ]
            if zu_melden and benachrichtige(zu_melden):
                self.repo.markiere_gemeldet([i.uid for i in zu_melden])

        entfernt = self.repo.raeume_auf(self.profil.betrieb.historie_tage)
        if entfernt:
            logger.info("{} veraltete Inserate entfernt", entfernt)

        ergebnis.beendet = datetime.now(timezone.utc)
        self.repo.protokolliere_lauf(ergebnis)
        logger.success(
            "Lauf beendet in {:.0f}s: {} roh -> {} eindeutig -> {} Treffer, davon {} neu",
            ergebnis.dauer_s, ergebnis.roh_gefunden, ergebnis.nach_dedupe,
            ergebnis.treffer, ergebnis.neu,
        )
        if ergebnis.ki_aufrufe:
            logger.info("Tokenverbrauch dieses Laufs: rund {}", ergebnis.ki_tokens)
        else:
            logger.info("Tokenverbrauch dieses Laufs: 0 – die KI war nicht eingeschaltet")
        return ergebnis


def _protokolliere_ausschluesse(verworfen: list[Inserat]) -> None:
    """Zeigt, woran es lag – sonst sucht man bei null Treffern im Dunkeln."""
    
    zaehler = Counter(
        (i.bewertung.ausschlussgrund or "").split(" (")[0].split(":")[0] for i in verworfen
    )
    if zaehler:
        logger.info("Aussortiert: {}", dict(zaehler.most_common(8)))
