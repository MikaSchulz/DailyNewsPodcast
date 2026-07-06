"""Turns picked topics into a single spoken-word podcast script via Claude."""

import logging

from anthropic import Anthropic

from podcast.news_fetcher import Topic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Du bist Autor eines taeglichen deutschsprachigen News-Podcasts. \
Du bekommst eine Liste aktueller Nachrichtenthemen mit Titel, Kurzbeschreibung, Kategorie, \
source_count (Anzahl unabhaengiger Quellen, die das Thema gebracht haben) und target_length \
(fuer dieses Thema vorgesehene Zeichenzahl im Sprechtext). target_length ist bereits nach \
Substanz der Quelle gewichtet - lange, ausfuehrliche Artikel haben ein hohes target_length, \
duenne Meldungen ein niedriges - und die Werte summieren sich auf die Ziel-Gesamtlaenge. \
Halte dich pro Thema ungefaehr an sein target_length (Toleranz ok, aber nicht durchgaengig \
kuerzer schreiben). Schreibe daraus einen zusammenhaengenden, natuerlich klingenden \
Sprechtext fuer eine Text-to-Speech-Stimme.

Gliederung:
- Kurzes Intro (Begruessung, Datum falls sinnvoll erwaehnt, Ueberblick was kommt).
- Themen mit niedrigem target_length (grob unter 500 Zeichen) in einem kompakten \
  Kurznachrichten-Block abhandeln - je ein bis zwei Saetze, direkt hintereinander mit \
  natuerlichen Uebergaengen (z. B. "Kurz notiert", "Ausserdem"). Bleibt Fliesstext, \
  keine Aufzaehlung.
- Themen mit hoeherem target_length bekommen einen entsprechend laengeren, gut \
  moderierten Absatz mit Hintergrund und Einordnung - je hoeher target_length, desto \
  ausfuehrlicher. Uebergaenge zwischen den Themen, keine rohen Stichpunkte, keine \
  Ueberschriften, keine Aufzaehlungszeichen.
- Die Reihenfolge darf sich am journalistischen Gewicht orientieren, nicht stur an \
  der Eingabereihenfolge.
- Kurzes Outro zum Abschluss.
- Reiner Fliesstext zum Vorlesen. Keine Markdown-Formatierung, keine Emojis, \
  keine Regieanweisungen in Klammern.
- Ziel-Gesamtlaenge ca. {target_chars} Zeichen (Summe aller target_length-Werte) - \
  daran halten, nicht kuenstlich kuerzen."""


def write_script(topics: list[Topic], config: dict) -> str:
    if not topics:
        raise RuntimeError("No topics to write a script from.")

    client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    model = config["anthropic"]["model"]
    target_chars = config["target_chars"]

    topics_block = "\n\n".join(
        f"[{t.category}] (source_count={t.source_count}, target_length={t.target_length}) {t.title}\n{t.summary}"
        for t in topics
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT.format(target_chars=target_chars),
            messages=[
                {
                    "role": "user",
                    "content": f"Hier sind die heutigen Themen:\n\n{topics_block}",
                }
            ],
        )
    except Exception as exc:
        raise RuntimeError(f"Anthropic API call failed while writing the script: {exc}") from exc

    script = "".join(block.text for block in response.content if block.type == "text").strip()
    if not script:
        raise RuntimeError("Anthropic API returned an empty script.")

    logger.info("Script written: %d characters.", len(script))
    return script
