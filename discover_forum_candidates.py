"""Discover additional DAO forums from DeFiLlama and publish a review report."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional


BASE = Path(__file__).parent
CONFIG_PATH = BASE / "forum_discovery_config.json"
DAOS_PATH = BASE / "daos.json"
JSON_OUTPUT_PATH = BASE / "forum_candidates.json"
MARKDOWN_OUTPUT_PATH = BASE / "forum_candidates.md"

DEFI_LLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"
DEFI_LLAMA_FEES_URL = "https://api.llama.fi/overview/fees"
FORUM_PREFIXES = ("gov", "forum", "discuss", "community", "research")
USER_AGENT = "dao-gov-watch/0.1 (forum discovery)"
FETCH_TIMEOUT = 20
MAX_FETCH_ATTEMPTS = 3
MAX_FORUM_WORKERS = 8
DEFAULT_TOP_N = 25
DEFAULT_MIN_SCORE = 0.45
ADD_NOW_THRESHOLD = 0.65
REVIEW_THRESHOLD = 0.45


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def save_text(path: Path, content: str) -> None:
    path.write_text(content)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or now_utc()
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def fetch_json(url: str, *, timeout: int = FETCH_TIMEOUT) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == MAX_FETCH_ATTEMPTS:
                raise
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == MAX_FETCH_ATTEMPTS:
                raise

        time.sleep(attempt)

    assert last_error is not None
    raise last_error


def build_alias_maps(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    slug_to_key: dict[str, str] = {}
    name_to_key: dict[str, str] = {}
    family_names: dict[str, str] = {}

    for canonical_key, family in config.get("family_aliases", {}).items():
        protocol_name = str(family.get("protocol_name") or canonical_key)
        family_names[canonical_key] = protocol_name
        name_to_key[normalize_key(protocol_name)] = canonical_key
        name_to_key[normalize_key(canonical_key)] = canonical_key

        for slug in family.get("defillama_slugs", []):
            slug_to_key[str(slug)] = canonical_key

        for fee_name in family.get("fee_names", []):
            name_to_key[normalize_key(str(fee_name))] = canonical_key

    return slug_to_key, name_to_key, family_names


def build_tracked_keys(daos: list[dict[str, str]], family_names: dict[str, str]) -> set[str]:
    tracked = {normalize_key(dao.get("name", "")) for dao in daos if dao.get("name")}
    canonical_keys = set(tracked)
    for canonical_key, protocol_name in family_names.items():
        if normalize_key(protocol_name) in tracked:
            canonical_keys.add(canonical_key)
    return canonical_keys


def canonical_key_for_protocol(row: dict[str, Any], slug_to_key: dict[str, str], name_to_key: dict[str, str]) -> str:
    slug = str(row.get("slug") or "")
    if slug in slug_to_key:
        return slug_to_key[slug]

    name = str(row.get("name") or "")
    normalized_name = normalize_key(name)
    if normalized_name in name_to_key:
        return name_to_key[normalized_name]

    return normalized_name


def canonical_key_for_fee_row(row: dict[str, Any], name_to_key: dict[str, str]) -> str:
    raw_name = str(row.get("displayName") or row.get("name") or "")
    normalized_name = normalize_key(raw_name)
    return name_to_key.get(normalized_name, normalized_name)


def build_candidate(protocol_name: str, canonical_key: str) -> dict[str, Any]:
    return {
        "protocol_name": protocol_name,
        "canonical_key": canonical_key,
        "defillama_slugs": set(),
        "category": "",
        "tvl": 0.0,
        "fees_7d": 0.0,
        "forum_url": "",
        "forum_status": "unvalidated",
        "latest_post_ts": None,
        "score": 0.0,
        "recommendation": "skip",
        "reason": "",
        "forum_activity_score": 0.0,
        "tvl_percentile": 0.0,
        "fees_7d_percentile": 0.0,
        "pre_score": 0.0,
        "source_url": "",
        "source_names": set(),
        "_anchor_tvl": -1.0,
    }


def aggregate_protocols(
    protocol_rows: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    tracked_keys: set[str],
    slug_to_key: dict[str, str],
    name_to_key: dict[str, str],
    family_names: dict[str, str],
) -> dict[str, dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    ignored_categories = set(config.get("ignored_categories", []))
    ignored_slugs = {str(slug) for slug in config.get("ignored_slugs", [])}

    for row in protocol_rows:
        slug = str(row.get("slug") or "")
        category = str(row.get("category") or "")
        if not slug:
            continue
        if row.get("parentProtocol") and slug not in slug_to_key:
            continue
        if category in ignored_categories or slug in ignored_slugs:
            continue

        canonical_key = canonical_key_for_protocol(row, slug_to_key, name_to_key)
        if canonical_key in tracked_keys:
            continue

        protocol_name = family_names.get(canonical_key, str(row.get("name") or canonical_key))
        candidate = candidates.setdefault(canonical_key, build_candidate(protocol_name, canonical_key))

        tvl = float(row.get("tvl") or 0.0)
        candidate["tvl"] += tvl
        candidate["defillama_slugs"].add(slug)
        candidate["source_names"].add(str(row.get("name") or ""))

        if tvl >= candidate["_anchor_tvl"]:
            candidate["_anchor_tvl"] = tvl
            candidate["category"] = category
            candidate["source_url"] = str(row.get("url") or "")

    return candidates


def apply_fee_data(
    candidates: dict[str, dict[str, Any]],
    fee_rows: list[dict[str, Any]],
    *,
    tracked_keys: set[str],
    name_to_key: dict[str, str],
    config: dict[str, Any],
) -> None:
    ignored_categories = set(config.get("ignored_categories", []))
    for row in fee_rows:
        raw_name = str(row.get("displayName") or row.get("name") or "")
        if not raw_name:
            continue
        if str(row.get("category") or "") in ignored_categories:
            continue

        canonical_key = canonical_key_for_fee_row(row, name_to_key)
        if canonical_key in tracked_keys:
            continue
        candidate = candidates.get(canonical_key)
        if candidate is None:
            continue

        candidate["fees_7d"] += float(row.get("total7d") or 0.0)


def percentile_rank(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return 1.0

    ordered = sorted(values)
    index = bisect_right(ordered, value) - 1
    return max(0.0, min(1.0, index / (len(ordered) - 1)))


def apply_percentiles(candidates: dict[str, dict[str, Any]]) -> None:
    tvls = [float(candidate["tvl"]) for candidate in candidates.values()]
    fees = [float(candidate["fees_7d"]) for candidate in candidates.values()]

    for candidate in candidates.values():
        candidate["tvl_percentile"] = percentile_rank(float(candidate["tvl"]), tvls)
        candidate["fees_7d_percentile"] = percentile_rank(float(candidate["fees_7d"]), fees)
        candidate["pre_score"] = round((0.45 * candidate["tvl_percentile"]) + (0.35 * candidate["fees_7d_percentile"]), 4)


def shortlist_candidates(
    candidates: dict[str, dict[str, Any]],
    *,
    top_n: int,
    override_keys: set[str],
) -> list[dict[str, Any]]:
    ranked = sorted(
        candidates.values(),
        key=lambda candidate: (-candidate["pre_score"], -candidate["tvl"], candidate["protocol_name"].lower()),
    )
    selected_keys = {candidate["canonical_key"] for candidate in ranked[:top_n]}
    selected_keys.update(key for key in override_keys if key in candidates)
    return [candidate for candidate in ranked if candidate["canonical_key"] in selected_keys]


def apex_domain(hostname: str) -> str:
    host = hostname.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def candidate_forum_urls(candidate: dict[str, Any], config: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    override = config.get("forum_overrides", {}).get(candidate["canonical_key"])
    if override:
        urls.append(str(override))

    source_url = str(candidate.get("source_url") or "")
    parsed = urllib.parse.urlparse(source_url)
    hostname = parsed.hostname or ""
    if hostname:
        scheme = parsed.scheme or "https"
        host_url = f"{scheme}://{hostname}"
        if hostname.startswith(FORUM_PREFIXES):
            urls.append(host_url)

        root = apex_domain(hostname)
        for prefix in FORUM_PREFIXES:
            urls.append(f"https://{prefix}.{root}")

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        clean = url.rstrip("/")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def classify_url_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json", "Forum probe returned non-JSON content."

    if isinstance(exc, TimeoutError):
        return "timeout", "Forum probe timed out."

    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}", f"Forum probe returned HTTP {exc.code}."

    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            return "timeout", "Forum probe timed out."
        if isinstance(reason, ssl.SSLError):
            return "tls_error", "TLS handshake failed while probing the forum."
        if isinstance(reason, socket.gaierror):
            return "dns_error", "Forum hostname did not resolve."
        reason_text = str(reason).lower()
        if "timed out" in reason_text:
            return "timeout", "Forum probe timed out."
        if "ssl" in reason_text or "tls" in reason_text or "certificate" in reason_text:
            return "tls_error", "TLS handshake failed while probing the forum."
        if "name or service not known" in reason_text or "nodename nor servname provided" in reason_text:
            return "dns_error", "Forum hostname did not resolve."
        return "url_error", f"Forum probe failed: {reason}."

    return "unexpected_error", f"Forum probe failed: {type(exc).__name__}."


def validate_forum_url(
    forum_url: str,
    *,
    fetcher: Callable[[str], Any] = fetch_json,
) -> dict[str, Any]:
    posts_url = forum_url.rstrip("/") + "/posts.json"
    try:
        data = fetcher(posts_url)
    except Exception as exc:
        status, reason = classify_url_error(exc)
        return {
            "forum_url": forum_url.rstrip("/"),
            "forum_status": status,
            "latest_post_ts": None,
            "reason": reason,
        }

    if not isinstance(data, dict):
        return {
            "forum_url": forum_url.rstrip("/"),
            "forum_status": "not_discourse",
            "latest_post_ts": None,
            "reason": "Endpoint did not return a JSON object.",
        }

    latest_posts = data.get("latest_posts")
    if not isinstance(latest_posts, list):
        return {
            "forum_url": forum_url.rstrip("/"),
            "forum_status": "not_discourse",
            "latest_post_ts": None,
            "reason": "Endpoint did not return Discourse latest_posts data.",
        }

    latest_post_ts: Optional[str] = None
    latest_post_dt: Optional[datetime] = None
    for post in latest_posts:
        if not isinstance(post, dict):
            continue
        raw_ts = post.get("updated_at") or post.get("created_at")
        if not raw_ts:
            continue
        try:
            parsed = parse_ts(str(raw_ts))
        except ValueError:
            continue
        if latest_post_dt is None or parsed > latest_post_dt:
            latest_post_dt = parsed
            latest_post_ts = parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    return {
        "forum_url": forum_url.rstrip("/"),
        "forum_status": "ok",
        "latest_post_ts": latest_post_ts,
        "reason": "Validated public Discourse forum.",
    }


def choose_best_probe_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "forum_url": "",
            "forum_status": "unvalidated",
            "latest_post_ts": None,
            "reason": "No candidate forum URLs were available to probe.",
        }

    for result in results:
        if result["forum_status"] == "ok":
            return result

    priority = {
        "tls_error": 0,
        "http_403": 1,
        "dns_error": 2,
        "timeout": 3,
        "not_discourse": 4,
        "invalid_json": 5,
        "http_404": 6,
        "url_error": 7,
        "unexpected_error": 8,
        "unvalidated": 9,
    }
    return sorted(results, key=lambda result: priority.get(result["forum_status"], 99))[0]


def forum_activity_score(latest_post_ts: Optional[str], *, now: datetime) -> float:
    if not latest_post_ts:
        return 0.0

    try:
        latest_post = parse_ts(latest_post_ts)
    except ValueError:
        return 0.0

    age = now - latest_post
    if age <= timedelta(days=7):
        return 1.0
    if age <= timedelta(days=30):
        return 0.7
    if age <= timedelta(days=90):
        return 0.4
    return 0.1


def choose_recommendation(candidate: dict[str, Any]) -> str:
    if candidate["forum_status"] == "ok":
        if candidate["score"] >= ADD_NOW_THRESHOLD:
            return "add_now"
        if candidate["score"] >= REVIEW_THRESHOLD:
            return "review"
        return "skip"

    if candidate["forum_status"] == "tls_error" and candidate.get("override_forum_match") and candidate["pre_score"] >= REVIEW_THRESHOLD:
        return "review"

    return "skip"


def build_reason(candidate: dict[str, Any]) -> str:
    status = candidate["forum_status"]
    recommendation = candidate["recommendation"]

    if status == "ok":
        latest_post_ts = candidate["latest_post_ts"]
        if recommendation == "add_now":
            return f"Validated forum with recent activity; blended score {candidate['score']:.2f} clears the add-now threshold."
        if recommendation == "review":
            return f"Validated forum but blended score {candidate['score']:.2f} is borderline; review before adding."
        if latest_post_ts:
            return f"Validated forum, but blended score {candidate['score']:.2f} is below the review threshold."
        return "Validated forum, but the latest post timestamp was unavailable."

    if status == "tls_error" and recommendation == "review":
        return "Forum override looks promising, but TLS validation failed locally; retry under Python 3.11 CI before discarding."

    return candidate["reason"]


def evaluate_candidate(
    candidate: dict[str, Any],
    *,
    config: dict[str, Any],
    now: datetime,
    fetcher: Callable[[str], Any] = fetch_json,
) -> dict[str, Any]:
    probe_results = [
        validate_forum_url(url, fetcher=fetcher)
        for url in candidate_forum_urls(candidate, config)
    ]
    probe_result = choose_best_probe_result(probe_results)
    override_url = str(config.get("forum_overrides", {}).get(candidate["canonical_key"], "")).rstrip("/")
    candidate["forum_url"] = probe_result["forum_url"]
    candidate["forum_status"] = probe_result["forum_status"]
    candidate["latest_post_ts"] = probe_result["latest_post_ts"]
    candidate["reason"] = probe_result["reason"]
    candidate["override_forum_match"] = bool(override_url and probe_result["forum_url"] == override_url)
    candidate["forum_activity_score"] = forum_activity_score(candidate["latest_post_ts"], now=now)
    candidate["score"] = round(
        (0.45 * candidate["tvl_percentile"])
        + (0.35 * candidate["fees_7d_percentile"])
        + (0.20 * candidate["forum_activity_score"]),
        4,
    )
    candidate["recommendation"] = choose_recommendation(candidate)
    candidate["reason"] = build_reason(candidate)
    return candidate


def evaluate_existing_forums(
    daos: list[dict[str, str]],
    *,
    fetcher: Callable[[str], Any] = fetch_json,
) -> list[dict[str, Any]]:
    broken: list[dict[str, Any]] = []

    def inspect_dao(dao: dict[str, str]) -> Optional[dict[str, Any]]:
        name = str(dao.get("name") or "")
        forum_url = str(dao.get("forum_url") or "")
        if not name or not forum_url:
            return None

        result = validate_forum_url(forum_url, fetcher=fetcher)
        if result["forum_status"] == "ok":
            return None

        return {
            "protocol_name": name,
            "canonical_key": normalize_key(name),
            "defillama_slugs": [],
            "category": "tracked_watchlist",
            "tvl": 0.0,
            "fees_7d": 0.0,
            "forum_url": forum_url.rstrip("/"),
            "forum_status": result["forum_status"],
            "latest_post_ts": result["latest_post_ts"],
            "score": 0.0,
            "recommendation": "existing_broken",
            "reason": f"Tracked forum failed validation: {result['reason']}",
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_FORUM_WORKERS) as executor:
        futures = [executor.submit(inspect_dao, dao) for dao in daos]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                broken.append(result)

    return sorted(broken, key=lambda item: item["protocol_name"].lower())


def serialize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_name": candidate["protocol_name"],
        "canonical_key": candidate["canonical_key"],
        "defillama_slugs": sorted(candidate["defillama_slugs"]),
        "category": candidate["category"],
        "tvl": round(float(candidate["tvl"]), 2),
        "fees_7d": round(float(candidate["fees_7d"]), 2),
        "forum_url": candidate["forum_url"],
        "forum_status": candidate["forum_status"],
        "latest_post_ts": candidate["latest_post_ts"],
        "score": round(float(candidate["score"]), 4),
        "recommendation": candidate["recommendation"],
        "reason": candidate["reason"],
        "tvl_percentile": round(float(candidate["tvl_percentile"]), 4),
        "fees_7d_percentile": round(float(candidate["fees_7d_percentile"]), 4),
        "forum_activity_score": round(float(candidate["forum_activity_score"]), 4),
        "pre_score": round(float(candidate["pre_score"]), 4),
        "override_forum_match": bool(candidate.get("override_forum_match")),
    }


def format_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.0f}"


def format_forum_cell(item: dict[str, Any]) -> str:
    if item["forum_url"]:
        return f"[{item['forum_status']}]({item['forum_url']})"
    return item["forum_status"]


def render_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_None_\n"

    lines = [
        "| Protocol | Category | TVL | Fees 7d | Forum | Latest Post | Score | Reason |",
        "|---|---|---:|---:|---|---|---:|---|",
    ]
    for item in items:
        latest_post = item["latest_post_ts"] or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    item["protocol_name"],
                    item["category"] or "-",
                    format_money(float(item["tvl"])),
                    format_money(float(item["fees_7d"])),
                    format_forum_cell(item),
                    latest_post,
                    f"{float(item['score']):.2f}",
                    item["reason"],
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def render_existing_broken(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_None_\n"

    lines = [
        "| DAO | Forum | Status | Reason |",
        "|---|---|---|---|",
    ]
    for item in items:
        forum_link = f"[{item['forum_url']}]({item['forum_url']})"
        lines.append(f"| {item['protocol_name']} | {forum_link} | {item['forum_status']} | {item['reason']} |")
    return "\n".join(lines) + "\n"


def render_markdown_report(
    *,
    generated_at: str,
    top_n: int,
    min_score: float,
    candidates: list[dict[str, Any]],
    existing_broken: list[dict[str, Any]],
) -> str:
    add_now = [item for item in candidates if item["recommendation"] == "add_now"]
    review = [item for item in candidates if item["recommendation"] == "review"]
    rejected = [
        item
        for item in candidates
        if item["recommendation"] == "skip" and (float(item["pre_score"]) >= min_score or item["forum_status"] != "ok")
    ]

    return (
        "# Forum Discovery Candidates\n\n"
        f"- Generated: `{generated_at}`\n"
        f"- Reviewed shortlist size: `{top_n}`\n"
        f"- Display threshold: `{min_score:.2f}`\n"
        "- Add entries manually to `daos.json` after review.\n\n"
        "## Add Now\n\n"
        f"{render_table(add_now)}\n"
        "## Review\n\n"
        f"{render_table(review)}\n"
        "## Rejected\n\n"
        f"{render_table(rejected)}\n"
        "## Existing But Broken\n\n"
        f"{render_existing_broken(existing_broken)}"
    )


def sort_candidates_for_output(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendation_rank = {"add_now": 0, "review": 1, "skip": 2}
    return sorted(
        candidates,
        key=lambda item: (
            recommendation_rank.get(item["recommendation"], 99),
            -float(item["score"]),
            -float(item["pre_score"]),
            item["protocol_name"].lower(),
        ),
    )


def discover_candidates(
    *,
    protocols_payload: list[dict[str, Any]],
    fees_payload: dict[str, Any],
    daos: list[dict[str, str]],
    config: dict[str, Any],
    top_n: int,
    now: datetime,
    fetcher: Callable[[str], Any] = fetch_json,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    slug_to_key, name_to_key, family_names = build_alias_maps(config)
    tracked_keys = build_tracked_keys(daos, family_names)
    candidates = aggregate_protocols(
        protocols_payload,
        config=config,
        tracked_keys=tracked_keys,
        slug_to_key=slug_to_key,
        name_to_key=name_to_key,
        family_names=family_names,
    )
    apply_fee_data(
        candidates,
        list(fees_payload.get("protocols", [])),
        tracked_keys=tracked_keys,
        name_to_key=name_to_key,
        config=config,
    )
    apply_percentiles(candidates)

    shortlisted = shortlist_candidates(
        candidates,
        top_n=top_n,
        override_keys=set(config.get("forum_overrides", {}).keys()),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_FORUM_WORKERS) as executor:
        futures = [
            executor.submit(evaluate_candidate, dict(candidate), config=config, now=now, fetcher=fetcher)
            for candidate in shortlisted
        ]
        evaluated = [future.result() for future in concurrent.futures.as_completed(futures)]
    serialized = sort_candidates_for_output([serialize_candidate(candidate) for candidate in evaluated])
    existing_broken = evaluate_existing_forums(daos, fetcher=fetcher)
    return serialized, existing_broken


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Number of ranked candidates to probe and report.")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE, help="Lower bound used when showing rejected candidates.")
    args = parser.parse_args()
    if args.top_n < 1:
        parser.error("--top-n must be >= 1")
    if args.min_score < 0:
        parser.error("--min-score must be >= 0")
    return args


def main() -> int:
    args = parse_args()

    config = load_json(CONFIG_PATH, {})
    daos = load_json(DAOS_PATH, [])
    generated_at = now_iso()
    now = parse_ts(generated_at)

    protocols_payload = fetch_json(DEFI_LLAMA_PROTOCOLS_URL)
    fees_payload = fetch_json(DEFI_LLAMA_FEES_URL)
    candidates, existing_broken = discover_candidates(
        protocols_payload=protocols_payload,
        fees_payload=fees_payload,
        daos=daos,
        config=config,
        top_n=args.top_n,
        now=now,
    )

    report = {
        "generated_at": generated_at,
        "top_n": args.top_n,
        "min_score": args.min_score,
        "candidates": candidates,
        "existing_broken": existing_broken,
    }
    markdown = render_markdown_report(
        generated_at=generated_at,
        top_n=args.top_n,
        min_score=args.min_score,
        candidates=candidates,
        existing_broken=existing_broken,
    )

    save_json(JSON_OUTPUT_PATH, report)
    save_text(MARKDOWN_OUTPUT_PATH, markdown)

    add_now = sum(1 for candidate in candidates if candidate["recommendation"] == "add_now")
    review = sum(1 for candidate in candidates if candidate["recommendation"] == "review")
    rejected = sum(1 for candidate in candidates if candidate["recommendation"] == "skip")
    print(
        f"Wrote {JSON_OUTPUT_PATH.name} and {MARKDOWN_OUTPUT_PATH.name} "
        f"(add_now={add_now}, review={review}, rejected={rejected}, existing_broken={len(existing_broken)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
