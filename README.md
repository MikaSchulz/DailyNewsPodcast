# Daily News Podcast Generator

Erstellt automatisch einen News-Podcast: Themen aus RSS-Feeds holen, zu einem Sprechtext verarbeiten, mit Google Cloud Text-to-Speech vertonen und als MP3 in einen Google Drive Ordner hochladen (synct dann automatisch auf dein Handy über die Drive App).

## Zwei Betriebsarten

- **Voll-automatisch** (`python3 main.py`, keine Flags): braucht einen **Anthropic API Key** (separates, bezahltes Konto auf console.anthropic.com — **nicht** dasselbe wie ein Claude Pro/Max-Abo, die beiden Systeme sind komplett getrennt). Praktisch für reines OS-Cron ganz ohne Claude-Agent.
- **Agent-gesteuert, ohne API-Key** (`--fetch-topics-only` dann `--script-file`): läuft über einen Claude-Agenten (die lokale oder Cloud-Routine, siehe Abschnitt 7) — der Agent schreibt den Sprechtext selbst (läuft ja sowieso schon unter deinem Claude-Abo), Python macht nur noch RSS-Fetch, TTS und Drive-Upload. Kein zweites, bezahltes API-Konto nötig. **Das ist der Standardweg für beide Routinen unten.**

## Projektstruktur

```
DailyPodcast/
├── main.py                    # Orchestriert alles, 3 Modi (siehe oben)
├── config.yaml                # Themen, Länge, Feeds, Drive-Ordner, TTS-Stimme
├── .env                        # API-Keys / Credential-Pfade (nicht einchecken)
├── podcast/
│   ├── news_fetcher.py        # RSS-Feeds abgreifen, Themen auswählen
│   ├── script_writer.py       # Claude API -> Sprechtext (nur Voll-auto-Modus)
│   ├── tts.py                  # Google Cloud TTS (Service Account) -> MP3
│   └── drive_uploader.py      # OAuth (dein Account) -> Upload zu Drive
├── credentials/                # Service-Account-JSON, OAuth-Client, Token
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

### 2a. Service Account für Text-to-Speech

TTS ist eine stateless API ohne Speicher/Kontingent-Bezug — dafür reicht ein Service Account, komplett headless, kein Browser-Login nötig.

1. **APIs & Dienste → Anmeldedaten → Anmeldedaten erstellen → Dienstkonto**
2. Name vergeben (z. B. `podcast-bot`), keine besondere Rolle nötig
3. Nach Erstellung: Dienstkonto öffnen → **Schlüssel → Neuer Schlüssel → JSON** → Datei herunterladen
4. Datei ablegen unter: `credentials/service-account.json`

**Wichtig:** dieser Service Account wird NUR für TTS benutzt, nicht für Drive — Google blockt Service Accounts explizit davon, Dateien in einem normalen (Nicht-Workspace-)Drive zu besitzen, auch wenn ein Ordner mit ihnen geteilt wird (`storageQuotaExceeded`, live getestet). Deshalb Schritt 2b.

### 2b. OAuth-Client für Google Drive (dein eigener Account)

1. **APIs & Dienste → OAuth-Zustimmungsbildschirm** einrichten (Nutzertyp "Extern" reicht)
2. **Publishing status auf "In production" stellen** (nicht "Testing" lassen!) — sonst läuft der Refresh-Token nach 7 Tagen ab und die Automatisierung bricht wöchentlich. Für den rein privaten Gebrauch ist das ohne Google-Review möglich; du bekommst dabei nur die "unverified app"-Warnung beim Login zu sehen, die du selbst bestätigst.
3. **Anmeldedaten → Anmeldedaten erstellen → OAuth-Client-ID**, Anwendungstyp **Desktop-App**
4. JSON herunterladen, ablegen unter: `credentials/oauth_client_secret.json`

Der eigentliche Login passiert automatisch beim ersten Testlauf (Abschnitt 6) — Browserfenster öffnet sich, einmal einloggen/bestätigen, danach läuft alles über einen automatisch erneuerten Token (`credentials/token.json`), auch headless in der Cloud-Routine (siehe Abschnitt 7).

## 3. Anthropic API Key — nur für Voll-auto-Modus

Überspringen, wenn du die Routinen aus Abschnitt 7 nutzt (Standardweg, kein Key nötig). Nur relevant für `python3 main.py` ganz ohne Agenten:

1. Key erstellen unter [console.anthropic.com](https://console.anthropic.com) (separates, bezahltes Konto — läuft nicht über dein Claude Pro/Max-Abo)
2. In `.env` eintragen (siehe unten)

## 4. `.env` Datei anlegen

```bash
cp .env.example .env
```

Dann `.env` ausfüllen (Anthropic-Zeile nur für Voll-auto-Modus, siehe oben):

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_APPLICATION_CREDENTIALS=credentials/service-account.json
GOOGLE_OAUTH_CLIENT_SECRET=credentials/oauth_client_secret.json
GOOGLE_OAUTH_TOKEN=credentials/token.json
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

**Wichtig: vor der Automatisierung immer erst manuell testen.** Dieser Schritt macht auch den einmaligen OAuth-Browser-Login für Drive (Abschnitt 2b).

```bash
source .venv/bin/activate
echo "Kurzer Test des Podcast-Systems. Wenn du das hörst, funktioniert die Pipeline." > /tmp/test_script.txt
python3 main.py --script-file /tmp/test_script.txt
```

Ablauf:
1. Google TTS erzeugt `output/podcast_<datum>.mp3` — **diese Datei anhören, bevor der Upload läuft**
2. Browserfenster öffnet sich für Drive (nur dieses eine Mal) → einloggen, "unverified app"-Warnung bestätigen, Zugriff erlauben
3. Datei wird in den konfigurierten Drive-Ordner hochgeladen

Wenn irgendwo ein Fehler auftritt, bricht das Script sauber mit einer Fehlermeldung ab (kein Absturz, kein Traceback-Wirrwarr) und sagt, welcher Schritt betroffen war. Häufige Ursachen:
- `GOOGLE_APPLICATION_CREDENTIALS` zeigt auf falschen/fehlenden Pfad → TTS-Client kann nicht erstellt werden
- `drive.folder_id` noch auf Platzhalter → Upload bricht sofort ab
- `credentials/oauth_client_secret.json` fehlt → OAuth-Login kann nicht starten
- Kein Internet / Feed-URL down → einzelner Feed wird übersprungen, Rest läuft weiter

Nach erfolgreichem Test: Handy öffnen, Google Drive App → Datei sollte im Ordner erscheinen (ggf. kurz auf Sync warten).

Danach den vollen RSS→Text→TTS→Upload-Ablauf einmal testen (braucht `ANTHROPIC_API_KEY`, siehe Abschnitt 3 — überspringbar, wenn du direkt mit den Routinen aus Abschnitt 7 arbeitest):

```bash
python3 main.py
```

## 7. Automatisierung

Drei Optionen, je nachdem ob der Rechner beim Trigger-Zeitpunkt laufen soll oder nicht.

Beide Routinen unten nutzen den **agent-gesteuerten Modus** (kein `ANTHROPIC_API_KEY` nötig): der Trigger-Prompt lässt `main.py --fetch-topics-only` laufen, der Agent schreibt selbst den deutschen Sprechtext aus dem JSON (Intro, ein Absatz pro Thema, Outro, Ziel-Zeichenzahl aus `config.yaml` beachten), speichert ihn in eine Datei, dann läuft `main.py --script-file <datei>` für TTS + Upload.

### Option A: Claude Code Routine (lokal — braucht offene App, aber nicht zwingend hochgefahrenen PC über Nacht)

Läuft über eine **Claude Code Scheduled Task** namens `daily-news-podcast` (Cron `30 6 * * *`, lokale Zeitzone), liegt unter `~/.claude/scheduled-tasks/daily-news-podcast/SKILL.md`.

Wichtig:
- Läuft nur, während die Claude-App offen ist. Ist sie beim Trigger-Zeitpunkt geschlossen, läuft die Aufgabe beim nächsten Start nach — **läuft also nicht, wenn der PC aus ist.**
- Vor dem ersten scharfen Lauf: einmal manuell **"Run now"** in der Sidebar unter "Scheduled" ausführen, damit Tool-Freigaben (z. B. Bash) einmalig bestätigt werden.
- Zeitplan/Prompt ändern: in der Sidebar unter "Scheduled" oder per `update_scheduled_task`.

### Option B: Cloud Routine (claude.ai — läuft unabhängig vom PC)

Das ist die Option, die tatsächlich läuft, wenn der Rechner aus ist — sie läuft komplett auf claude.ai-Infrastruktur, nicht auf deinem Mac. Jeder Lauf startet in einer frischen, leeren Cloud-Sandbox (kein gespeicherter Zustand zwischen Läufen), deshalb übernimmt `main.py` selbst das Bootstrapping: `ensure_service_account_file()` und `ensure_oauth_token_file()` schreiben die Keys beim Start aus Umgebungsvariablen, falls die Dateien noch nicht existieren — lokal (Dateien liegen schon da) passiert dabei nichts.

Repo ist bereits gepusht ([github.com/MikaSchulz/DailyNewsPodcast](https://github.com/MikaSchulz/DailyNewsPodcast)), Credentials sind per `.gitignore` ausgeschlossen. Noch zu tun:

1. In claude.ai unter **Environments** eine neue Environment anlegen, die an dieses Repo gekoppelt ist.
2. In dieser Environment zwei **Secrets** setzen (kein `ANTHROPIC_API_KEY` nötig, siehe oben):
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — kompletter Inhalt von `credentials/service-account.json`
   - `GOOGLE_OAUTH_TOKEN_JSON` — kompletter Inhalt von `credentials/token.json` (existiert erst nach dem ersten lokalen Testlauf aus Abschnitt 6 — der einmalige Browser-Login lässt sich nicht headless in der Cloud nachholen)
3. Mir die `environment_id` nennen — ich lege dann per `RemoteTrigger` (Cron `30 6 * * *`) einen Trigger an.

Der einzige Nachteil ggü. lokal: jeder Lauf installiert Dependencies neu (ein paar Sekunden Mehraufwand), sonst identisches Verhalten.

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

- Google Cloud TTS: 1 Mio. Zeichen/Monat kostenlos (WaveNet/Standard-Stimmen; Chirp3-HD-Stimmen haben ein eigenes kostenloses Kontingent, siehe [Preisseite](https://cloud.google.com/text-to-speech/pricing)). Bei 30 Min/Tag (~17.000 Zeichen) = ~510.000 Zeichen/Monat → noch im kostenlosen Rahmen, aber nicht mehr weit davon entfernt.
- Anthropic API: **nur im Voll-auto-Modus relevant** (Abschnitt 3) — im agent-gesteuerten Modus (Standardweg, beide Routinen) entfällt das komplett, Skript-Schreiben läuft über dein bestehendes Claude-Abo.
- Google Drive: kein zusätzlicher Kostenpunkt, nutzt dein bestehendes Speicherkontingent.

## 9. Länge, Themen und Gewichtung ändern

Siehe Abschnitt 5 — steuerbar dauerhaft über `config.yaml` (`podcast_length_minutes`, `num_topics`, `categories`-Gewichte) oder einmalig per CLI-Flag (`--minutes`, `--num-topics`, `--categories`), ohne die Datei anzufassen.
