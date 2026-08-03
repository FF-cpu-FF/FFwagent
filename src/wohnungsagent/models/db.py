"""SQLAlchemy-Modell.

Vier Tabellen:

  inserate         aktueller Stand je Wohnung, Schlüssel ist die uid
  preishistorie    jede erkannte Änderung an Kalt- oder Warmmiete
  laeufe           ein Datensatz pro Durchlauf, für Diagnose und Statistik
  markierungen     Favoriten und "gesehen" aus dem Dashboard

Absichtlich getrennt: `inserate` wird bei jedem Lauf aktualisiert,
`preishistorie` nur ergänzt. Dadurch bleibt die Preisentwicklung erhalten,
auch wenn ein Inserat später verschwindet.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def jetzt() -> datetime:
    return datetime.now(UTC)


class Basis(DeclarativeBase):
    pass


class InseratRow(Basis):
    __tablename__ = "inserate"

    uid: Mapped[str] = mapped_column(String(16), primary_key=True)
    quelle: Mapped[str] = mapped_column(String(64), index=True)
    externe_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(Text)
    titel: Mapped[str] = mapped_column(Text)

    kaltmiete: Mapped[float | None] = mapped_column(Float)
    nebenkosten: Mapped[float | None] = mapped_column(Float)
    warmmiete: Mapped[float | None] = mapped_column(Float, index=True)
    kaution: Mapped[float | None] = mapped_column(Float)
    provision: Mapped[str | None] = mapped_column(String(128))

    zimmer: Mapped[float | None] = mapped_column(Float, index=True)
    flaeche: Mapped[float | None] = mapped_column(Float, index=True)
    etage: Mapped[str | None] = mapped_column(String(64))
    qm_preis: Mapped[float | None] = mapped_column(Float)

    adresse: Mapped[str | None] = mapped_column(Text)
    stadtteil: Mapped[str | None] = mapped_column(String(96), index=True)
    plz: Mapped[str | None] = mapped_column(String(8))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    geo_quelle: Mapped[str | None] = mapped_column(String(24))
    distanz_km: Mapped[float | None] = mapped_column(Float, index=True)

    einzug_ab: Mapped[str | None] = mapped_column(String(10))
    einzug_status: Mapped[str] = mapped_column(String(16), index=True, default="unbekannt")
    einzug_rohtext: Mapped[str | None] = mapped_column(String(200))

    vermietertyp: Mapped[str] = mapped_column(String(16), default="unbekannt")
    anbieter: Mapped[str | None] = mapped_column(String(200))
    ausstattung: Mapped[dict] = mapped_column(JSON, default=dict)
    merkmale: Mapped[list] = mapped_column(JSON, default=list)
    bilder: Mapped[list] = mapped_column(JSON, default=list)
    beschreibung: Mapped[str] = mapped_column(Text, default="")

    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_treffer: Mapped[list] = mapped_column(JSON, default=list)
    score_abzuege: Mapped[list] = mapped_column(JSON, default=list)
    ausgeschlossen: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ausschlussgrund: Mapped[str | None] = mapped_column(Text)

    ki_zusammenfassung: Mapped[str | None] = mapped_column(Text)
    ki_warnsignale: Mapped[list] = mapped_column(JSON, default=list)
    ki_wg_geeignet: Mapped[bool | None] = mapped_column(Boolean)
    ki_seriositaet: Mapped[int | None] = mapped_column(Integer)

    erstmals_gesehen: Mapped[datetime] = mapped_column(DateTime, default=jetzt, index=True)
    zuletzt_gesehen: Mapped[datetime] = mapped_column(DateTime, default=jetzt, index=True)
    gemeldet: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    preise: Mapped[list[PreisRow]] = relationship(
        back_populates="inserat", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_inserate_ranking", "ausgeschlossen", "score", "zuletzt_gesehen"),
        Index("ix_inserate_quelle_extern", "quelle", "externe_id", unique=True),
    )


class PreisRow(Basis):
    __tablename__ = "preishistorie"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(ForeignKey("inserate.uid", ondelete="CASCADE"), index=True)
    zeitpunkt: Mapped[datetime] = mapped_column(DateTime, default=jetzt, index=True)
    feld: Mapped[str] = mapped_column(String(24))
    alt: Mapped[float | None] = mapped_column(Float)
    neu: Mapped[float | None] = mapped_column(Float)

    inserat: Mapped[InseratRow] = relationship(back_populates="preise")


class LaufRow(Basis):
    __tablename__ = "laeufe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gestartet: Mapped[datetime] = mapped_column(DateTime, default=jetzt, index=True)
    beendet: Mapped[datetime | None] = mapped_column(DateTime)
    dauer_s: Mapped[float] = mapped_column(Float, default=0.0)
    roh_gefunden: Mapped[int] = mapped_column(Integer, default=0)
    nach_dedupe: Mapped[int] = mapped_column(Integer, default=0)
    ausgeschlossen: Mapped[int] = mapped_column(Integer, default=0)
    treffer: Mapped[int] = mapped_column(Integer, default=0)
    neu: Mapped[int] = mapped_column(Integer, default=0)
    ki_aufrufe: Mapped[int] = mapped_column(Integer, default=0)
    ki_tokens: Mapped[int] = mapped_column(Integer, default=0)
    quellen_ok: Mapped[list] = mapped_column(JSON, default=list)
    quellen_fehler: Mapped[dict] = mapped_column(JSON, default=dict)
    quellen_robots_blockiert: Mapped[list] = mapped_column(JSON, default=list)


class MarkierungRow(Basis):
    """Favoriten und Gesehen-Markierungen aus dem Dashboard."""

    __tablename__ = "markierungen"

    uid: Mapped[str] = mapped_column(String(16), primary_key=True)
    favorit: Mapped[bool] = mapped_column(Boolean, default=False)
    gesehen: Mapped[bool] = mapped_column(Boolean, default=False)
    notiz: Mapped[str | None] = mapped_column(Text)
    geaendert: Mapped[datetime] = mapped_column(DateTime, default=jetzt, onupdate=jetzt)
