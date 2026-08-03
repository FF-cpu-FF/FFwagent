# Annahmen und Entscheidungen

Der Auftrag ließ an mehreren Stellen Spielraum. Hier steht, wie ich ihn
genutzt habe und woran du das jeweils drehen kannst.

---

## 1. Der Zeitpunkt ist die größte Einschränkung, nicht die Technik

Du suchst im **August 2026** eine Wohnung zum Einzug ab **Januar 2027**. Der
deutsche Mietmarkt inseriert typischerweise **vier bis acht Wochen vor
Bezug**. Für Januar 2027 heißt das: die passenden Inserate erscheinen ab
etwa **Ende Oktober 2026**, der Großteil im November und Dezember.

Bis dahin wird der Agent fast ausschließlich Inserate mit "ab sofort" finden
und korrekt aussortieren. Das ist kein Defekt – aber es heißt, dass die
Trefferliste monatelang leer bleibt und du das nicht als Fehler fehldeuten
solltest.

Was in der Zwischenzeit trotzdem hilft:

- Der Agent läuft weiter und protokolliert, **wie viele** Inserate an welcher
  Regel scheitern. Nach ein paar Wochen weißt du, ob dein Budget realistisch
  ist und wie viele Wohnungen im Nordend überhaupt monatlich auf den Markt
  kommen. Der Reiter "Aussortiert" im Dashboard ist dafür da.
- Die Vergabeverfahren mit Vorlauf (ABG, Nassauische Heimstätte,
  Genossenschaften) laufen über Wartelisten. Dort solltest du **jetzt**
  eingetragen sein, nicht im November.

---

## 2. "Einzug nicht vor 01.01.2027" bei Inseraten ohne Datum

Das Kriterium hat laut Auftrag höchste Priorität. Nur: ein erheblicher Teil
der Inserate nennt gar keinen Termin, sondern schreibt "nach Vereinbarung"
oder gar nichts.

Konsequente Auslegung wäre, diese auszuschließen. Das würde aber gerade die
interessanten Fälle treffen – bei "nach Vereinbarung" ist ein Termin in vier
Monaten oft **verhandelbar**, während "ab sofort" eine harte Absage ist.

**Entscheidung:** Der Einzugsstatus ist dreiwertig – `passt`, `zu_frueh`,
`unbekannt`. Nur `zu_frueh` fliegt raus. `unbekannt` bleibt drin, wird im
Dashboard sichtbar markiert und verliert 12 Punkte im Ranking.

Umstellbar in `config/suchprofil.yml`:

```yaml
einzug:
  unbekannt: pruefen        # ausschliessen | pruefen | ignorieren
  malus_unbekannt: 12
```

Auf `ausschliessen` stellen, wenn dir die Liste zu voll wird – rechne dann
aber damit, dass sie sehr kurz wird.

---

## 3. robots.txt gegen Priorität A

Der Auftrag verlangt zweierlei, was sich widersprechen kann: ImmoScout24,
Immowelt und Immonet sollen durchsucht werden **und** robots.txt soll
respektiert werden.

**Korrektur zur ersten Fassung dieses Dokuments:** Ich hatte behauptet, diese
Portale würden die Suchpfade in ihrer robots.txt untersagen. Das war
ungeprüft. Der tatsächliche Blocker ist bei ImmoScout24 eine
AWS-Web-Application-Firewall, die Anfragen anhand des Browser-Fingerprints
abweist – teils schon beim Abruf der robots.txt selbst. `make pruefe` zeigt
deshalb jetzt drei getrennte Zustände an (*erlaubt*, *NICHT ABRUFBAR*,
*GESPERRT*) statt pauschal "erlaubt"; nur die Ausgabe auf deiner Maschine ist
maßgeblich.

**Entscheidung:** robots.txt gewinnt. Jede Quelle hat einen Schalter
`robots_pflicht`; steht er auf `true` und untersagt robots.txt den Pfad,
deaktiviert sich die Quelle für diesen Lauf und meldet das im Log, im
Dashboard und in der Actions-Zusammenfassung. Der Parser bleibt vollständig
implementiert – die Entscheidung, ihn ohne robots-Prüfung laufen zu lassen,
liegt bei dir und nicht im Code versteckt.

Saubere Alternativen, die im Code vorbereitet sind:

- **ImmoScout24 Partner-API.** Liegt `IS24_API_KEY` vor, nutzt das Modul die
  offizielle Schnittstelle und robots.txt ist irrelevant.
- **Suchagent des Portals + eigenes Postfach.** Du legst im Portal einen
  Suchagenten an, leitest die Mails an eine eigene Adresse und liest sie per
  IMAP aus. Rechtlich unproblematisch, weil du deine eigenen Mails liest.

Erwartung setzen: mit `robots_pflicht: true` liefern die Priorität-A-Portale
außer Kleinanzeigen voraussichtlich nichts. Der Großteil der Treffer kommt
dann von Kleinanzeigen, WG-Gesucht und den Vermietern.

---

## 4. ABG Frankfurt Holding ist kein Scraper-Problem

Die ABG hält rund 54.000 Wohnungen in Frankfurt, häufig deutlich unter
Marktmiete, und ist damit auf dem Papier die wichtigste Quelle der
Priorität C. Sie **inseriert keine Einzelwohnungen**: die Vergabe läuft über
eine Interessentenliste und ein Losverfahren.

**Entscheidung:** Statt einen Scraper zu bauen, der nichts finden kann, gibt
das ABG-Modul einmal pro Lauf einen Hinweis mit dem Link zum
Interessentenformular aus. Ein Scraper, der so tut, als könnte er dort
suchen, wäre die schlechtere Antwort.

---

## 5. Koordinaten ohne Straßenangabe

Der 3-km-Radius braucht Koordinaten, aber viele Inserate nennen nur den
Stadtteil. Ein Geocoding-Dienst hilft dann nicht.

**Entscheidung:** dreistufig, absteigend nach Genauigkeit:

| Datenlage | Quelle | Genauigkeit |
|---|---|---|
| volle Adresse | Nominatim, wenn `GEOCODING_AKTIV=true` | ±80 m |
| Stadtteil | eingebaute Zentroidtabelle | ±700 m |
| nur PLZ | PLZ-Tabelle | ±1200 m |

Die Ungenauigkeit wird mitgeführt: die Radius-Ausschlussregel schlägt sie als
Toleranz auf, damit ein Inserat am Rand des Nordends nicht an einem
Zentroidfehler scheitert. Nominatim ist standardmäßig aus, weil es auf eine
Anfrage pro Sekunde begrenzt ist und die Läufe spürbar verlängert.

Gemessen wird **Luftlinie**. Fußweg und ÖPNV liegen erfahrungsgemäß 20 bis
30 % darüber; 3 km Luftlinie sind also grob 25 bis 35 Gehminuten.

---

## 6. Ranking: Normierung und Datenlücken

Die Sterne aus dem Auftrag sind in Rohpunkte übersetzt (5★=25, 4★=18, 3★=10).
Der Endscore ist der erreichte Anteil an den **prüfbaren** Punkten – Kriterien,
für die keine Daten vorliegen, fallen aus Zähler und Nenner.

Das allein hätte einen unangenehmen Nebeneffekt: ein Inserat, bei dem nur ein
einziges Kriterium prüfbar ist und zufällig zutrifft, käme auf 100 Punkte.
Deshalb wird zusätzlich nach **Datenabdeckung** gedämpft (Faktor 0,55 bis
1,0). Ein datenarmes Inserat bleibt sichtbar, rankt aber weit unten. Der
Dämpfungsfaktor steht als Abzug in der Begründung, ist also nachvollziehbar.

---

## 7a. Manueller Betrieb statt Zeitplan

Nachträgliche Anforderung: der Agent soll nicht selbstständig laufen, weil ein
anderer GitHub-Agent bereits den Großteil des Token-Budgets verbraucht.

**Entscheidung:** Der `schedule`-Block im Workflow ist entfernt (auskommentiert
und dokumentiert, falls du ihn zurückwillst), der Docker-Standardbefehl ist das
Dashboard statt des Schedulers, und die KI ist von einem Standardverhalten zu
einer Opt-in-Option geworden.

Wichtig für die Einordnung, weil hier zwei Kostenarten leicht verwechselt
werden: **Das Scrapen verbraucht überhaupt keine LLM-Tokens.** Es kostet
GitHub-Actions-Minuten, die auf öffentlichen Repositories unbegrenzt frei sind.
Token verbraucht ausschließlich die KI-Bewertung, und die lief vorher bei
jedem stündlichen Lauf auf bis zu 15 Inseraten – das waren im schlimmsten Fall
360 Modellaufrufe pro Tag.

Wenn du den Tokenverbrauch im Blick behalten willst, aber trotzdem nichts
verpassen möchtest, ist die sparsamste Variante deshalb nicht „alles manuell",
sondern:

- den `schedule`-Block wieder einkommentieren (etwa dreimal täglich), und
- die KI aus lassen (`ki.aktiv: false`, keine `--mit-ki`-Flag).

Das kostet null Tokens, meldet dir neue Treffer aber weiterhin von selbst per
Telegram. Die KI schaltest du dann gezielt zu, wenn ein interessantes Inserat
auftaucht und du eine Einschätzung dazu willst. Beides ist ein Einzeiler; ich
habe die Voreinstellung so gelassen, wie du sie angefordert hast.

Sichtbar gemacht wird der Verbrauch an drei Stellen: als Kennzahl im
Dashboard, in der Actions-Zusammenfassung nach jedem Lauf, und kumuliert im
Reiter Suchhistorie.

---

## 7. Was die KI darf und was nicht

Zahlen, Distanz und Termine gehören in deterministischen Code – dort ist ein
LLM schlechter, teurer und nicht reproduzierbar. Das Modell übernimmt nur,
was Regeln schlecht können: WG-Tauglichkeit eines Grundrisses, Seriosität
eines Inserats, versteckte Nachteile im Fließtext.

**Entscheidung:** Der KI-Einfluss auf den Score ist auf ±10 Punkte gedeckelt
(`ranking.llm_einfluss_max`). Eine Halluzination kann die Reihenfolge
verschieben, aber kein Inserat nach oben tragen, das die harten Kriterien
verfehlt.

Die KI läuft außerdem erst **nach** den Ausschlussregeln, nur auf den besten
`ki.max_inserate_pro_lauf` Kandidaten (Standard 8), und überspringt alles, was
bereits eine Bewertung in der Datenbank hat. Ein Lauf mit KI landet damit
typischerweise bei rund 12.000 Tokens statt bei einem Vielfachen davon.

Ohne API-Schlüssel und ohne `--mit-ki` läuft alles, nur ohne
Zusammenfassungen.

---

## 8. Pydantic im Kern? Nein.

Der Auftrag nennt Pydantic **und** Clean Architecture. Beides gleichzeitig
ginge nur halb: nach Clean Architecture darf die innerste Schicht keine
Framework-Abhängigkeit haben.

**Entscheidung:** Die Domänenmodelle (`models/domain.py`) und das Suchprofil
(`config/profil.py`) sind stdlib-Dataclasses mit eigener Validierung.
Pydantic sitzt an den Rändern, wo fremde Daten hereinkommen:
`config/settings.py` für Umgebungsvariablen und `api/app.py` für die
HTTP-Schnittstelle. SQLAlchemy taucht ausschließlich in `models/db.py` und
`database/repository.py` auf.

Praktischer Nebeneffekt: der gesamte Kern – Parsing, Geo, Ranking, Dedupe,
Pipeline – ist ohne Datenbank, ohne Netz und ohne installierte Frameworks
testbar. Genau das machen die 121 Tests.

---

## 9. Zwei Dashboards statt einem

Der Auftrag lässt React/Next.js oder Streamlit zu und verlangt gleichzeitig
GitHub Actions als Betriebsart. Auf GitHub Pages läuft aber kein Python.

**Entscheidung:** beides, mit klarer Rollenteilung.

- **Streamlit** (`dashboard/app.py`) ist das vollwertige Dashboard: Karte,
  Preisentwicklung, Favoriten, Suchhistorie, Aussortiert-Diagnose. Liest
  direkt aus SQLite, schreibt Markierungen zurück. Läuft lokal oder im
  Docker-Compose.
- **Statische Seite** (`docs/index.html`) ist die Ansicht für unterwegs:
  liest den JSON-Export, filtert clientseitig, funktioniert auf GitHub Pages
  ohne Server. Keine Favoriten, kein Schreibzugriff.

---

## 10. Was ich nicht ausführen konnte

Ehrlichkeitshalber: die Tests für Parsing, Geo, Ranking, Dedupe und Pipeline
(126 Stück) sind gelaufen und grün. Zwei Dinge sind **ungetestet gegen die
Wirklichkeit**:

1. **Die CSS-Selektoren der Portale.** Sie entsprechen dem aktuellen Markup,
   aber Portale ändern das ohne Vorwarnung. Wenn eine Quelle "0 Treffer, kein
   Fehler" meldet, liegt es fast immer daran – siehe README, Abschnitt
   "Wenn eine Quelle nichts liefert".
2. **Die Datenbankschicht** (SQLAlchemy) und die Benachrichtigungskanäle.
   Sie sind vollständig implementiert, ließen sich hier aber ohne
   Netzwerkzugang nicht ausführen. Der erste `make scan` ist damit der echte
   Integrationstest.

Führe deshalb als Erstes `make pruefe` und dann `make probelauf` aus, bevor
du den Scheduler anwirfst.
