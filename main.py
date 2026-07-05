#!/usr/bin/env python3
"""Daily news podcast generator.

Fetches current headlines, writes a spoken-word script, synthesizes it to
MP3, and uploads it to a Google Drive folder. Run manually first (see
README.md) before wiring it up to cron / Task Scheduler.
"""

import logging
import os
import sys
from datetime import date

import yaml
from dotenv import load_dotenv

from podcast import drive_uploader, news_fetcher, script_writer, tts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("podcast")


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    load_dotenv()
    config = load_config()

    try:
        logger.info("Step 1/4: Fetching topics...")
        topics = news_fetcher.fetch_topics(config)
        logger.info("Picked %d topics: %s", len(topics), [t.title for t in topics])
    except Exception as exc:
        logger.error("News fetch failed: %s", exc)
        return 1

    try:
        logger.info("Step 2/4: Writing script...")
        script = script_writer.write_script(topics, config)
    except Exception as exc:
        logger.error("Script writing failed: %s", exc)
        return 1

    try:
        logger.info("Step 3/4: Synthesizing audio...")
        os.makedirs(config["output_dir"], exist_ok=True)
        output_path = os.path.join(config["output_dir"], f"podcast_{date.today().isoformat()}.mp3")
        tts.synthesize(script, config, output_path)
    except Exception as exc:
        logger.error("Text-to-speech failed: %s", exc)
        return 1

    try:
        logger.info("Step 4/4: Uploading to Google Drive...")
        drive_uploader.upload_file(output_path, config)
    except Exception as exc:
        logger.error("Drive upload failed: %s", exc)
        logger.error("Audio file was still generated locally at: %s", output_path)
        return 1

    logger.info("Done. Podcast published: %s", output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
