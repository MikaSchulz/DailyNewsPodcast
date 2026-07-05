#!/usr/bin/env python3
"""Daily news podcast generator.

Fetches current headlines, writes a spoken-word script, synthesizes it to
MP3, and uploads it to a Google Drive folder. Run manually first (see
README.md) before wiring it up to cron / Task Scheduler.
"""

import argparse
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily news podcast generator")
    parser.add_argument("--config", default="config.yaml", help="Path to config file (default: config.yaml)")
    parser.add_argument("--minutes", type=float, help="Override podcast_length_minutes for this run")
    parser.add_argument("--num-topics", type=int, help="Override num_topics for this run")
    parser.add_argument(
        "--categories",
        help='Override category weights for this run, e.g. "politik:2,tech:1,wirtschaft:1"',
    )
    return parser.parse_args()


def apply_overrides(config: dict, args: argparse.Namespace) -> dict:
    if args.minutes is not None:
        config["podcast_length_minutes"] = args.minutes
    if args.num_topics is not None:
        config["num_topics"] = args.num_topics
    if args.categories:
        weights = {}
        for part in args.categories.split(","):
            name, _, weight = part.partition(":")
            weights[name.strip()] = float(weight) if weight else 1.0
        config["categories"] = weights
    config["target_chars"] = round(config["podcast_length_minutes"] * config.get("chars_per_minute", 570))
    return config


def main() -> int:
    load_dotenv()
    args = parse_args()
    config = apply_overrides(load_config(args.config), args)

    try:
        logger.info(
            "Step 1/4: Fetching topics (%d topics, %.0f min target, categories=%s)...",
            config["num_topics"],
            config["podcast_length_minutes"],
            config["categories"],
        )
        topics = news_fetcher.fetch_topics(config)
        logger.info("Picked %d topics: %s", len(topics), [t.title for t in topics])
    except Exception as exc:
        logger.error("News fetch failed: %s", exc)
        return 1

    try:
        logger.info("Step 2/4: Writing script (target ~%d chars)...", config["target_chars"])
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
