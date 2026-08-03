"""Streamlit-Dashboard.

Start:  streamlit run dashboard/app.py

Liest direkt aus SQLite. Favoriten und Gesehen-Markierungen werden über das
Repository zurückgeschrieben, überleben also einen Neustart und sind auch für
die Benachrichtigungen sichtbar.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import streamlit as st

from wohnungsagent.config.profil import lade_profil
from wohnungsagent.database.repository import Repository

DB_URL = os.getenv("DATENBANK_URL", "sqlite:///data/wohnungen.db")
PROFIL_PFAD = os.getenv("PROFIL_PFAD", "config/suchprofil.yml")

st.set_page_config(page_title="Wohnungsagent Frankfurt", page_icon="🔑", layout="wide")


@st.cache_resource
def hole_repo() -> Repository:
    return Repository(DB_URL)


@st.cache_data(ttl=60)
def hole_daten() -> tuple[pd.DataFrame, dict]:
    repo = hole_repo()
    inserate = repo.alle(limit=2000)
    markierungen = repo.markierungen()
    laeufe = repo.laeufe(limit=40)

    zeilen = []
    for inserat in inserate:
        eintrag = inserat.als_dict()
        markierung = markierungen.get(inserat.uid)
        eintrag["favorit"] = bool(markierung and markierung.favorit)
        eintrag["gesehen"] = bool(markierung and markierung.gesehen)
        eintrag["notiz"] = markierung.notiz if markierung else None
        zeilen.append(eintrag)

    tabelle = pd.DataFrame(zeilen)
    statistik = {
        "laeufe": [
            {
                "zeitpunkt": lauf.gestartet,
                "roh": lauf.roh_gefunden,
                "treffer": lauf.treffer,
                "neu": lauf.neu,
                "ki_aufrufe": lauf.ki_aufrufe,
                "ki_tokens": lauf.ki_tokens,
                "fehler": lauf.quellen_fehler,
                "robots": lauf.quellen_robots_blockiert,
            }
            for lauf in laeufe
        ]
    }
    return tabelle, statistik


def geld(wert) -> str:
    return f"{wert:,.0f} €".replace(",", ".") if pd.notna(wert) and wert else "–"


# ------------------------------------------------------------------ Kopf

profil = lade_profil(PROFIL_PFAD)
tabelle, statistik = hole_daten()

st.title("Wohnungsagent Frankfurt")

# ---------------------------------------------------------------- Suchknopf
#
# Der Agent läuft nicht selbstständig. Gesucht wird, wenn du hier drückst.
# Das Scrapen kostet nichts; Tokens verbraucht ausschließlich der optionale
# KI-Schritt, deshalb ist er ein eigener Haken mit sichtbarem Limit.

with st.container(border=True):
    knopf, ki_an, ki_zahl, hinweis = st.columns([1.4, 1.1, 1, 2.5])

    with ki_an:
        mit_ki = st.checkbox("mit KI", value=False, help="Erzeugt Zusammenfassungen und Warnsignale. Verbraucht LLM-Tokens.")
    with ki_zahl:
        ki_limit = st.number_input(
            "max. Inserate", min_value=1, max_value=30,
            value=int(profil.ki.max_inserate_pro_lauf), step=1, disabled=not mit_ki,
            label_visibility="visible",
        )
    with hinweis:
        if mit_ki:
            st.caption(
                f"Schätzung: bis zu {int(ki_limit)} Aufrufe, grob "
                f"{int(ki_limit) * 1500:,} Tokens.".replace(",", ".")
                + " Bereits bewertete Inserate werden übersprungen."
            )
        else:
            st.caption("Ohne KI: null Tokenverbrauch. Ranking, Filter und Meldungen funktionieren trotzdem vollständig.")

    with knopf:
        gestartet = st.button("Jetzt suchen", type="primary", use_container_width=True)

if gestartet:
    befehl = [sys.executable, "-m", "wohnungsagent.cli", "scan", "-v"]
    if mit_ki:
        befehl += ["--mit-ki", "--ki-limit", str(int(ki_limit))]

    protokoll = st.empty()
    with st.status("Suche läuft – das dauert je nach Quellenzahl ein bis drei Minuten", expanded=True) as status:
        lauf = subprocess.run(
            befehl, capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        ausgabe = (lauf.stderr or "") + (lauf.stdout or "")
        protokoll.code(ausgabe[-4000:] or "(keine Ausgabe)", language="text")
        if lauf.returncode == 0:
            status.update(label="Suche abgeschlossen", state="complete")
            hole_daten.clear()
        else:
            status.update(label=f"Suche fehlgeschlagen (Code {lauf.returncode})", state="error")
    if lauf.returncode == 0:
        st.rerun()

if tabelle.empty:
    st.info(
        "Noch keine Daten. Einmal `python -m wohnungsagent.cli scan` ausführen, "
        "dann diese Seite neu laden."
    )
    st.stop()

treffer_alle = tabelle[~tabelle["ausgeschlossen"]]
jung = pd.to_datetime(tabelle["erstmals_gesehen"], format="mixed", utc=True) > (
    datetime.now(UTC) - timedelta(hours=24)
)

spalten = st.columns(6)
spalten[0].metric("Treffer", len(treffer_alle))
spalten[1].metric("neu, 24 h", int((jung & ~tabelle["ausgeschlossen"]).sum()))
spalten[2].metric("Favoriten", int(tabelle["favorit"].sum()))
spalten[3].metric(
    "Median Warmmiete", geld(treffer_alle["warmmiete"].median()) if len(treffer_alle) else "–"
)
letzter_lauf = statistik["laeufe"][0] if statistik["laeufe"] else None
spalten[4].metric(
    "letzter Lauf",
    letzter_lauf["zeitpunkt"].strftime("%d.%m. %H:%M") if letzter_lauf else "–",
)
spalten[5].metric(
    "Tokens letzter Lauf",
    f"{letzter_lauf['ki_tokens']:,}".replace(",", ".") if letzter_lauf and letzter_lauf["ki_tokens"] else "0",
)

if statistik["laeufe"]:
    aktuell = statistik["laeufe"][0]
    if aktuell["robots"]:
        st.warning(
            "Durch robots.txt gesperrt: " + ", ".join(aktuell["robots"])
            + ". Das ist erwartetes Verhalten – siehe README, Abschnitt Quellen."
        )
    if aktuell["fehler"]:
        st.error("Quellen mit Fehlern: " + ", ".join(aktuell["fehler"]))

# --------------------------------------------------------------- Filter

with st.sidebar:
    st.header("Filter")
    nur_favoriten = st.checkbox("nur Favoriten")
    ungesehen = st.checkbox("nur ungesehene")
    mit_ausgeschlossenen = st.checkbox("aussortierte anzeigen", help="zum Kalibrieren der Regeln")

    miete_max = st.slider(
        "Warmmiete bis",
        800, 2500, int(profil.budget.warmmiete_max), step=50,
    )
    zimmer_min = st.slider("Zimmer ab", 1.0, 5.0, float(profil.wohnung.zimmer_min), step=0.5)
    flaeche_min = st.slider("Fläche ab (m²)", 30, 150, int(profil.wohnung.flaeche_min), step=5)
    distanz_max = st.slider(
        "Entfernung zur Frankfurt School (km)", 0.5, 10.0, float(profil.referenzpunkt.max_km), step=0.5
    )
    score_min = st.slider("Score ab", 0, 100, 0, step=5)

    einzug_modi = st.multiselect(
        "Einzugstermin",
        ["passt", "unbekannt", "zu_frueh"],
        default=["passt", "unbekannt"],
    )
    stadtteile = st.multiselect(
        "Stadtteil", sorted(tabelle["stadtteil"].dropna().unique().tolist())
    )
    quellen = st.multiselect("Quelle", sorted(tabelle["quelle"].unique().tolist()))

gefiltert = tabelle if mit_ausgeschlossenen else treffer_alle
maske = (
    (gefiltert["score"] >= score_min)
    & (gefiltert["warmmiete"].fillna(0) <= miete_max)
    & (gefiltert["zimmer"].fillna(99) >= zimmer_min)
    & (gefiltert["flaeche"].fillna(999) >= flaeche_min)
    & (gefiltert["distanz_km"].fillna(0) <= distanz_max)
    & (gefiltert["einzug_status"].isin(einzug_modi))
)
if nur_favoriten:
    maske &= gefiltert["favorit"]
if ungesehen:
    maske &= ~gefiltert["gesehen"]
if stadtteile:
    maske &= gefiltert["stadtteil"].isin(stadtteile)
if quellen:
    maske &= gefiltert["quelle"].isin(quellen)

sicht = gefiltert[maske].sort_values("score", ascending=False)

# ---------------------------------------------------------------- Reiter

reiter = st.tabs(["Top-Treffer", "Neu", "Karte", "Preisentwicklung", "Suchhistorie", "Aussortiert"])

with reiter[0]:
    st.caption(f"{len(sicht)} Inserate")
    for _, zeile in sicht.head(50).iterrows():
        with st.container(border=True):
            links, rechts = st.columns([4, 1])
            with links:
                st.markdown(f"### [{zeile['titel'][:110]}]({zeile['url']})")
                fakten = [
                    geld(zeile["warmmiete"]) + " warm",
                    f"{zeile['zimmer']:g} Zimmer" if pd.notna(zeile["zimmer"]) else "Zimmer ?",
                    f"{zeile['flaeche']:.0f} m²" if pd.notna(zeile["flaeche"]) else "Fläche ?",
                    f"{zeile['distanz_km']:.1f} km zur FS" if pd.notna(zeile["distanz_km"]) else "",
                    zeile["stadtteil"] or "",
                ]
                st.write(" · ".join(f for f in fakten if f))

                if zeile["einzug_ab"]:
                    st.success(f"Einzug ab {zeile['einzug_ab']}", icon="📅")
                elif zeile["einzug_status"] == "unbekannt":
                    st.warning("Einzugstermin nicht angegeben – vor Kontaktaufnahme klären", icon="❓")

                if zeile["ki_zusammenfassung"]:
                    st.info(zeile["ki_zusammenfassung"], icon="🤖")
                if zeile["ki_warnsignale"]:
                    st.error("Warnsignale: " + "; ".join(zeile["ki_warnsignale"]), icon="⚠️")

                with st.expander("Bewertung im Detail"):
                    st.write("**Punkte:** " + ", ".join(zeile["score_treffer"] or ["–"]))
                    if zeile["score_abzuege"]:
                        st.write("**Abzüge:** " + ", ".join(zeile["score_abzuege"]))
                    st.caption(
                        f"Quelle {zeile['quelle']} · Vermieter {zeile['vermietertyp']} · "
                        f"Koordinaten aus {zeile['geo_quelle']}"
                    )

            with rechts:
                st.metric("Score", int(zeile["score"]))
                if zeile["bilder"]:
                    st.image(zeile["bilder"][0], use_container_width=True)
                favorit = st.checkbox("Favorit", value=bool(zeile["favorit"]), key=f"f{zeile['uid']}")
                gesehen = st.checkbox("gesehen", value=bool(zeile["gesehen"]), key=f"g{zeile['uid']}")
                if favorit != zeile["favorit"] or gesehen != zeile["gesehen"]:
                    hole_repo().setze_markierung(zeile["uid"], favorit=favorit, gesehen=gesehen)
                    hole_daten.clear()

with reiter[1]:
    neu = sicht[
        pd.to_datetime(sicht["erstmals_gesehen"], format="mixed", utc=True)
        > (datetime.now(UTC) - timedelta(hours=48))
    ]
    st.caption(f"{len(neu)} Inserate der letzten 48 Stunden")
    st.dataframe(
        neu[["score", "titel", "warmmiete", "zimmer", "flaeche", "distanz_km",
             "stadtteil", "einzug_ab", "quelle", "url"]],
        use_container_width=True,
        column_config={"url": st.column_config.LinkColumn("Link", display_text="öffnen")},
        hide_index=True,
    )

with reiter[2]:
    karte = sicht.dropna(subset=["lat", "lon"])
    if karte.empty:
        st.info("Keine Koordinaten vorhanden.")
    else:
        referenz = pd.DataFrame(
            [{"lat": profil.referenzpunkt.lat, "lon": profil.referenzpunkt.lon}]
        )
        st.map(pd.concat([karte[["lat", "lon"]], referenz]), size=40)
        st.caption(
            "Der einzelne Punkt ohne Umgebung ist die Frankfurt School. "
            "Inserate ohne Straßenangabe sitzen auf dem Stadtteilschwerpunkt "
            "(±700 m), nicht auf der echten Adresse."
        )

with reiter[3]:
    repo = hole_repo()
    mit_historie = [
        (zeile["uid"], zeile["titel"]) for _, zeile in sicht.iterrows()
        if repo.preisverlauf(zeile["uid"])
    ]
    if not mit_historie:
        st.info("Noch keine Preisänderungen erfasst. Die entstehen erst über mehrere Läufe.")
    else:
        auswahl = st.selectbox(
            "Inserat", mit_historie, format_func=lambda p: p[1][:80]
        )
        verlauf = pd.DataFrame(
            [{"Zeitpunkt": a.zeitpunkt, "Feld": a.feld, "vorher": a.alt, "nachher": a.neu}
             for a in repo.preisverlauf(auswahl[0])]
        )
        st.dataframe(verlauf, use_container_width=True, hide_index=True)
        st.line_chart(verlauf.set_index("Zeitpunkt")["nachher"])

with reiter[4]:
    verlauf = pd.DataFrame(statistik["laeufe"])
    if verlauf.empty:
        st.info("Noch keine Läufe protokolliert.")
    else:
        st.line_chart(verlauf.set_index("zeitpunkt")[["roh", "treffer", "neu"]])
        gesamt = int(verlauf["ki_tokens"].sum())
        st.caption(
            f"Tokenverbrauch aller protokollierten Läufe: {gesamt:,}".replace(",", ".")
            + f" über {int(verlauf['ki_aufrufe'].sum())} KI-Aufrufe."
        )
        st.dataframe(verlauf, use_container_width=True, hide_index=True)

with reiter[5]:
    aussortiert = tabelle[tabelle["ausgeschlossen"]]
    st.caption(
        f"{len(aussortiert)} Inserate wurden von den Regeln aussortiert. "
        "Nützlich, um zu prüfen, ob die Filter zu streng stehen."
    )
    if not aussortiert.empty:
        st.bar_chart(
            aussortiert["ausschlussgrund"]
            .str.split(" (", regex=False).str[0]
            .value_counts()
            .head(10)
        )
        st.dataframe(
            aussortiert[["titel", "ausschlussgrund", "warmmiete", "zimmer", "stadtteil", "url"]],
            use_container_width=True,
            column_config={"url": st.column_config.LinkColumn("Link", display_text="öffnen")},
            hide_index=True,
        )
