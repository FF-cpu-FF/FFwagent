"""FastAPI-Schnittstelle.

Zweck: die Daten für andere Werkzeuge zugänglich machen (Shortcuts, Home
Assistant, eigenes Frontend) und einen Lauf von außen anstoßen. Das
Streamlit-Dashboard braucht sie nicht – es liest direkt aus SQLite.

Start:  uvicorn wohnungsagent.api.app:app --reload
"""
from __future__ import annotations

from datetime import date

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ..config.profil import lade_profil
from ..config.settings import einstellungen
from ..database.repository import Repository
from ..llm.client import LlmClient
from ..services.pipeline import Pipeline

app = FastAPI(
    title="Wohnungsagent Frankfurt",
    version="1.0.0",
    description="Treffer abrufen, markieren und Läufe anstoßen.",
)


def repo() -> Repository:
    return Repository(einstellungen.datenbank_url)


class InseratAusgabe(BaseModel):
    uid: str
    titel: str
    url: str
    quelle: str
    score: int
    warmmiete: float | None = None
    kaltmiete: float | None = None
    zimmer: float | None = None
    flaeche: float | None = None
    stadtteil: str | None = None
    distanz_km: float | None = None
    einzug_ab: date | None = None
    einzug_status: str
    ki_zusammenfassung: str | None = None
    ki_warnsignale: list[str] = Field(default_factory=list)


class MarkierungEingabe(BaseModel):
    favorit: bool | None = None
    gesehen: bool | None = None
    notiz: str | None = None


@app.get("/gesundheit")
def gesundheit() -> dict:
    laeufe = repo().laeufe(limit=1)
    return {
        "status": "ok",
        "letzter_lauf": laeufe[0].gestartet.isoformat() if laeufe else None,
        "treffer_letzter_lauf": laeufe[0].treffer if laeufe else 0,
    }


@app.get("/treffer", response_model=list[InseratAusgabe])
def treffer(
    limit: int = Query(50, ge=1, le=500),
    mindestscore: int = Query(0, ge=0, le=100),
) -> list[InseratAusgabe]:
    return [
        InseratAusgabe(**{k: v for k, v in i.als_dict().items() if k in InseratAusgabe.model_fields})
        for i in repo().treffer(limit=limit)
        if i.bewertung.score >= mindestscore
    ]


@app.get("/treffer/{uid}", response_model=InseratAusgabe)
def einzeln(uid: str) -> InseratAusgabe:
    for inserat in repo().alle(limit=2000):
        if inserat.uid == uid:
            daten = inserat.als_dict()
            return InseratAusgabe(**{k: v for k, v in daten.items() if k in InseratAusgabe.model_fields})
    raise HTTPException(404, f"Kein Inserat mit uid {uid}")


@app.post("/treffer/{uid}/markierung")
def markiere(uid: str, eingabe: MarkierungEingabe) -> dict:
    repo().setze_markierung(uid, eingabe.favorit, eingabe.gesehen, eingabe.notiz)
    return {"uid": uid, "gespeichert": True}


@app.post("/scan")
def scan(
    hintergrund: BackgroundTasks,
    mit_ki: bool = Query(False, description="KI-Zusammenfassungen erzeugen (kostet Tokens)"),
    ki_limit: int | None = Query(None, ge=1, le=50),
) -> dict:
    """Stößt einen Lauf an. Antwortet sofort, der Lauf läuft im Hintergrund."""
    def durchlauf() -> None:
        profil = lade_profil(einstellungen.profil_pfad)
        datenbank = repo()
        llm = LlmClient(profil, eingeschaltet=True if mit_ki else None)
        Pipeline(profil, datenbank, llm).laufe(
            mindestscore=einstellungen.mindestscore_meldung, ki_limit=ki_limit
        )

    hintergrund.add_task(durchlauf)
    return {"gestartet": True, "mit_ki": mit_ki}


@app.get("/laeufe")
def laeufe(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    return [
        {
            "gestartet": lauf.gestartet.isoformat(),
            "dauer_s": lauf.dauer_s,
            "roh_gefunden": lauf.roh_gefunden,
            "treffer": lauf.treffer,
            "neu": lauf.neu,
            "quellen_ok": lauf.quellen_ok,
            "quellen_fehler": lauf.quellen_fehler,
            "quellen_robots_blockiert": lauf.quellen_robots_blockiert,
        }
        for lauf in repo().laeufe(limit=limit)
    ]
