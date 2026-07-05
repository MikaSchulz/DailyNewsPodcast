# Daily News Podcast Generator

Erstellt jeden Morgen automatisch einen ~10-minütigen News-Podcast: Themen aus RSS-Feeds holen, per Claude zu einem Sprechtext verarbeiten, mit Google Cloud Text-to-Speech vertonen und als MP3 in einen Google Drive Ordner hochladen (synct dann automatisch auf dein Handy über die Drive App).

## Projektstruktur

```
DailyPodcast/
├── main.py                    # Orchestriert den ganzen Ablauf
├── config.yaml                # Themen, Länge, Feeds, Drive-Ordner, TTS-Stimme
├── .env                        # API-Keys / Credential-Pfade (nicht einchecken)
├── podcast/
│   ├── news_fetcher.py        # RSS-Feeds abgreifen, Themen auswählen
│   ├── script_writer.py       # Claude API -> Sprechtext
│   ├── tts.py                  # Google Cloud TTS -> MP3
│   └── drive_uploader.py      # Service Account -> Upload zu Drive
├── credentials/                # Service-Account-JSON
└── output/                     # Generierte MP3s
```

## 1. Setup: Python-Umgebung

```bash
cd /Users/mikaschulz/Documents/Code/DailyPodcast
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Google Cloud Projekt einrichten

1. Auf [console.cloud.google.com](https://console.cloud.google.com) ein neues Projekt anlegen (z. B. `daily-podcast`).
2. Im Menü **APIs & Dienste → Bibliothek**:
   - **Cloud Text-to-Speech API** aktivieren
   - **Google Drive API** aktivieren

### 2a. Ein Service Account für TTS + Drive

Ein einzelner Service Account reicht für beides. Bewusst **kein OAuth mit deinem persönlichen Account** — ein Service Account läuft komplett headless (kein Browser-Login, kein Token, der nach 7 Tagen abläuft), was sowohl für lokales Cron als auch für die Cloud-Routine (siehe unten) Voraussetzung ist.

1. **APIs & Dienste → Anmeldedaten → Anmeldedaten erstellen → Dienstkonto**
2. Name vergeben (z. B. `podcast-bot`), keine besondere Rolle nötig
3. Nach Erstellung: Dienstkonto öffnen → **Schlüssel → Neuer Schlüssel → JSON** → Datei herunterladen
4. Datei ablegen unter: `credentials/service-account.json`
5. JSON öffnen, Feld `client_email` kopieren (sieht aus wie `podcast-bot@daily-podcast-xxxxx.iam.gserviceaccount.com`)
6. Ziel-Ordner in Google Drive öffnen → **Freigeben** → diese `client_email`-Adresse eintragen, Rolle **Bearbeiter**

Ohne Schritt 6 schlägt der Upload fehl (Service Account hat sonst keinen Zugriff auf den Ordner). Die hochgeladene Datei zählt gegen dein eigenes Speicherkontingent und taucht in deiner Drive-App auf — nur der "Besitzer" steht dann auf den Service Account statt auf dich.

## 3. Anthropic API Key (für den Sprechtext)

1. Key erstellen unter [console.anthropic.com](https://console.anthropic.com)
2. In `.env` eintragen (siehe unten)

## 4. `.env` Datei anlegen

```bash
cp .env.example .env
```

Dann `.env` ausfüllen:

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_APPLICATION_CREDENTIALS=credentials/service-account.json
```

## 5. `config.yaml` anpassen

- `drive.folder_id`: Ordner in Google Drive anlegen, ID aus der URL kopieren (`.../folders/<ID_HIER>`)
- `feeds`: RSS-Feeds pro Kategorie nach Bedarf anpassen oder ergänzen
- `categories`: Kategorie → Gewicht. Höheres Gewicht = proportional mehr Themen aus dieser Kategorie. Gewicht `0`/weglassen = Kategorie ausgeschlossen. Beispiel: `politik: 2, tech: 1` → Politik bekommt doppelt so viele Slots wie Tech.
- `num_topics`: Gesamtzahl Themen, wird gemäß der Gewichte auf die Kategorien verteilt
- `podcast_length_minutes` / `chars_per_minute`: steuern zusammen die Ziel-Zeichenzahl fürs Skript (`minutes * chars_per_minute`) — für 30 Minuten einfach `podcast_length_minutes: 30` setzen, kein separates Zeichen-Feld mehr nötig
- `tts.voice_name`: andere Stimme wählen, Liste unter [cloud.google.com/text-to-speech/docs/voices](https://cloud.google.com/text-to-speech/docs/voices)

### Pro Lauf überschreiben (ohne `config.yaml` zu ändern)

```bash
python3 main.py --minutes 20 --num-topics 8 --categories "politik:2,tech:2,wirtschaft:1"
```

Alle drei Flags sind optional und überschreiben nur für diesen einen Lauf — nützlich für spontane Läufe oder unterschiedliche Werte an Wochentagen/Wochenende (z. B. in der Routine bzw. im Cron-Aufruf verschiedene Argumente je nach Wochentag übergeben).

## 6. Erster manueller Test

**Wichtig: vor der Automatisierung immer erst manuell testen.**

```bash
source .venv/bin/activate
python3 main.py
```

Ablauf beim ersten Start:
1. Themen werden von den RSS-Feeds geholt (Konsolen-Log zeigt die ausgewählten Titel)
2. Claude schreibt den Sprechtext
3. Google TTS erzeugt `output/podcast_<datum>.mp3` — **diese Datei anhören, bevor der Upload läuft**
4. Datei wird direkt (ohne Login-Schritt) in den konfigurierten Drive-Ordner hochgeladen

Wenn irgendwo ein Fehler auftritt, bricht das Script sauber mit einer Fehlermeldung ab (kein Absturz, kein Traceback-Wirrwarr) und sagt, welcher Schritt betroffen war. Häufige Ursachen:
- `GOOGLE_APPLICATION_CREDENTIALS` zeigt auf falschen/fehlenden Pfad → TTS-Client kann nicht erstellt werden
- `drive.folder_id` noch auf Platzhalter → Upload bricht sofort ab
- Ordner nicht mit `client_email` des Service Accounts geteilt → Upload schlägt mit Berechtigungsfehler fehl
- Kein Internet / Feed-URL down → einzelner Feed wird übersprungen, Rest läuft weiter

Nach erfolgreichem Test: Handy öffnen, Google Drive App → Datei sollte im Ordner erscheinen (ggf. kurz auf Sync warten).

## 7. Automatisierung

Drei Optionen, je nachdem ob der Rechner beim Trigger-Zeitpunkt laufen soll oder nicht.

### Option A: Claude Code Routine (lokal — braucht offene App, aber nicht zwingend hochgefahrenen PC über Nacht)

Läuft über eine **Claude Code Scheduled Task** namens `daily-news-podcast` (Cron `30 6 * * *`, lokale Zeitzone), liegt unter `~/.claude/scheduled-tasks/daily-news-podcast/SKILL.md`, ruft bei jedem Lauf `.venv/bin/python3 main.py` in diesem Ordner auf.

Wichtig:
- Läuft nur, während die Claude-App offen ist. Ist sie beim Trigger-Zeitpunkt geschlossen, läuft die Aufgabe beim nächsten Start nach — **läuft also nicht, wenn der PC aus ist.**
- Vor dem ersten scharfen Lauf: einmal manuell **"Run now"** in der Sidebar unter "Scheduled" ausführen, damit Tool-Freigaben (z. B. Bash) einmalig bestätigt werden.
- Zeitplan/Prompt ändern: in der Sidebar unter "Scheduled" oder per `update_scheduled_task`.

### Option B: Cloud Routine (claude.ai — läuft unabhängig vom PC)

Das ist die Option, die tatsächlich läuft, wenn der Rechner aus ist — sie läuft komplett auf claude.ai-Infrastruktur, nicht auf deinem Mac. Jeder Lauf startet in einer frischen, leeren Cloud-Sandbox (kein gespeicherter Zustand zwischen Läufen), deshalb übernimmt `main.py` selbst das Bootstrapping: `ensure_service_account_file()` schreibt den Key beim Start aus der Umgebungsvariable `GOOGLE_SERVICE_ACCOUNT_JSON` nach `credentials/service-account.json`, falls die Datei noch nicht existiert — lokal (Datei liegt schon da) passiert dabei nichts.

Repo ist bereits gepusht ([github.com/MikaSchulz/DailyNewsPodcast](https://github.com/MikaSchulz/DailyNewsPodcast)), Credentials sind per `.gitignore` ausgeschlossen. Noch zu tun:

1. In claude.ai unter **Environments** eine neue Environment anlegen, die an dieses Repo gekoppelt ist.
2. In dieser Environment zwei **Secrets** setzen:
   - `ANTHROPIC_API_KEY` — dein Anthropic Key
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — kompletter Inhalt von `credentials/service-account.json` als ein String
3. Mir die `environment_id` nennen — ich lege dann per `RemoteTrigger` (Cron `30 6 * * *`) einen Trigger an, dessen Prompt bei jedem Lauf: Repo pullt, `pip install -r requirements.txt` ausführt, `python3 main.py` ausführt (Credential-Bootstrap passiert automatisch im Code) und das Ergebnis kurz zurückmeldet.

Der einzige Nachteil ggü. lokal: jeder Lauf installiert Dependencies neu (ein paar Sekunden Mehraufwand), sonst identisches Verhalten — gleicher `main.py`-Code läuft lokal wie in der Cloud.

### Option C: OS-Cron (klassisch, ohne Claude-App)

#### macOS / Linux: cron

```bash
crontab -e
```

Zeile hinzufügen (Beispiel: täglich 6:30 Uhr):

```
30 6 * * * cd /Users/mikaschulz/Documents/Code/DailyPodcast && /Users/mikaschulz/Documents/Code/DailyPodcast/.venv/bin/python3 main.py >> logs/podcast.log 2>&1
```

Hinweis: `logs/` Ordner vorher anlegen (`mkdir logs`), sonst schlägt die Log-Umleitung fehl. Absoluten Pfad zum venv-Python verwenden, da cron kein `source activate` kennt.

#### Windows: Task Scheduler

1. **Aufgabenplanung** öffnen → **Einfache Aufgabe erstellen**
2. Trigger: **Täglich**, Uhrzeit z. B. 06:30
3. Aktion: **Programm starten**
   - Programm/Skript: `C:\Pfad\zu\DailyPodcast\.venv\Scripts\python.exe`
   - Argumente: `main.py`
   - Starten in: `C:\Pfad\zu\DailyPodcast`
4. Fertigstellen. Optional unter Eigenschaften → "Unabhängig von Benutzeranmeldung ausführen", falls der Rechner beim Trigger-Zeitpunkt gesperrt sein könnte.

## 8. Kosten

- Google Cloud TTS: 1 Mio. Zeichen/Monat kostenlos (WaveNet/Standard-Stimmen; Chirp3-HD-Stimmen haben ein eigenes kostenloses Kontingent, siehe [Preisseite](https://cloud.google.com/text-to-speech/pricing)). Bei ~5.500 Zeichen/Tag = ~170.000 Zeichen/Monat → deutlich im kostenlosen Rahmen.
- Anthropic API: abhängig vom Modell, Kosten pro Aufruf minimal (ein Request/Tag, wenige Tausend Tokens).
- Google Drive: kein zusätzlicher Kostenpunkt, nutzt dein bestehendes Speicherkontingent.

## 9. Länge, Themen und Gewichtung ändern

Siehe Abschnitt 5 — steuerbar dauerhaft über `config.yaml` (`podcast_length_minutes`, `num_topics`, `categories`-Gewichte) oder einmalig per CLI-Flag (`--minutes`, `--num-topics`, `--categories`), ohne die Datei anzufassen.
