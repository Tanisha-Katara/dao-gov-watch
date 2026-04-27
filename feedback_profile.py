from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


LABEL_DONE = "done"
LABEL_NOT_RELEVANT = "not_relevant"
VALID_LABELS = {LABEL_DONE, LABEL_NOT_RELEVANT}
STOP_WORDS = {
    "about",
    "advisory",
    "again",
    "advisor",
    "after",
    "apply",
    "around",
    "because",
    "clear",
    "consulting",
    "could",
    "deliver",
    "external",
    "forum",
    "governance",
    "great",
    "helps",
    "help",
    "hiring",
    "into",
    "just",
    "looking",
    "maybe",
    "named",
    "need",
    "outside",
    "paid",
    "post",
    "proposal",
    "protocol",
    "quarterly",
    "reply",
    "report",
    "research",
    "review",
    "role",
    "scope",
    "seeking",
    "should",
    "signal",
    "similar",
    "still",
    "support",
    "their",
    "there",
    "these",
    "this",
    "thread",
    "through",
    "tokenomics",
    "update",
    "vendor",
    "with",
    "work",
}
TOKEN_RE = re.compile(r"[a-z0-9]{4,}")


def default_feedback_store() -> dict[str, Any]:
    return {"version": 1, "updated_at": None, "items": {}}


def load_feedback_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_feedback_store()

    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
        return raw

    if isinstance(raw, list):
        items: dict[str, dict[str, Any]] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            post_url = entry.get("post_url")
            if isinstance(post_url, str) and post_url:
                items[post_url] = entry
        return {"version": 1, "updated_at": None, "items": items}

    return default_feedback_store()


def normalize_feedback_entry(post_url: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    label = entry.get("label")
    if label not in VALID_LABELS:
        return None

    normalized = {
        "post_url": post_url,
        "label": label,
        "dao": str(entry.get("dao", "") or "").strip(),
        "opportunity_type": str(entry.get("opportunity_type", "") or "").strip(),
        "title": str(entry.get("title", "") or "").strip(),
        "call_to_action": str(entry.get("call_to_action", "") or "").strip(),
        "one_line_reason": str(entry.get("one_line_reason", "") or "").strip(),
        "updated_at": str(entry.get("updated_at", "") or "").strip(),
    }
    return normalized


def load_feedback_entries(path: Path) -> list[dict[str, Any]]:
    store = load_feedback_store(path)
    items = store.get("items", {})
    if not isinstance(items, dict):
        return []

    entries: list[dict[str, Any]] = []
    for post_url, entry in items.items():
        if not isinstance(post_url, str) or not post_url or not isinstance(entry, dict):
            continue
        normalized = normalize_feedback_entry(post_url, entry)
        if normalized is not None:
            entries.append(normalized)
    return entries


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    tokens = {match.group(0) for match in TOKEN_RE.finditer(text.lower())}
    return sorted(token for token in tokens if token not in STOP_WORDS)


def build_feedback_profile(entries: list[dict[str, Any]]) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "total": 0,
        "done": 0,
        "not_relevant": 0,
        "dao": {},
        "type": {},
        "tokens": {},
    }

    def add_weight(bucket: dict[str, float], key: str, delta: float) -> None:
        if not key:
            return
        bucket[key] = round(bucket.get(key, 0.0) + delta, 3)

    for entry in entries:
        label = entry.get("label")
        if label not in VALID_LABELS:
            continue

        profile["total"] += 1
        positive = label == LABEL_DONE
        if positive:
            profile["done"] += 1
        else:
            profile["not_relevant"] += 1

        direction = 1.0 if positive else -1.0
        add_weight(profile["dao"], entry.get("dao", ""), direction * 2.4)
        add_weight(profile["type"], entry.get("opportunity_type", ""), direction * 1.6)

        token_source = " ".join(
            part
            for part in (
                entry.get("dao", ""),
                entry.get("opportunity_type", ""),
                entry.get("title", ""),
                entry.get("call_to_action", ""),
                entry.get("one_line_reason", ""),
            )
            if part
        )
        for token in tokenize(token_source):
            add_weight(profile["tokens"], token, direction * 0.45)

    return profile


def score_feedback(profile: dict[str, Any], dao_name: str = "", opportunity_type: str = "", text: str = "") -> float:
    dao_score = float(profile.get("dao", {}).get(dao_name, 0.0))
    type_score = float(profile.get("type", {}).get(opportunity_type, 0.0))
    tokens = tokenize(" ".join(part for part in (dao_name, opportunity_type, text) if part))
    token_weights = profile.get("tokens", {})
    token_sum = sum(float(token_weights.get(token, 0.0)) for token in tokens)
    token_score = token_sum / min(len(tokens), 6) if tokens else 0.0
    return round(dao_score + type_score + token_score, 3)


def _top_keys(bucket: dict[str, float], *, positive: bool, limit: int = 3) -> list[str]:
    items = [(key, value) for key, value in bucket.items() if (value > 0 if positive else value < 0)]
    items.sort(key=lambda pair: pair[1], reverse=positive)
    return [key for key, _ in items[:limit]]


def build_preference_note(profile: dict[str, Any]) -> str:
    if profile.get("total", 0) == 0:
        return ""

    positives = _top_keys(profile.get("dao", {}), positive=True) + _top_keys(profile.get("type", {}), positive=True)
    negatives = _top_keys(profile.get("dao", {}), positive=False) + _top_keys(profile.get("type", {}), positive=False)
    positive_tokens = _top_keys(profile.get("tokens", {}), positive=True)
    negative_tokens = _top_keys(profile.get("tokens", {}), positive=False)

    parts = [
        "User preference hints from dashboard feedback. Use these only as tie-breakers after the core rubric.",
        f"Positive labels: {profile.get('done', 0)}.",
        f"Negative labels: {profile.get('not_relevant', 0)}.",
    ]
    if positives:
        parts.append("Stronger positive patterns: " + ", ".join(positives[:4]) + ".")
    if negatives:
        parts.append("Stronger negative patterns: " + ", ".join(negatives[:4]) + ".")
    if positive_tokens:
        parts.append("Positive topical hints: " + ", ".join(positive_tokens[:4]) + ".")
    if negative_tokens:
        parts.append("Negative topical hints: " + ", ".join(negative_tokens[:4]) + ".")
    return " ".join(parts)
