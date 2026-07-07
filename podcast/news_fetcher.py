"""Pulls current headlines from configured RSS feeds and picks a topic set."""

import calendar
import logging
import re
import time
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

# Title prefixes that mark sponsored posts or paywalled teasers rather than
# actual news (e.g. Golem's "Anzeige:" ads, Heise's "heise+ |" paywall tag).
AD_TITLE_PREFIXES = ("Anzeige:", "heise+ |")

# Floor for a summary's "weight" (its length) when estimating how much
# script space a topic deserves — keeps a near-empty summary from getting
# zero representation or blowing up a ratio calculation.
MIN_SUMMARY_WEIGHT = 30

# How many script characters we expect to get per character of RSS summary,
# used only to decide how many topics to pull in before the character
# budget is likely full. Rough and hand-tuned — if podcasts consistently
# come out short, raise it (pulls in fewer, more padded-out topics); if
# they come out long, lower it.
SUMMARY_EXPANSION_RATIO = 6.0

# Only include entries published within this many hours. Deliberately a bit
# under 24h so there's a real gap to whatever the previous day's run already
# covered, instead of the two runs' 24h windows butting up edge-to-edge (or
# overlapping, if this run happens to fire a little early).
MAX_AGE_HOURS_DEFAULT = 23


@dataclass
class Topic:
    title: str
    summary: str
    link: str
    category: str
    rank: int = 0  # position within its source feed (0 = that outlet's top story)
    source_domain: str = ""  # e.g. "tagesschau.de" — which outlet this entry came from
    source_count: int = 1  # how many DISTINCT outlets reported this same story
    target_length: int = 0  # suggested script chars for this topic, set once topics are picked


def fetch_topics(config: dict) -> list[Topic]:
    """Picks topics until their estimated combined length fills `target_chars`.

    `config["categories"]` is a {category: weight} map (higher weight = more
    of the character budget for that category, weight <= 0 excludes it).
    `config["num_topics"]` is a safety ceiling on total topic count, not a
    target — the real driver is `config["target_chars"]`: topics keep getting
    added (longest-article-summaries-first won't help fill a *specific*
    count, so this fills a *character budget* instead) until the category's
    share of that budget is used up, estimating a topic's script footprint
    from its RSS summary length. A long, detailed source article earns a
    bigger target_length (set on the picked topics); a thin one gets just
    enough for a one-line mention. This naturally produces however many
    topics the day's actual news volume supports, instead of a fixed count.

    Within a category, candidates are deduplicated (same story reported by
    multiple feeds is merged into one, with source_count tracking how many
    outlets ran it) and ranked by source_count desc, then by rank asc (each
    outlet's own editorial position — index 0 is that outlet's top story).
    """
    weights = {c: w for c, w in config["categories"].items() if w > 0}
    feeds_by_category = config["feeds"]
    max_topics = config["num_topics"]
    target_chars = config["target_chars"]
    expansion_ratio = config.get("summary_expansion_ratio", SUMMARY_EXPANSION_RATIO)
    max_age_hours = config.get("max_age_hours", MAX_AGE_HOURS_DEFAULT)

    if not weights:
        raise RuntimeError("No category in config.yaml has a positive weight.")

    per_category: dict[str, list[Topic]] = {}
    for category in weights:
        urls = feeds_by_category.get(category, [])
        if not urls:
            logger.warning("No feed URLs configured for category '%s', skipping.", category)
            continue
        candidates = _fetch_category(category, urls, max_age_hours)
        per_category[category] = _rank_and_dedup(candidates)

    if not any(per_category.values()):
        raise RuntimeError(
            "No topics could be fetched from any configured feed. "
            "Check your network connection and the feed URLs in config.yaml."
        )

    char_quotas = _weighted_quotas(weights, target_chars)

    picked: list[Topic] = []
    seen_titles: set[str] = set()
    leftover_chars = 0
    for category, quota in char_quotas.items():
        entries = per_category.get(category, [])
        used_chars = 0
        while entries and used_chars < quota and len(picked) < max_topics:
            candidate = entries.pop(0)
            if candidate.title in seen_titles:
                continue
            seen_titles.add(candidate.title)
            picked.append(candidate)
            used_chars += max(len(candidate.summary), MIN_SUMMARY_WEIGHT) * expansion_ratio
        leftover_chars += max(quota - used_chars, 0)

    fallback_order = sorted(weights, key=lambda c: weights[c], reverse=True)
    i = 0
    while leftover_chars > 0 and len(picked) < max_topics and any(per_category.get(c) for c in fallback_order):
        category = fallback_order[i % len(fallback_order)]
        entries = per_category.get(category, [])
        if entries:
            candidate = entries.pop(0)
            if candidate.title not in seen_titles:
                seen_titles.add(candidate.title)
                picked.append(candidate)
                leftover_chars -= max(len(candidate.summary), MIN_SUMMARY_WEIGHT) * expansion_ratio
        i += 1

    _assign_target_lengths(picked, target_chars)
    return picked


def _assign_target_lengths(topics: list[Topic], target_chars: int) -> None:
    """Distributes target_chars across picked topics, proportional to summary length."""
    if not topics:
        return
    weights = [max(len(t.summary), MIN_SUMMARY_WEIGHT) for t in topics]
    total_weight = sum(weights)
    for topic, weight in zip(topics, weights):
        topic.target_length = round(target_chars * weight / total_weight)


def _weighted_quotas(weights: dict[str, float], total: int) -> dict[str, int]:
    """Apportions `total` (topic count or character budget) across categories by weight (largest-remainder method)."""
    total_weight = sum(weights.values())
    raw = {c: total * w / total_weight for c, w in weights.items()}
    quotas = {c: int(v) for c, v in raw.items()}
    remainder = total - sum(quotas.values())
    by_fraction = sorted(weights, key=lambda c: raw[c] - quotas[c], reverse=True)
    for c in by_fraction[:remainder]:
        quotas[c] += 1
    return quotas


def _fetch_category(category: str, urls: list[str], max_age_hours: float) -> list[Topic]:
    topics = []
    for url in urls:
        domain = urlparse(url).netloc.removeprefix("www.")
        try:
            parsed = feedparser.parse(url)
            if parsed.bozo and not parsed.entries:
                logger.warning("Feed '%s' (%s) failed to parse: %s", category, url, parsed.bozo_exception)
                continue
            rank = 0
            for entry in parsed.entries:
                age_hours = _entry_age_hours(entry)
                if age_hours is not None and age_hours > max_age_hours:
                    continue
                title = entry.get("title", "").strip()
                if title.startswith(AD_TITLE_PREFIXES):
                    continue
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                topics.append(
                    Topic(
                        title=title,
                        summary=_strip_html(summary).strip(),
                        link=entry.get("link", ""),
                        category=category,
                        rank=rank,
                        source_domain=domain,
                    )
                )
                rank += 1
        except Exception as exc:
            logger.warning("Could not fetch feed '%s' (%s): %s", category, url, exc)
    return topics


def _entry_age_hours(entry) -> float | None:
    """Hours since the entry's published/updated timestamp, or None if it has neither.

    Entries with no parseable date are kept (not excluded) - better to include
    genuinely fresh content that a feed just failed to timestamp than to
    silently drop it.
    """
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct:
        return None
    published_epoch = calendar.timegm(struct)
    return (time.time() - published_epoch) / 3600


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
