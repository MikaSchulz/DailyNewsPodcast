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
    """Fetch headlines and pick `num_topics` total, weighted across categories.

    `config["categories"]` is a {category: weight} map (higher weight = more
    topics from that category, weight <= 0 excludes it). Slots are apportioned
    proportionally to weight; any slot a category can't fill (feed too short)
    is redistributed to categories that still have unpicked entries.
    """
    weights = {c: w for c, w in config["categories"].items() if w > 0}
    feeds_by_category = config["feeds"]
    num_topics = config["num_topics"]

    if not weights:
        raise RuntimeError("No category in config.yaml has a positive weight.")

    per_category: dict[str, list[Topic]] = {}
    for category in weights:
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

    quotas = _weighted_quotas(weights, num_topics)

    picked: list[Topic] = []
    seen_titles: set[str] = set()
    leftover_slots = 0
    for category, quota in quotas.items():
        entries = per_category.get(category, [])
        filled = 0
        while entries and filled < quota:
            candidate = entries.pop(0)
            if candidate.title in seen_titles:
                continue
            seen_titles.add(candidate.title)
            picked.append(candidate)
            filled += 1
        leftover_slots += quota - filled

    fallback_order = sorted(weights, key=lambda c: weights[c], reverse=True)
    i = 0
    while leftover_slots > 0 and any(per_category.get(c) for c in fallback_order):
        category = fallback_order[i % len(fallback_order)]
        entries = per_category.get(category, [])
        if entries:
            candidate = entries.pop(0)
            if candidate.title not in seen_titles:
                seen_titles.add(candidate.title)
                picked.append(candidate)
                leftover_slots -= 1
        i += 1

    return picked[:num_topics]


def _weighted_quotas(weights: dict[str, float], total: int) -> dict[str, int]:
    """Apportions `total` slots across categories proportional to weight (largest-remainder method)."""
    total_weight = sum(weights.values())
    raw = {c: total * w / total_weight for c, w in weights.items()}
    quotas = {c: int(v) for c, v in raw.items()}
    remainder = total - sum(quotas.values())
    by_fraction = sorted(weights, key=lambda c: raw[c] - quotas[c], reverse=True)
    for c in by_fraction[:remainder]:
        quotas[c] += 1
    return quotas


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
