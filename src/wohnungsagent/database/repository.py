"""Repository: der einzige Ort, an dem Domänenobjekte auf die Datenbank treffen.

Die Pipeline arbeitet ausschließlich mit `Inserat` und ruft hier `speichere()`
auf. Dadurch bleibt SQLAlchemy vollständig aus dem Kern heraus, und die
Umwandlung Domäne <-> Tabelle liegt an einer Stelle statt verteilt.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..models.db import Basis, InseratRow, LaufRow, MarkierungRow, PreisRow
from ..models.domain import (
    Ausstattung,
    Bewertung,
    Einzugsstatus,
    Geo,
    Inserat,
    KiBewertung,
    Laufergebnis,
    Preisaenderung,
    Vermietertyp,
    als_utc,
)

PREISFELDER = ("kaltmiete", "warmmiete", "nebenkosten")


def baue_engine(url: str = "sqlite:///data/wohnungen.db"):
    if url.startswith("sqlite"):
        from pathlib import Path

        pfad = url.replace("sqlite:///", "")
        Path(pfad).parent.mkdir(parents=True, exist_ok=True)
    motor = create_engine(url, future=True)
    Basis.metadata.create_all(motor)
    return motor


class Repository:
    def __init__(self, url: str = "sqlite:///data/wohnungen.db") -> None:
        self.engine = baue_engine(url)
        self._sitzung = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def sitzung(self) -> Session:
        return self._sitzung()

    # ------------------------------------------------------------ Schreiben
    def speichere(self, inserate: list[Inserat]) -> tuple[list[Inserat], list[Inserat], list[Preisaenderung]]:
        """Legt an oder aktualisiert.

        Rückgabe: (neu angelegt, aktualisiert, erkannte Preisänderungen).
        "Neu" heißt: uid war noch nie in der Datenbank – das ist die Grundlage
        dafür, dass niemand dieselbe Wohnung zweimal gemeldet bekommt.
        """
        neu: list[Inserat] = []
        aktualisiert: list[Inserat] = []
        aenderungen: list[Preisaenderung] = []

        with self.sitzung() as sitzung:
            for inserat in inserate:
                zeile = sitzung.get(InseratRow, inserat.uid)
                if zeile is None:
                    sitzung.add(_nach_zeile(inserat, InseratRow()))
                    neu.append(inserat)
                    continue

                for feld in PREISFELDER:
                    alt = getattr(zeile, feld)
                    aktuell = getattr(inserat, feld)
                    if aktuell is not None and alt is not None and abs(alt - aktuell) >= 1.0:
                        aenderung = Preisaenderung(
                            uid=inserat.uid,
                            zeitpunkt=datetime.now(UTC),
                            feld=feld,
                            alt=alt,
                            neu=aktuell,
                        )
                        aenderungen.append(aenderung)
                        sitzung.add(PreisRow(uid=inserat.uid, feld=feld, alt=alt, neu=aktuell))

                erstmals = zeile.erstmals_gesehen
                gemeldet = zeile.gemeldet
                _nach_zeile(inserat, zeile)
                zeile.erstmals_gesehen = erstmals      # Erstsichtung nie überschreiben
                zeile.gemeldet = gemeldet
                inserat.erstmals_gesehen = erstmals
                aktualisiert.append(inserat)

            sitzung.commit()
        return neu, aktualisiert, aenderungen

    def markiere_gemeldet(self, uids: list[str]) -> None:
        if not uids:
            return
        with self.sitzung() as sitzung:
            for uid in uids:
                if (zeile := sitzung.get(InseratRow, uid)):
                    zeile.gemeldet = True
            sitzung.commit()

    def markiere_verschwundene(self, gesehene_uids: set[str], quellen: list[str]) -> int:
        """Setzt Inserate auf inaktiv, die in ihrer Quelle nicht mehr auftauchen.

        Nur für Quellen, die im aktuellen Lauf tatsächlich erfolgreich waren –
        sonst würde eine blockierte Quelle ihren gesamten Bestand stilllegen.
        """
        if not quellen:
            return 0
        with self.sitzung() as sitzung:
            zeilen = sitzung.scalars(
                select(InseratRow).where(InseratRow.quelle.in_(quellen), InseratRow.aktiv.is_(True))
            ).all()
            betroffen = 0
            for zeile in zeilen:
                if zeile.uid not in gesehene_uids:
                    zeile.aktiv = False
                    betroffen += 1
            sitzung.commit()
            return betroffen

    def protokolliere_lauf(self, ergebnis: Laufergebnis) -> None:
        with self.sitzung() as sitzung:
            sitzung.add(
                LaufRow(
                    gestartet=ergebnis.gestartet,
                    beendet=ergebnis.beendet,
                    dauer_s=ergebnis.dauer_s,
                    roh_gefunden=ergebnis.roh_gefunden,
                    nach_dedupe=ergebnis.nach_dedupe,
                    ausgeschlossen=ergebnis.ausgeschlossen,
                    treffer=ergebnis.treffer,
                    neu=ergebnis.neu,
                    ki_aufrufe=ergebnis.ki_aufrufe,
                    ki_tokens=ergebnis.ki_tokens,
                    quellen_ok=ergebnis.quellen_ok,
                    quellen_fehler=ergebnis.quellen_fehler,
                    quellen_robots_blockiert=ergebnis.quellen_robots_blockiert,
                )
            )
            sitzung.commit()

    def raeume_auf(self, tage: int) -> int:
        # Die Spalte ist zeitzonenlos, deshalb hier bewusst naiv vergleichen.
        grenze = (datetime.now(UTC) - timedelta(days=tage)).replace(tzinfo=None)
        with self.sitzung() as sitzung:
            ergebnis = sitzung.execute(
                delete(InseratRow).where(
                    InseratRow.zuletzt_gesehen < grenze, InseratRow.aktiv.is_(False)
                )
            )
            sitzung.commit()
            return ergebnis.rowcount or 0

    def setze_markierung(self, uid: str, favorit: bool | None = None,
                         gesehen: bool | None = None, notiz: str | None = None) -> None:
        with self.sitzung() as sitzung:
            zeile = sitzung.get(MarkierungRow, uid) or MarkierungRow(uid=uid)
            if favorit is not None:
                zeile.favorit = favorit
            if gesehen is not None:
                zeile.gesehen = gesehen
            if notiz is not None:
                zeile.notiz = notiz
            sitzung.add(zeile)
            sitzung.commit()

    # --------------------------------------------------------------- Lesen
    def treffer(self, limit: int = 200, nur_aktive: bool = True) -> list[Inserat]:
        with self.sitzung() as sitzung:
            abfrage = select(InseratRow).where(InseratRow.ausgeschlossen.is_(False))
            if nur_aktive:
                abfrage = abfrage.where(InseratRow.aktiv.is_(True))
            zeilen = sitzung.scalars(
                abfrage.order_by(InseratRow.score.desc(), InseratRow.zuletzt_gesehen.desc()).limit(limit)
            ).all()
            return [_nach_domaene(z) for z in zeilen]

    def alle(self, limit: int = 2000) -> list[Inserat]:
        with self.sitzung() as sitzung:
            zeilen = sitzung.scalars(
                select(InseratRow).order_by(InseratRow.score.desc()).limit(limit)
            ).all()
            return [_nach_domaene(z) for z in zeilen]

    def ungemeldete_treffer(self, mindestscore: int = 0) -> list[Inserat]:
        with self.sitzung() as sitzung:
            zeilen = sitzung.scalars(
                select(InseratRow)
                .where(
                    InseratRow.gemeldet.is_(False),
                    InseratRow.ausgeschlossen.is_(False),
                    InseratRow.aktiv.is_(True),
                    InseratRow.score >= mindestscore,
                )
                .order_by(InseratRow.score.desc())
            ).all()
            return [_nach_domaene(z) for z in zeilen]

    def preisverlauf(self, uid: str) -> list[Preisaenderung]:
        with self.sitzung() as sitzung:
            zeilen = sitzung.scalars(
                select(PreisRow).where(PreisRow.uid == uid).order_by(PreisRow.zeitpunkt)
            ).all()
            return [
                Preisaenderung(uid=z.uid, zeitpunkt=als_utc(z.zeitpunkt), feld=z.feld,
                               alt=z.alt, neu=z.neu)
                for z in zeilen
            ]

    def laeufe(self, limit: int = 50) -> list[LaufRow]:
        with self.sitzung() as sitzung:
            return list(
                sitzung.scalars(select(LaufRow).order_by(LaufRow.gestartet.desc()).limit(limit)).all()
            )

    def markierungen(self) -> dict[str, MarkierungRow]:
        with self.sitzung() as sitzung:
            return {z.uid: z for z in sitzung.scalars(select(MarkierungRow)).all()}


# --------------------------------------------------------------- Umwandlung

def _nach_zeile(inserat: Inserat, zeile: InseratRow) -> InseratRow:
    zeile.uid = inserat.uid
    zeile.quelle = inserat.quelle
    zeile.externe_id = inserat.externe_id
    zeile.url = inserat.url
    zeile.titel = inserat.titel
    zeile.kaltmiete = inserat.kaltmiete
    zeile.nebenkosten = inserat.nebenkosten
    zeile.warmmiete = inserat.warmmiete
    zeile.kaution = inserat.kaution
    zeile.provision = inserat.provision
    zeile.zimmer = inserat.zimmer
    zeile.flaeche = inserat.flaeche
    zeile.etage = inserat.etage
    zeile.qm_preis = inserat.qm_preis
    zeile.adresse = inserat.adresse
    zeile.stadtteil = inserat.stadtteil
    zeile.plz = inserat.plz
    zeile.lat = inserat.geo.lat
    zeile.lon = inserat.geo.lon
    zeile.geo_quelle = inserat.geo.quelle
    zeile.distanz_km = inserat.distanz_km
    zeile.einzug_ab = inserat.einzug_ab.isoformat() if inserat.einzug_ab else None
    zeile.einzug_status = inserat.einzug_status.value
    zeile.einzug_rohtext = inserat.einzug_rohtext
    zeile.frei_bis = inserat.frei_bis.isoformat() if inserat.frei_bis else None
    zeile.befristet = inserat.befristet
    zeile.detail_gelesen = inserat.detail_gelesen
    zeile.vermietertyp = inserat.vermietertyp.value
    zeile.anbieter = inserat.anbieter
    zeile.ausstattung = inserat.ausstattung.als_dict()
    zeile.merkmale = list(inserat.merkmale)
    zeile.bilder = list(inserat.bilder[:8])
    zeile.beschreibung = inserat.beschreibung[:4000]
    zeile.score = inserat.bewertung.score
    zeile.score_treffer = list(inserat.bewertung.treffer)
    zeile.score_abzuege = list(inserat.bewertung.abzuege)
    zeile.ausgeschlossen = inserat.bewertung.ausgeschlossen
    zeile.ausschlussgrund = inserat.bewertung.ausschlussgrund
    if inserat.ki:
        zeile.ki_zusammenfassung = inserat.ki.zusammenfassung
        zeile.ki_warnsignale = list(inserat.ki.warnsignale)
        zeile.ki_wg_geeignet = inserat.ki.wg_geeignet
        zeile.ki_seriositaet = inserat.ki.seriositaet
    zeile.zuletzt_gesehen = inserat.zuletzt_gesehen
    zeile.aktiv = True
    if zeile.erstmals_gesehen is None:
        zeile.erstmals_gesehen = inserat.erstmals_gesehen
    return zeile


def _nach_domaene(zeile: InseratRow) -> Inserat:
    inserat = Inserat(
        quelle=zeile.quelle,
        externe_id=zeile.externe_id,
        url=zeile.url,
        titel=zeile.titel,
        kaltmiete=zeile.kaltmiete,
        nebenkosten=zeile.nebenkosten,
        warmmiete=zeile.warmmiete,
        kaution=zeile.kaution,
        provision=zeile.provision,
        zimmer=zeile.zimmer,
        flaeche=zeile.flaeche,
        etage=zeile.etage,
        adresse=zeile.adresse,
        stadtteil=zeile.stadtteil,
        plz=zeile.plz,
        geo=Geo(lat=zeile.lat, lon=zeile.lon, quelle=zeile.geo_quelle or "unbekannt"),
        distanz_km=zeile.distanz_km,
        einzug_ab=date.fromisoformat(zeile.einzug_ab) if zeile.einzug_ab else None,
        einzug_status=Einzugsstatus(zeile.einzug_status),
        einzug_rohtext=zeile.einzug_rohtext,
        frei_bis=date.fromisoformat(zeile.frei_bis) if zeile.frei_bis else None,
        befristet=bool(zeile.befristet),
        detail_gelesen=bool(zeile.detail_gelesen),
        ausstattung=Ausstattung(**(zeile.ausstattung or {})),
        vermietertyp=Vermietertyp(zeile.vermietertyp),
        anbieter=zeile.anbieter,
        beschreibung=zeile.beschreibung or "",
        bilder=list(zeile.bilder or []),
        merkmale=list(zeile.merkmale or []),
        erstmals_gesehen=als_utc(zeile.erstmals_gesehen),
        zuletzt_gesehen=als_utc(zeile.zuletzt_gesehen),
    )
    inserat.bewertung = Bewertung(
        score=zeile.score,
        treffer=list(zeile.score_treffer or []),
        abzuege=list(zeile.score_abzuege or []),
        ausgeschlossen=zeile.ausgeschlossen,
        ausschlussgrund=zeile.ausschlussgrund,
    )
    if zeile.ki_zusammenfassung:
        inserat.ki = KiBewertung(
            zusammenfassung=zeile.ki_zusammenfassung,
            warnsignale=list(zeile.ki_warnsignale or []),
            wg_geeignet=zeile.ki_wg_geeignet,
            seriositaet=zeile.ki_seriositaet,
        )
    return inserat
