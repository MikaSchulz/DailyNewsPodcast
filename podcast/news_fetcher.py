"""Pulls current headlines from configured RSS feeds and picks a topic set."""

import logging
from dataclasses import dataclass

import feedparser

logger = logging.getLogger(__name__)


@dataclass
class Topic:
    title: str
    summary: str
    link: str
    category: str


def fetch_topics(config: dict) -> list[Topic]:
    """Fetch headlines for each configured category and pick `num_topics` total.

    Picks round-robin across categories so a single category can't crowd out
    the rest, then truncates to config["num_topics"].
    """
    categories = config["categories"]
    feeds_by_category = config["feeds"]
    num_topics = config["num_topics"]

    per_category: dict[str, list[Topic]] = {}
    for category in categories:
        urls = feeds_by_category.get(category, [])
        if not urls:
            logger.warning("No feed URLs configured for category '%s', skipping.", category)
            continue
        per_category[category] = _fetch_category(category, urls)

    if not any(per_category.values()):
        raise RuntimeError(
            "No topics could be fetched from any configured feed. "
            "Check your network connection and the feed URLs in config.yaml."
        )

    picked: list[Topic] = []
    seen_titles: set[str] = set()
    exhausted = False
    while len(picked) < num_topics and not exhausted:
        exhausted = True
        for category in categories:
            queue = per_category.get(category, [])
            while queue:
                candidate = queue.pop(0)
                if candidate.title in seen_titles:
                    continue
                seen_titles.add(candidate.title)
                picked.append(candidate)
                exhausted = False
                break
            if len(picked) >= num_topics:
                break

    return picked[:num_topics]


def _fetch_category(category: str, urls: list[str]) -> list[Topic]:
    topics = []
    for url in urls:
        try:
            parsed = feedparser.parse(url)
            if parsed.bozo and not parsed.entries:
                logger.warning("Feed '%s' (%s) failed to parse: %s", category, url, parsed.bozo_exception)
                continue
            for entry in parsed.entries:
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                topics.append(
                    Topic(
                        title=entry.get("title", "").strip(),
                        summary=_strip_html(summary).strip(),
                        link=entry.get("link", ""),
                        category=category,
                    )
                )
        except Exception as exc:
            logger.warning("Could not fetch feed '%s' (%s): %s", category, url, exc)
    return topics


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text)
