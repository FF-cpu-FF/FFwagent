"""Domänenmodelle.

Bewusst frameworkfrei (nur stdlib-Dataclasses). Nach Clean Architecture darf
die innerste Schicht nichts über Pydantic, SQLAlchemy oder HTTP wissen –
dadurch ist der gesamte Kern ohne Datenbank und ohne Netz testbar.

Pydantic kommt in `config/` (Validierung der Eingaben) und in `api/`
(Serialisierung nach außen) zum Einsatz, SQLAlchemy ausschließlich in
`models/db.py`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


def jetzt() -> datetime:
    return datetime.now(timezone.utc)


class Einzugsstatus(str, Enum):
    """Dreiwertig – "unbekannt" ist ein eigener Zustand, kein stiller Fehlschlag."""

    PASST = "passt"            # belegtes Datum ab dem Stichtag
    ZU_FRUEH = "zu_frueh"      # belegtes Datum vor dem Stichtag oder "ab sofort"
    UNBEKANNT = "unbekannt"    # kein Datum im Inserat auffindbar


class Vermietertyp(str, Enum):
    PRIVAT = "privat"
    GEWERBLICH = "gewerblich"
    UNBEKANNT = "unbekannt"


@dataclass(slots=True)
class Ausstattung:
    balkon: bool | None = None
    keller: bool | None = None
    einbaukueche: bool | None = None
    aufzug: bool | None = None
    altbau: bool | None = None
    wg_geeignet: bool | None = None
    oepnv_erwaehnt: bool | None = None

    def als_dict(self) -> dict[str, bool | None]:
        return {f: getattr(self, f) for f in self.__slots__}


@dataclass(slots=True)
class Geo:
    lat: float | None = None
    lon: float | None = None
    quelle: str = "unbekannt"     # "adresse" | "stadtteil" | "portal" | "unbekannt"
    genauigkeit_m: int | None = None


@dataclass(slots=True)
class KiBewertung:
    """Ergebnis der semantischen Prüfung durch das LLM."""

    zusammenfassung: str = ""
    wg_geeignet: bool | None = None
    seriositaet: int | None = None        # 1–5
    lagebewertung: int | None = None      # 1–5
    warnsignale: list[str] = field(default_factory=list)
    fehlende_angaben: list[str] = field(default_factory=list)
    punkte_delta: int = 0                 # wird auf ranking.llm_einfluss_max gekappt
    modell: str = ""
    erzeugt_am: datetime | None = None


@dataclass(slots=True)
class Bewertung:
    score: int = 0
    rohpunkte: float = 0.0
    maximalpunkte: float = 0.0
    treffer: list[str] = field(default_factory=list)      # erfüllte Kriterien
    abzuege: list[str] = field(default_factory=list)
    ausgeschlossen: bool = False
    ausschlussgrund: str | None = None


@dataclass(slots=True)
class Inserat:
    """Ein Wohnungsangebot, quellenunabhängig normalisiert."""

    quelle: str
    externe_id: str
    url: str
    titel: str

    kaltmiete: float | None = None
    nebenkosten: float | None = None
    warmmiete: float | None = None
    kaution: float | None = None
    provision: str | None = None

    zimmer: float | None = None
    flaeche: float | None = None
    etage: str | None = None

    adresse: str | None = None
    stadtteil: str | None = None
    plz: str | None = None
    geo: Geo = field(default_factory=Geo)
    distanz_km: float | None = None

    einzug_ab: date | None = None
    einzug_status: Einzugsstatus = Einzugsstatus.UNBEKANNT
    einzug_rohtext: str | None = None

    ausstattung: Ausstattung = field(default_factory=Ausstattung)
    vermietertyp: Vermietertyp = Vermietertyp.UNBEKANNT
    anbieter: str | None = None

    beschreibung: str = ""
    bilder: list[str] = field(default_factory=list)
    merkmale: list[str] = field(default_factory=list)

    erstmals_gesehen: datetime = field(default_factory=jetzt)
    zuletzt_gesehen: datetime = field(default_factory=jetzt)
    inseriert_rohtext: str | None = None

    bewertung: Bewertung = field(default_factory=Bewertung)
    ki: KiBewertung | None = None

    # ---------------------------------------------------------------- Identität
    @property
    def uid(self) -> str:
        return hashlib.sha1(f"{self.quelle}|{self.externe_id}".encode()).hexdigest()[:16]

    @property
    def dedupe_schluessel(self) -> str:
        """Grober Vorfilter für die Duplikatsuche über Portalgrenzen hinweg.

        Nur Inserate mit gleichem Schlüssel werden anschließend unscharf
        verglichen – das hält den Vergleich bei O(n) statt O(n²).
        """
        miete = round(self.warmmiete / 50) * 50 if self.warmmiete else 0
        flaeche = round(self.flaeche / 5) * 5 if self.flaeche else 0
        zimmer = f"{self.zimmer:g}" if self.zimmer else "?"
        return f"{miete}|{flaeche}|{zimmer}"

    # ------------------------------------------------------------- Abgeleitetes
    @property
    def ist_brauchbar(self) -> bool:
        """Enthält das Inserat überhaupt verwertbare Angaben?

        Ein Eintrag ohne Titel, ohne Preis, ohne Fläche und ohne Zimmerzahl
        ist kein Angebot, sondern eine leere Hülle – meist ein Parser, der
        nur noch die Links findet. Solche Einträge kämen durch jede
        Ausschlussregel, weil es nichts gibt, woran sie scheitern könnten.
        Sie deshalb hier abzufangen ist notwendig; sonst füllen sie
        Dashboard und Benachrichtigungen.
        """
        hat_titel = bool(self.titel) and self.titel.strip().lower() not in (
            "", "ohne titel", "kein titel", "-"
        )
        hat_zahlen = any(
            w is not None for w in (self.kaltmiete, self.warmmiete, self.flaeche, self.zimmer)
        )
        return hat_titel and hat_zahlen

    @property
    def qm_preis(self) -> float | None:
        basis = self.kaltmiete or self.warmmiete
        if basis and self.flaeche:
            return round(basis / self.flaeche, 2)
        return None

    @property
    def volltext(self) -> str:
        teile = [self.titel, self.beschreibung, self.stadtteil or "", " ".join(self.merkmale)]
        return " ".join(teile).lower()

    @property
    def normalisierter_titel(self) -> str:
        """Für den unscharfen Titelvergleich beim Dedupe."""
        text = re.sub(r"[^\wäöüß ]+", " ", self.titel.lower())
        return " ".join(sorted(set(text.split()) - FUELLWOERTER))

    @property
    def ist_neu(self) -> bool:
        return (jetzt() - self.erstmals_gesehen).total_seconds() < 86_400

    def als_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "quelle": self.quelle,
            "externe_id": self.externe_id,
            "url": self.url,
            "titel": self.titel,
            "kaltmiete": self.kaltmiete,
            "nebenkosten": self.nebenkosten,
            "warmmiete": self.warmmiete,
            "kaution": self.kaution,
            "provision": self.provision,
            "zimmer": self.zimmer,
            "flaeche": self.flaeche,
            "etage": self.etage,
            "qm_preis": self.qm_preis,
            "adresse": self.adresse,
            "stadtteil": self.stadtteil,
            "plz": self.plz,
            "lat": self.geo.lat,
            "lon": self.geo.lon,
            "geo_quelle": self.geo.quelle,
            "distanz_km": self.distanz_km,
            "einzug_ab": self.einzug_ab.isoformat() if self.einzug_ab else None,
            "einzug_status": self.einzug_status.value,
            "einzug_rohtext": self.einzug_rohtext,
            "vermietertyp": self.vermietertyp.value,
            "anbieter": self.anbieter,
            "ausstattung": self.ausstattung.als_dict(),
            "beschreibung": self.beschreibung[:1200],
            "bilder": self.bilder[:8],
            "merkmale": self.merkmale,
            "erstmals_gesehen": self.erstmals_gesehen.isoformat(timespec="seconds"),
            "zuletzt_gesehen": self.zuletzt_gesehen.isoformat(timespec="seconds"),
            "ist_neu": self.ist_neu,
            "score": self.bewertung.score,
            "score_treffer": self.bewertung.treffer,
            "score_abzuege": self.bewertung.abzuege,
            "ausgeschlossen": self.bewertung.ausgeschlossen,
            "ausschlussgrund": self.bewertung.ausschlussgrund,
            "ki_zusammenfassung": self.ki.zusammenfassung if self.ki else None,
            "ki_warnsignale": self.ki.warnsignale if self.ki else [],
            "ki_wg_geeignet": self.ki.wg_geeignet if self.ki else None,
        }


FUELLWOERTER = {
    "die", "der", "das", "ein", "eine", "mit", "in", "im", "am", "und", "für",
    "von", "zu", "zum", "zur", "auf", "wohnung", "zimmer", "qm", "m2", "frankfurt",
}


@dataclass(slots=True)
class Preisaenderung:
    uid: str
    zeitpunkt: datetime
    feld: str
    alt: float | None
    neu: float | None


@dataclass(slots=True)
class Laufergebnis:
    """Was ein einzelner Durchlauf produziert hat – Grundlage für Reporting."""

    gestartet: datetime = field(default_factory=jetzt)
    beendet: datetime | None = None
    roh_gefunden: int = 0
    nach_dedupe: int = 0
    ausgeschlossen: int = 0
    treffer: int = 0
    neu: int = 0
    aktualisiert: int = 0
    preisaenderungen: list[Preisaenderung] = field(default_factory=list)
    unbrauchbar: int = 0
    ki_aufrufe: int = 0
    ki_tokens: int = 0
    quellen_ok: list[str] = field(default_factory=list)
    quellen_fehler: dict[str, str] = field(default_factory=dict)
    quellen_robots_blockiert: list[str] = field(default_factory=list)

    @property
    def dauer_s(self) -> float:
        return ((self.beendet or jetzt()) - self.gestartet).total_seconds()
