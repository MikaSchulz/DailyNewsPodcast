#!/usr/bin/env python3
"""Daily news podcast generator.

Three ways to run this:

1. Full auto (needs ANTHROPIC_API_KEY):
   python3 main.py
   Fetches topics, writes the script via the Anthropic API, synthesizes
   MP3, uploads to Drive. Good for plain OS cron with no agent involved.

2. Agent-orchestrated, no Anthropic API key needed (used by the Claude
   Code / cloud Routine — the agent running the routine already IS Claude,
   so it writes the script itself instead of paying for a second API call):
   python3 main.py --fetch-topics-only            # prints topics as JSON
   # agent writes the German script from that JSON, saves it to a file
   python3 main.py --script-file output/script.txt  # TTS + upload only

See README.md before wiring either mode up to cron / a Routine.
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
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


def ensure_service_account_file() -> None:
    """Materializes the service account key from GOOGLE_SERVICE_ACCOUNT_JSON if needed.

    Local runs already have the key file on disk (see README). Cloud runs
    only get the key as a secret env var (no persistent filesystem between
    runs), so write it out once at startup before the Google clients need it.
    """
    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials/service-account.json")
    if not os.path.exists(key_path):
        raw_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if raw_json:
            os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)
            with open(key_path, "w", encoding="utf-8") as f:
                f.write(raw_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path


def ensure_oauth_token_file() -> None:
    """Materializes the Drive OAuth token from GOOGLE_OAUTH_TOKEN_JSON if needed.

    Mirrors ensure_service_account_file(): local runs already did the
    one-time interactive browser login and have the token file on disk.
    Cloud runs get that token's content as a secret env var instead (no
    persistent filesystem), so write it out once at startup — it's then
    refreshed non-interactively by drive_uploader, no browser needed.
    """
    token_path = os.environ.get("GOOGLE_OAUTH_TOKEN", "credentials/token.json")
    if not os.path.exists(token_path):
        raw_json = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
        if raw_json:
            os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(raw_json)
    os.environ["GOOGLE_OAUTH_TOKEN"] = token_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily news podcast generator")
    parser.add_argument("--config", default="config.yaml", help="Path to config file (default: config.yaml)")
    parser.add_argument("--minutes", type=float, help="Override podcast_length_minutes for this run")
    parser.add_argument("--num-topics", type=int, help="Override num_topics for this run")
    parser.add_argument(
        "--categories",
        help='Override category weights for this run, e.g. "politik:2,tech:1,wirtschaft:1"',
    )
    parser.add_argument(
        "--fetch-topics-only",
        action="store_true",
        help="Only fetch and print topics as JSON, then exit (no script, no TTS, no upload).",
    )
    parser.add_argument(
        "--script-file",
        help="Skip fetching/writing and synthesize+upload this pre-written script file instead ('-' for stdin).",
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


def run_fetch_topics_only(config: dict) -> int:
    try:
        topics = news_fetcher.fetch_topics(config)
    except Exception as exc:
        logger.error("News fetch failed: %s", exc)
        return 1
    print(json.dumps([asdict(t) for t in topics], ensure_ascii=False, indent=2))
    return 0


def run_from_script_file(script_path: str, config: dict) -> int:
    try:
        script = sys.stdin.read() if script_path == "-" else open(script_path, encoding="utf-8").read()
        script = script.strip()
        if not script:
            raise RuntimeError("Script is empty.")
    except Exception as exc:
        logger.error("Could not read script: %s", exc)
        return 1
    return synthesize_and_upload(script, config)


def synthesize_and_upload(script: str, config: dict) -> int:
    try:
        logger.info("Synthesizing audio...")
        os.makedirs(config["output_dir"], exist_ok=True)
        output_path = os.path.join(config["output_dir"], f"podcast_{date.today().isoformat()}.mp3")
        tts.synthesize(script, config, output_path)
    except Exception as exc:
        logger.error("Text-to-speech failed: %s", exc)
        return 1

    try:
        logger.info("Uploading to Google Drive...")
        drive_uploader.upload_file(output_path, config)
    except Exception as exc:
        logger.error("Drive upload failed: %s", exc)
        logger.error("Audio file was still generated locally at: %s", output_path)
        return 1

    logger.info("Done. Podcast published: %s", output_path)
    return 0


def run_full_auto(config: dict) -> int:
    try:
        logger.info(
            "Fetching topics (%d topics, %.0f min target, categories=%s)...",
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
        logger.info("Writing script (target ~%d chars)...", config["target_chars"])
        script = script_writer.write_script(topics, config)
    except Exception as exc:
        logger.error("Script writing failed: %s", exc)
        return 1

    return synthesize_and_upload(script, config)


def main() -> int:
    load_dotenv()
    ensure_service_account_file()
    ensure_oauth_token_file()
    args = parse_args()
    config = apply_overrides(load_config(args.config), args)

    if args.fetch_topics_only:
        return run_fetch_topics_only(config)
    if args.script_file:
        return run_from_script_file(args.script_file, config)
    return run_full_auto(config)


if __name__ == "__main__":
    sys.exit(main())
