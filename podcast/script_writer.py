"""Turns picked topics into a single spoken-word podcast script via Claude."""

import logging

from anthropic import Anthropic

from podcast.news_fetcher import Topic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Du bist Autor eines taeglichen deutschsprachigen News-Podcasts. \
Du bekommst eine Liste aktueller Nachrichtenthemen mit Titel, Kurzbeschreibung und Kategorie. \
Schreibe daraus einen zusammenhaengenden, natuerlich klingenden Sprechtext fuer eine \
Text-to-Speech-Stimme.

Regeln:
- Kurzes Intro (Begruessung, Datum falls sinnvoll erwaehnt, Ueberblick was kommt).
- Pro Thema ein Absatz, flüssig moderiert, mit Uebergaengen zwischen den Themen \
  (keine rohen Stichpunkte, keine Ueberschriften, keine Aufzaehlungszeichen).
- Kurzes Outro zum Abschluss.
- Reiner Fliesstext zum Vorlesen. Keine Markdown-Formatierung, keine Emojis, \
  keine Regieanweisungen in Klammern.
- Ziel-Zeichenzahl: {target_chars} Zeichen (+/- 10%)."""


def write_script(topics: list[Topic], config: dict) -> str:
    if not topics:
        raise RuntimeError("No topics to write a script from.")

    client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    model = config["anthropic"]["model"]
    target_chars = config["target_chars"]

    topics_block = "\n\n".join(
        f"[{t.category}] {t.title}\n{t.summary}" for t in topics
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
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
