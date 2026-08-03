"""Laden und Validieren des Suchprofils aus `config/suchprofil.yml`.

Bewusst als Dataclasses statt Pydantic: das Profil wird von `ranking/` und
`services/` benutzt, also von der innersten Schicht. Bliebe Pydantic dort
Pflicht, ließe sich der Kern nicht mehr ohne Framework testen. Pydantic kommt
in `config/settings.py` (Umgebungsvariablen und Geheimnisse) und in
`api/schemas.py` (Ein- und Ausgaben der HTTP-Schnittstelle) zum Einsatz –
also genau an den Rändern, an denen fremde Daten hereinkommen.

Die Validierung hier ist bewusst laut: ein Tippfehler im Profil soll beim
Start auffallen und nicht erst, wenn stundenlang nichts gefunden wurde.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml


class ProfilFehler(ValueError):
    """Das Suchprofil ist unbrauchbar. Enthält den Pfad zum Feld."""


def _hole(daten: dict, pfad: str, standard: Any = ..., typ: type | tuple = object) -> Any:
    aktuell: Any = daten
    for teil in pfad.split("."):
        if not isinstance(aktuell, dict) or teil not in aktuell:
            if standard is ...:
                raise ProfilFehler(f"Pflichtfeld fehlt: {pfad}")
            return standard
        aktuell = aktuell[teil]
    if aktuell is None and standard is not ...:
        return standard
    if typ is not object and not isinstance(aktuell, typ):
        if typ in ((int, float), float) and isinstance(aktuell, (int, float)):
            return float(aktuell)
        raise ProfilFehler(f"{pfad}: erwartet {typ}, gefunden {type(aktuell).__name__}")
    return aktuell


@dataclass(frozen=True, slots=True)
class Referenzpunkt:
    name: str
    adresse: str
    lat: float
    lon: float
    max_km: float
    bonus_km: float


@dataclass(frozen=True, slots=True)
class Lage:
    bevorzugt: list[str]
    erlaubt: list[str]
    ohne_stadtteil_ueber_radius: bool


@dataclass(frozen=True, slots=True)
class Wohnung:
    zimmer_bevorzugt: float
    zimmer_min: float
    zimmer_max: float
    flaeche_min: float
    flaeche_hart: float
    wg_geeignet: bool


@dataclass(frozen=True, slots=True)
class Budget:
    warmmiete_min: float
    warmmiete_max: float
    toleranz_prozent: float
    warmmiete_schaetzfaktor: float


@dataclass(frozen=True, slots=True)
class Einzug:
    fruehestens: date
    unbekannt: str
    malus_unbekannt: float
    sofort_signale: list[str]


@dataclass(frozen=True, slots=True)
class Ranking:
    gewichte: dict[str, float]
    llm_einfluss_max: float
    malus_luecke: float


@dataclass(frozen=True, slots=True)
class Ki:
    aktiv: bool
    max_inserate_pro_lauf: int
    erneut_bewerten: bool


@dataclass(frozen=True, slots=True)
class Betrieb:
    intervall_minuten: int
    request_pause_sekunden: tuple[float, float]
    timeout_sekunden: int
    max_retries: int
    historie_tage: int
    user_agent_kontakt: str


@dataclass(frozen=True, slots=True)
class Suchprofil:
    referenzpunkt: Referenzpunkt
    lage: Lage
    wohnung: Wohnung
    budget: Budget
    einzug: Einzug
    ranking: Ranking
    ki: Ki
    betrieb: Betrieb
    ausstattung_wunsch: dict[str, bool] = field(default_factory=dict)
    ausschluss_stichwoerter: list[str] = field(default_factory=list)
    quellen: dict[str, dict] = field(default_factory=dict)

    def aktive_quellen(self) -> dict[str, dict]:
        return {n: c for n, c in self.quellen.items() if c.get("aktiv")}


UNBEKANNT_MODI = {"ausschliessen", "pruefen", "ignorieren"}


def _als_datum(wert: Any, pfad: str) -> date:
    if isinstance(wert, date):
        return wert
    try:
        return date.fromisoformat(str(wert))
    except ValueError as fehler:
        raise ProfilFehler(f"{pfad}: '{wert}' ist kein Datum im Format JJJJ-MM-TT") from fehler


def lade_profil(pfad: str | Path = "config/suchprofil.yml") -> Suchprofil:
    datei = Path(pfad)
    if not datei.exists():
        raise ProfilFehler(f"Suchprofil nicht gefunden: {datei.resolve()}")
    daten = yaml.safe_load(datei.read_text(encoding="utf-8")) or {}

    referenzpunkt = Referenzpunkt(
        name=_hole(daten, "referenzpunkt.name", "Referenzpunkt", str),
        adresse=_hole(daten, "referenzpunkt.adresse", "", str),
        lat=float(_hole(daten, "referenzpunkt.lat", typ=(int, float))),
        lon=float(_hole(daten, "referenzpunkt.lon", typ=(int, float))),
        max_km=float(_hole(daten, "referenzpunkt.max_km", 3.0, (int, float))),
        bonus_km=float(_hole(daten, "referenzpunkt.bonus_km", 2.0, (int, float))),
    )
    if not (47.0 < referenzpunkt.lat < 55.5 and 5.5 < referenzpunkt.lon < 15.5):
        raise ProfilFehler(
            f"referenzpunkt: {referenzpunkt.lat}/{referenzpunkt.lon} liegt außerhalb "
            "Deutschlands – Breiten- und Längengrad vertauscht?"
        )
    if referenzpunkt.bonus_km > referenzpunkt.max_km:
        raise ProfilFehler("referenzpunkt.bonus_km darf nicht größer als max_km sein")

    wohnung = Wohnung(
        zimmer_bevorzugt=float(_hole(daten, "wohnung.zimmer_bevorzugt", 3, (int, float))),
        zimmer_min=float(_hole(daten, "wohnung.zimmer_min", 2, (int, float))),
        zimmer_max=float(_hole(daten, "wohnung.zimmer_max", 99, (int, float))),
        flaeche_min=float(_hole(daten, "wohnung.flaeche_min", 0, (int, float))),
        flaeche_hart=float(_hole(daten, "wohnung.flaeche_hart", 0, (int, float))),
        wg_geeignet=bool(_hole(daten, "wohnung.wg_geeignet", True, bool)),
    )
    if wohnung.zimmer_min > wohnung.zimmer_max:
        raise ProfilFehler("wohnung.zimmer_min ist größer als zimmer_max")
    if wohnung.flaeche_hart > wohnung.flaeche_min:
        raise ProfilFehler("wohnung.flaeche_hart darf nicht über flaeche_min liegen")

    budget = Budget(
        warmmiete_min=float(_hole(daten, "budget.warmmiete_min", 0, (int, float))),
        warmmiete_max=float(_hole(daten, "budget.warmmiete_max", typ=(int, float))),
        toleranz_prozent=float(_hole(daten, "budget.toleranz_prozent", 0, (int, float))),
        warmmiete_schaetzfaktor=float(_hole(daten, "budget.warmmiete_schaetzfaktor", 1.25, (int, float))),
    )
    if budget.warmmiete_min > budget.warmmiete_max:
        raise ProfilFehler("budget.warmmiete_min ist größer als warmmiete_max")

    modus = str(_hole(daten, "einzug.unbekannt", "pruefen", str)).lower()
    if modus not in UNBEKANNT_MODI:
        raise ProfilFehler(f"einzug.unbekannt: '{modus}' – erlaubt sind {sorted(UNBEKANNT_MODI)}")

    einzug = Einzug(
        fruehestens=_als_datum(_hole(daten, "einzug.fruehestens"), "einzug.fruehestens"),
        unbekannt=modus,
        malus_unbekannt=float(_hole(daten, "einzug.malus_unbekannt", 10, (int, float))),
        sofort_signale=list(_hole(daten, "einzug.sofort_signale", [], list)),
    )

    gewichte = dict(_hole(daten, "ranking.gewichte", {}, dict))
    unbekannte = set(gewichte) - set(_ERLAUBTE_GEWICHTE)
    if unbekannte:
        raise ProfilFehler(
            f"ranking.gewichte: unbekannte Kriterien {sorted(unbekannte)} – "
            f"erlaubt sind {sorted(_ERLAUBTE_GEWICHTE)}"
        )

    pause = _hole(daten, "betrieb.request_pause_sekunden", [2.0, 5.0], list)
    if len(pause) != 2 or pause[0] > pause[1]:
        raise ProfilFehler("betrieb.request_pause_sekunden erwartet [min, max]")

    return Suchprofil(
        referenzpunkt=referenzpunkt,
        lage=Lage(
            bevorzugt=list(_hole(daten, "lage.bevorzugt", [], list)),
            erlaubt=list(_hole(daten, "lage.erlaubt", [], list)),
            ohne_stadtteil_ueber_radius=bool(
                _hole(daten, "lage.ohne_stadtteil_ueber_radius", True, bool)
            ),
        ),
        wohnung=wohnung,
        budget=budget,
        einzug=einzug,
        ranking=Ranking(
            gewichte={k: float(v) for k, v in gewichte.items()},
            llm_einfluss_max=float(_hole(daten, "ranking.llm_einfluss_max", 10, (int, float))),
            malus_luecke=float(_hole(daten, "ranking.malus_luecke", 4, (int, float))),
        ),
        ki=Ki(
            aktiv=bool(_hole(daten, "ki.aktiv", False, bool)),
            max_inserate_pro_lauf=int(_hole(daten, "ki.max_inserate_pro_lauf", 8, int)),
            erneut_bewerten=bool(_hole(daten, "ki.erneut_bewerten", False, bool)),
        ),
        betrieb=Betrieb(
            intervall_minuten=int(_hole(daten, "betrieb.intervall_minuten", 60, int)),
            request_pause_sekunden=(float(pause[0]), float(pause[1])),
            timeout_sekunden=int(_hole(daten, "betrieb.timeout_sekunden", 30, int)),
            max_retries=int(_hole(daten, "betrieb.max_retries", 3, int)),
            historie_tage=int(_hole(daten, "betrieb.historie_tage", 90, int)),
            user_agent_kontakt=str(_hole(daten, "betrieb.user_agent_kontakt", "wohnungsagent", str)),
        ),
        ausstattung_wunsch=dict(_hole(daten, "ausstattung", {}, dict)),
        ausschluss_stichwoerter=list(_hole(daten, "ausschluss_stichwoerter", [], list)),
        quellen=dict(_hole(daten, "quellen", {}, dict)),
    )


_ERLAUBTE_GEWICHTE = {
    "lage_bevorzugt", "distanz_unter_bonus_km", "zimmer_ideal", "budget_eingehalten",
    "einzug_bestaetigt", "balkon", "keller", "einbaukueche", "beschreibung_gut",
    "bilder_gut", "privatvermieter",
}
