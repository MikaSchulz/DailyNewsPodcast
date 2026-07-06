"""Pulls current headlines from configured RSS feeds and picks a topic set."""

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlparse

import feedparser

logger = logging.getLogger(__name__)

# How similar two (normalized) titles need to be to count as the same story
# across sources. Tuned by hand — lower catches more duplicates but risks
# merging unrelated stories with similar wording; higher misses duplicates
# that outlets phrase very differently.
DEDUP_SIMILARITY_THRESHOLD = 0.6


@dataclass
class Topic:
    title: str
    summary: str
    link: str
    category: str
    rank: int = 0  # position within its source feed (0 = that outlet's top story)
    source_domain: str = ""  # e.g. "tagesschau.de" — which outlet this entry came from
    source_count: int = 1  # how many DISTINCT outlets reported this same story


def fetch_topics(config: dict) -> list[Topic]:
    """Fetch headlines and pick `num_topics` total, weighted across categories.

    `config["categories"]` is a {category: weight} map (higher weight = more
    topics from that category, weight <= 0 excludes it). Slots are apportioned
    proportionally to weight; any slot a category can't fill (feed too short)
    is redistributed to categories that still have unpicked entries.

    Within a category, candidates are deduplicated (same story reported by
    multiple feeds is merged into one, with source_count tracking how many
    outlets ran it) and ranked by source_count desc, then by rank asc (each
    outlet's own editorial position — index 0 is that outlet's top story).
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
        candidates = _fetch_category(category, urls)
        per_category[category] = _rank_and_dedup(candidates)

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
        domain = urlparse(url).netloc.removeprefix("www.")
        try:
            parsed = feedparser.parse(url)
            if parsed.bozo and not parsed.entries:
                logger.warning("Feed '%s' (%s) failed to parse: %s", category, url, parsed.bozo_exception)
                continue
            for rank, entry in enumerate(parsed.entries):
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                topics.append(
                    Topic(
                        title=entry.get("title", "").strip(),
                        summary=_strip_html(summary).strip(),
                        link=entry.get("link", ""),
                        category=category,
                        rank=rank,
                        source_domain=domain,
                    )
                )
        except Exception as exc:
            logger.warning("Could not fetch feed '%s' (%s): %s", category, url, exc)
    return topics


def _rank_and_dedup(candidates: list[Topic]) -> list[Topic]:
    """Merges near-duplicate titles (same story, multiple outlets) and sorts by relevance.

    Relevance = reported by more sources first, then each outlet's own
    editorial ranking (lower rank = closer to that outlet's top story).
    """
    merged: list[Topic] = []
    used = [False] * len(candidates)
    normalized = [_normalize_title(c.title) for c in candidates]

    for i, topic in enumerate(candidates):
        if used[i]:
            continue
        used[i] = True
        cluster = [topic]
        for j in range(i + 1, len(candidates)):
            if used[j]:
                continue
            if SequenceMatcher(None, normalized[i], normalized[j]).ratio() >= DEDUP_SIMILARITY_THRESHOLD:
                used[j] = True
                cluster.append(candidates[j])

        primary = min(cluster, key=lambda t: t.rank)
        primary.source_count = len({t.source_domain for t in cluster})
        merged.append(primary)

    merged.sort(key=lambda t: (-t.source_count, t.rank))
    return merged


def _normalize_title(title: str) -> str:
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)
