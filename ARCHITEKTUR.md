# Architektur

## Schichten

Abhängigkeiten zeigen ausschließlich nach innen. Der Kern kennt weder HTTP
noch SQL noch Streamlit.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Adapter (außen)                                                    │
│                                                                     │
│   scrapers/        notifier/       api/          dashboard/         │
│   requests,        requests,       FastAPI,      Streamlit          │
│   BeautifulSoup,   smtplib         Pydantic                         │
│   Playwright                                                        │
│                                                                     │
│   database/  models/db.py        llm/                               │
│   SQLAlchemy                     Anthropic / OpenAI SDK             │
└───────────────────────────┬─────────────────────────────────────────┘
                            │  ruft an, wird nie gerufen
┌───────────────────────────▼─────────────────────────────────────────┐
│  Anwendungsschicht                                                  │
│                                                                     │
│   services/pipeline.py    Ablauf eines Durchlaufs                   │
│   services/export.py      JSON-Auszug für die statische Seite       │
│   cli.py, scheduler.py    Einstiegspunkte                           │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────────┐
│  Kern – nur Standardbibliothek                                      │
│                                                                     │
│   models/domain.py    Inserat, Bewertung, Einzugsstatus, …          │
│   config/profil.py    Suchprofil + Validierung                      │
│   services/parsing.py Datum, Preise, Ausstattung aus Fließtext      │
│   services/geo.py     Haversine, Stadtteilzentroide                 │
│   services/dedupe.py  Ähnlichkeit, Gruppierung                      │
│   ranking/regeln.py   harte Ausschlussregeln                        │
│   ranking/scoring.py  Punktesystem                                  │
└─────────────────────────────────────────────────────────────────────┘
```

Der Kern hat **keine** Drittabhängigkeit außer PyYAML (Profil laden). Deshalb
laufen 126 Tests ohne Datenbank, ohne Netz und ohne installierte Frameworks.

Umgesetzt wird das über zwei Details: `Pipeline` importiert `Repository` nur
unter `TYPE_CHECKING`, und `LlmClient` lädt die SDKs erst im Konstruktor.

---

## Ablauf eines Durchlaufs

```
  registry.baue_alle(profil)
        │
        ▼
  ┌──────────────┐   je Quelle isoliert: eine kaputte Quelle
  │  sammeln     │   kippt nie den ganzen Lauf
  └──────┬───────┘
         │  list[Inserat]  (Rohfelder, vieles leer)
         ▼
  ┌──────────────┐   Einzugstermin aus Fließtext, Ausstattung,
  │  anreichern  │   Vermietertyp, Koordinaten, Distanz
  └──────┬───────┘
         ▼
  ┌──────────────┐   Blockbildung + gewichtete Ähnlichkeit;
  │  entdoppeln  │   je Gruppe gewinnt das vollständigste Inserat
  └──────┬───────┘
         ▼
  ┌──────────────┐   erste greifende Regel gewinnt,
  │ ausschließen │   Einzugstermin wird zuerst geprüft
  └──────┬───────┘
         │  Treffer          Verworfene (mit Grund, für die Diagnose)
         ▼                        │
  ┌──────────────┐                │
  │  bewerten    │                │
  └──────┬───────┘                │
         ▼                        │
  ┌──────────────┐  aus, sofern   │
  │  KI-Urteil   │  nicht --mit-ki│
  └──────┬───────┘  Top 8, ±10    │
         ▼                        ▼
  ┌──────────────────────────────────┐
  │  speichern (SQLite)              │  neu / aktualisiert / Preisänderung
  └──────┬───────────────────────────┘
         ├──► melden   nur wirklich neue Treffer, einmalig
         └──► export   docs/data/listings.json
```

**Warum entdoppelt wird, bevor ausgeschlossen wird:** Dieselbe Wohnung läuft
oft auf drei Portalen, mit unterschiedlich vollständigen Angaben. Würde man
erst filtern, könnte die vollständige Fassung an einer Regel scheitern, die
nur wegen einer Datenlücke greift, während die dünne Fassung durchrutscht.
Umgekehrt gewinnt immer das Inserat mit den meisten belegten Feldern.

**Warum die KI zuletzt kommt:** Sie ist der einzige Schritt, der LLM-Tokens
verbraucht. Nach dem Ausschluss bleiben typischerweise wenige Prozent der
Rohmenge übrig. Sie ist zusätzlich standardmäßig abgeschaltet, auf
`ki.max_inserate_pro_lauf` begrenzt und überspringt bereits bewertete
Inserate. Ausgelöst wird ein Lauf ausschließlich manuell – über den Knopf im
Dashboard, den Actions-Dialog oder die Kommandozeile.

---

## Datenmodell

```
inserate                    aktueller Stand je Wohnung, PK = uid
  │                         uid = sha1(quelle|externe_id)[:16]
  │
  ├── preishistorie         eine Zeile je erkannter Preisänderung
  │                         wird nur ergänzt, nie überschrieben
  └── markierungen          Favorit / gesehen / Notiz aus dem Dashboard

laeufe                      ein Datensatz je Durchlauf
                            Grundlage für Suchhistorie und Diagnose
```

Getrennt gehalten, weil `inserate` bei jedem Lauf überschrieben wird.
`preishistorie` und `laeufe` überleben das und bleiben auch erhalten, wenn ein
Inserat vom Markt verschwindet.

`erstmals_gesehen` wird beim Aktualisieren bewusst **nicht** überschrieben –
sonst wäre jedes Inserat bei jedem Lauf wieder "neu". `gemeldet` sorgt dafür,
dass keine Wohnung zweimal gepusht wird.

---

## Eine Quelle ergänzen

**Einfacher Fall** – die Seite hat schema.org-Daten oder eine simple
Trefferliste: Eintrag in `scrapers/vermieter.py` unter `BAUPLAENE`, dazu ein
Eintrag in `config/suchprofil.yml`. Kein Code.

```python
"meine_genossenschaft": QuellenBauplan(
    name="meine_genossenschaft",
    label="Wohnungsgenossenschaft Musterstadt",
    art="json_ld",
    urls=("https://example.de/mietangebote",),
),
```

**Komplexer Fall** – eigene Klasse:

```python
class NeuePortal(Scraper):
    name = "neues_portal"
    label = "Neues Portal"
    basis_url = "https://example.de"

    def suchseiten(self):
        yield Suchseite(f"{self.basis_url}/suche?ort=frankfurt", "Seite 1")

    def parse_seite(self, html, seite):
        return [...]   # list[Inserat] mit Rohfeldern
```

Danach in `scrapers/registry.py` unter `KLASSEN` eintragen. robots.txt,
Drosselung, Retry, Fehlerisolierung und Normalisierung liegen in `base.py`
und `pipeline.py` – die neue Klasse muss davon nichts wissen.

---

## Einen Benachrichtigungskanal ergänzen

Unterklasse von `Kanal` in `notifier/kanaele.py`, `aktiv` und `sende`
implementieren, in `KANAELE` eintragen. Ein Kanal ist genau dann aktiv, wenn
seine Umgebungsvariablen gesetzt sind – es gibt keinen zweiten Schalter.

---

## Wo Entscheidungen nachlesbar sind

| Frage | Datei |
|---|---|
| Warum wird ein Inserat aussortiert? | `ranking/regeln.py`, Reihenfolge in `REGELN` |
| Wie kommt der Score zustande? | `ranking/scoring.py`, `KRITERIEN` |
| Welche Datumsformate werden erkannt? | `services/parsing.py`, dazu `tests/test_parsing.py` |
| Warum ist eine Quelle deaktiviert? | `scrapers/robots.py` und das Lauf-Log |
| Warum diese Abwägung? | `ANNAHMEN.md` |
