# Wohnungsagent Frankfurt

Durchsucht **auf Knopfdruck** Wohnungsportale, erkennt Dubletten über
Portalgrenzen hinweg, sortiert nach deinem Suchprofil aus, bewertet die Reste
mit einem nachvollziehbaren Punktesystem und schickt neue Treffer per Telegram,
Discord, Slack oder E-Mail.

Es läuft nichts nach Zeitplan und nichts im Hintergrund. Gesucht wird, wenn du
den Knopf drückst.

Voreingestellt auf: **Nordend / Westend-Nord, höchstens 3 km zur Frankfurt
School, 3 Zimmer ab 70 m², Warmmiete 1.300–1.800 €, Einzug frühestens
01.01.2027.** Alles davon steht in `config/suchprofil.yml` und braucht keine
Codeänderung.

```
config/suchprofil.yml   ──►  scrapers  ──►  anreichern  ──►  entdoppeln
                                                                  │
   Telegram ◄── melden ◄── SQLite ◄── KI ◄── bewerten ◄── ausschließen
                             │
                             ├──► Streamlit-Dashboard (Karte, Favoriten, Verlauf)
                             └──► GitHub Pages (statische Ansicht)
```

Details zum Aufbau: [ARCHITEKTUR.md](ARCHITEKTUR.md).
Warum welche Abwägung getroffen wurde: [ANNAHMEN.md](ANNAHMEN.md).

---

## Was kostet ein Lauf

Zwei Dinge werden leicht verwechselt, sie sind aber unabhängig voneinander:

| | Was es verbraucht | Wann |
|---|---|---|
| **Suchen, filtern, ranken, melden** | GitHub-Actions-Minuten (auf öffentlichen Repos frei), sonst nichts | bei jedem Lauf |
| **KI-Zusammenfassungen** | LLM-Tokens, grob 1.500 je Inserat | nur wenn ausdrücklich eingeschaltet |

**Der gesamte Agent funktioniert ohne die KI vollständig.** Die Distanzberechnung,
die Einzugsterminerkennung, das Punktesystem, die Duplikaterkennung und die
Benachrichtigungen sind deterministischer Code und kosten null Tokens. Ohne KI
fehlen nur die Textzusammenfassung, die WG-Einschätzung und die Warnsignale.

Die KI ist deshalb dreifach gebremst:

1. Sie ist standardmäßig aus (`ki.aktiv: false`), auch mit hinterlegtem Schlüssel.
2. Sie läuft erst **nach** den Ausschlussregeln, also auf wenigen Prozent der Rohmenge.
3. Sie bewertet höchstens `ki.max_inserate_pro_lauf` Inserate (Standard 8) und
   überspringt alles, was schon eine Bewertung hat.

Ein Lauf mit KI kostet damit typischerweise etwa 12.000 Tokens, ein Lauf ohne
KI null. Der tatsächliche Verbrauch steht nach jedem Lauf im Dashboard, in der
Actions-Zusammenfassung und in der Suchhistorie.

---

## Vorher lesen: drei Dinge, die dich sonst überraschen

**1. Du bist sehr früh dran.** Der deutsche Mietmarkt inseriert vier bis acht
Wochen vor Bezug. Für Januar 2027 heißt das: die relevanten Inserate kommen ab
Ende Oktober 2026. Bis dahin findet der Agent fast nur "ab sofort" und sortiert
es korrekt aus. Die leere Trefferliste ist dann kein Fehler.

**2. Die großen Portale wehren sich, aber anders als erwartet.** ImmoScout24
steht hinter einer AWS-Web-Application-Firewall: Anfragen ohne Browser-Fingerprint
werden mit HTTP 401 abgewiesen, oft schon bevor eine robots.txt gelesen werden
kann. Was `make pruefe` bei dir meldet, ist deshalb die maßgebliche Auskunft –
nicht meine Vorhersage. Steht dort *NICHT ABRUFBAR*, greift Bot-Schutz und du
solltest bei diesen Quellen keine Treffer erwarten. Der Großteil kommt in der
Praxis aus Kleinanzeigen, WG-Gesucht und den Vermietern.

**3. Die ABG erreicht kein Scraper.** Der größte Vermieter Frankfurts
(~54.000 Wohnungen, oft deutlich unter Marktmiete) inseriert nicht, sondern
vergibt per Interessentenliste und Losverfahren. Das
[Interessentenformular](https://www.abg.de/mieten/faire-wohnungsvergabe/interessentenformular/)
solltest du jetzt ausfüllen, nicht im November. Gleiches gilt für die
Nassauische Heimstätte und die Frankfurter Genossenschaften.

---

## Installation

Python 3.11 oder neuer.

```bash
git clone <dein-repo> wohnungsagent && cd wohnungsagent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .

cp .env.example .env      # Schlüssel eintragen, alles optional
```

Erste Schritte in dieser Reihenfolge:

```bash
make pruefe        # Profil validieren, robots.txt je Quelle abfragen – kein Scraping
make test          # 126 Tests
make probelauf     # sucht einmal, speichert und meldet aber nichts
make scan          # echter Lauf, ohne KI – null Tokens
make scan-ki       # echter Lauf mit KI-Zusammenfassungen
make dashboard     # http://localhost:8501 – hier sitzt der Suchknopf
```

`make pruefe` zeigt dir vor dem ersten Scraping, welche Quellen überhaupt
erlaubt sind. Das erspart die Ratlosigkeit, wenn ein Lauf wenig liefert.

### Die drei Wege, eine Suche auszulösen

**Dashboard.** `make dashboard`, dann oben auf **Jetzt suchen**. Daneben ein
Haken „mit KI" samt Limit und einer Schätzung, was das an Tokens kostet. Das
Protokoll läuft live mit, danach aktualisiert sich die Liste selbst. Das ist
der bequemste Weg.

**GitHub Actions.** Reiter *Actions* › *Wohnungen suchen* › *Run workflow*. Im
Dialog stehen dieselben Schalter. Die statische Seite auf GitHub Pages hat oben
einen Knopf, der direkt dorthin führt.

**Kommandozeile.**

```bash
make scan          # ohne KI
make scan-ki       # mit KI
python -m wohnungsagent.cli scan --mit-ki --ki-limit 5
```

### Mit Docker

```bash
cp .env.example .env
docker compose up -d                          # nur das Dashboard auf :8501
docker compose run --rm agent scan            # einmal suchen, ohne KI
docker compose run --rm agent scan --mit-ki   # einmal suchen, mit KI
docker compose --profile api up -d            # REST-Schnittstelle auf :8000
```

Es gibt keinen Dienst, der von allein sucht. Falls du das später doch willst:
`docker compose --profile dauerbetrieb up -d` bzw. den `schedule`-Block in
`.github/workflows/scan.yml` einkommentieren – vier Zeilen, im Kopf der Datei
beschrieben.

Playwright wird nur gebraucht, wenn du Immowelt/Immonet aktiv nutzen willst
(spart sonst rund 400 MB im Image): in `docker-compose.yml`
`MIT_PLAYWRIGHT: "true"` setzen.

### Mit GitHub Actions

1. Repo **öffentlich** anlegen – bei privaten Repos verbrauchen stündliche
   Läufe schnell die Actions-Freiminuten. Im Code stehen keine Geheimnisse.
2. Settings › Actions › General › Workflow permissions: *Read and write*.
3. Settings › Pages › Source: *Deploy from a branch*, Branch `main`, Ordner
   `/docs`.
4. Settings › Secrets and variables › Actions: die Werte aus `.env.example`
   eintragen, die du brauchst.
5. Actions › *Wohnungen suchen* › *Run workflow*.

Der Workflow hat **keinen Zeitplan**. Er läuft ausschließlich, wenn du ihn
auslöst. Damit fällt zwischen zwei Suchen weder Rechenzeit noch Tokenverbrauch
an.

---

## Benachrichtigungen

Jeder Kanal schaltet sich selbst frei, sobald seine Variablen gesetzt sind.
Telegram ist am schnellsten eingerichtet:

1. [@BotFather](https://t.me/BotFather) anschreiben, `/newbot`, Token kopieren.
2. Dem neuen Bot einmal selbst schreiben, sonst darf er nicht antworten.
3. `https://api.telegram.org/bot<TOKEN>/getUpdates` öffnen, `message.chat.id` ablesen.
4. `TELEGRAM_TOKEN` und `TELEGRAM_CHAT_ID` in `.env` bzw. in die Actions-Secrets.

Discord und Slack brauchen nur eine Webhook-URL, E-Mail die üblichen
SMTP-Zugangsdaten. Push (ntfy, Pushover) ergänzt man als weitere Unterklasse
in `notifier/kanaele.py` – siehe ARCHITEKTUR.md.

Gemeldet wird jede Wohnung **genau einmal**, gesteuert über das Feld
`gemeldet` in der Datenbank. Mit `MINDESTSCORE_MELDUNG` legst du fest, ab
welchem Score überhaupt gepusht wird.

---

## Quellen

| Quelle | Priorität | Zustand | Anmerkung |
|---|---|---|---|
| Kleinanzeigen | A | funktioniert | höchster Anteil privater Inserate |
| ImmobilienScout24 | A | robots.txt sperrt | API-Schlüssel oder Suchagent, siehe unten |
| Immowelt | A | robots.txt sperrt | braucht zusätzlich Playwright |
| Immonet | A | robots.txt sperrt | gleiche Plattform wie Immowelt |
| WG-Gesucht | B | funktioniert | auch komplette Wohnungen, nicht nur Zimmer |
| Wohnungsboerse | B | CSS-Selektoren ungeprüft | über `BAUPLAENE` konfiguriert |
| Wohnung-jetzt | B | aus, ungeprüft | in `suchprofil.yml` aktivierbar |
| Vonovia | C | JSON-API | |
| GWH, Nassauische Heimstätte | C | JSON-LD | |
| LEG, Adler | C | aus | aktivierbar, URLs ungeprüft |
| ABG | C | Hinweisquelle | kein Scraper möglich, siehe oben |

### ImmoScout24 doch nutzen

Drei Wege, in dieser Reihenfolge zu empfehlen:

1. **Partner-API.** Mit `IS24_API_KEY` in der `.env` nutzt das Modul die
   offizielle Schnittstelle. robots.txt ist dann irrelevant.
2. **Suchagent + eigenes Postfach.** Im Portal einen Suchagenten anlegen, die
   Benachrichtigungsmails an eine eigene Adresse leiten und per IMAP auslesen.
   Du liest damit deine eigenen Mails – rechtlich unproblematisch.
3. **`robots_pflicht: false`** in `config/suchprofil.yml`. Technisch möglich,
   die Entscheidung liegt bewusst bei dir und nicht versteckt im Code.

### Neue Quelle ergänzen

Für Genossenschaften, lokale Makler oder Portale mit einfacher Trefferliste
reicht ein Eintrag in `scrapers/vermieter.py` unter `BAUPLAENE` – kein Code.
Für alles andere eine Unterklasse von `Scraper`. Beides ist in
[ARCHITEKTUR.md](ARCHITEKTUR.md) mit Beispiel beschrieben. Facebook-Gruppen,
HousingAnywhere, Wunderflats und Homelike passen in dasselbe Schema.

---

## Wenn eine Quelle nichts liefert

Der Agent bricht nie ab; eine Quelle mit Problemen taucht im Log, im Dashboard
und in der Actions-Zusammenfassung auf.

| Meldung | Ursache | Was tun |
|---|---|---|
| `robots.txt untersagt …` | so gewollt | siehe oben, oder Schalter umlegen |
| `HTTP 403 / 429` | Bot-Schutz gegen die Runner-IP | Intervall verlängern oder lokal per Docker laufen lassen – von einer Wohnadresse aus greift der Schutz kaum |
| **0 Treffer, kein Fehler** | Markup hat sich geändert | Seite im Browser öffnen, Element inspizieren, `parse_seite` in `scrapers/<quelle>.py` anpassen |
| `benötigt Playwright` | Immowelt/Immonet ohne Browser | `pip install playwright && playwright install chromium` |

Wenn *alle* Quellen leer bleiben, sieh zuerst in den Reiter **Aussortiert** im
Dashboard. Dort steht, an welcher Regel wie viele Inserate scheitern – meist
ist es der Einzugstermin, und das ist im Sommer 2026 die richtige Antwort.

---

## Ranking

Die Sterne aus dem Suchprofil sind in Rohpunkte übersetzt und stehen alle in
`config/suchprofil.yml`:

| Kriterium | Punkte |
|---|---:|
| Nordend / Westend-Nord | 25 |
| unter 2 km zur Frankfurt School | 18 |
| 3 Zimmer | 18 |
| Warmmiete im Budgetkorridor | 18 |
| Einzugstermin belegt ab Stichtag | 18 |
| Balkon, Keller, Einbauküche | je 10 |
| gute Beschreibung, gute Bilder, privater Vermieter | je 10 |

Der Score ist der erreichte Anteil an den **prüfbaren** Punkten, gedämpft nach
Datenabdeckung, minus Abzüge für Lücken und unbestätigten Termin, plus die
KI-Korrektur von höchstens ±10. Jede einzelne Position steht im Dashboard
unter "Bewertung im Detail" – es gibt keine unsichtbaren Gewichte.

---

## Tests

```bash
make test                     # 126 Tests, mit Abdeckungsbericht
pytest tests/test_parsing.py  # nur die Datumserkennung
```

Der Kern hat keine Framework-Abhängigkeit, deshalb laufen die Tests ohne
Datenbank und ohne Netz. Schwerpunkt liegt auf der Erkennung des
Einzugstermins (alle unterstützten Schreibweisen sind als Testfall belegt),
den Ausschlussregeln und dem Duplikatabgleich.

Ungetestet gegen die Wirklichkeit sind die CSS-Selektoren der Portale und die
Datenbankschicht – siehe [ANNAHMEN.md](ANNAHMEN.md), Abschnitt 10.

---

## Befehle

```bash
python -m wohnungsagent.cli pruefe                 # Profil + robots.txt prüfen, kein Scraping
python -m wohnungsagent.cli scan                   # ein Durchlauf, ohne KI (0 Tokens)
python -m wohnungsagent.cli scan --mit-ki          # mit KI-Zusammenfassungen
python -m wohnungsagent.cli scan --mit-ki --ki-limit 3
python -m wohnungsagent.cli scan --dry-run -v      # ohne Speichern, Melden und KI
python -m wohnungsagent.cli scan --quelle kleinanzeigen
python -m wohnungsagent.cli scan --geocoding       # Adressen über Nominatim auflösen
python -m wohnungsagent.cli top -n 20              # beste Treffer im Terminal
python -m wohnungsagent.cli export                 # JSON für GitHub Pages
python -m wohnungsagent.cli dienst                 # optionaler Dauerbetrieb
```

---

## Rechtliches

Der Agent ruft öffentlich zugängliche Suchergebnisseiten für den privaten
Eigenbedarf ab. Er identifiziert sich mit einem eigenen User-Agent, hält
robots.txt und `Crawl-delay` ein, drosselt je Host und wiederholt fehlerhafte
Anfragen mit wachsendem Abstand. Offizielle Schnittstellen haben Vorrang, wo
sie existieren.

Die Nutzungsbedingungen der meisten Portale untersagen automatisierte Abrufe
unabhängig davon. Die Frequenz deutlich hochzudrehen, `robots_pflicht`
abzuschalten oder die gesammelten Daten weiterzuveröffentlichen ist etwas
anderes als der hier voreingestellte Betrieb – diese Entscheidungen liegen
bewusst offen in der Konfiguration und nicht versteckt im Code.
