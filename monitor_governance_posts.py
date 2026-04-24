"""Monitor DAO forums for live and historical governance opportunities.

Live mode checks only newly visible posts since the stored cursor.
Backfill mode paginates through the last N days of public Discourse posts,
deduplicating by post URL and preserving the live cursor for future runs.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional, Tuple

from classifier import classify_post, get_client


BASE = Path(__file__).parent
USER_AGENT = "dao-gov-watch/0.1 (governance consulting lead monitor)"
CONFIDENCE_THRESHOLD = 0.7
EXCERPT_MAX_CHARS = 1500
FETCH_TIMEOUT = 20
DEFAULT_BACKFILL_DAYS = 30
LIVE_MODE = "live"
BACKFILL_MODE = "backfill"
DISCOURSE_PAGE_WINDOW = 50
MAX_EMPTY_BACKFILL_PAGES = 2
DISALLOWED_OPPORTUNITY_TYPES = {"grant"}

# Free tier: 15 req/min. Keep a 4.5s minimum gap between Gemini calls.
MIN_GEMINI_GAP_SECONDS = 4.5

SELF_PROMO_PHRASES = (
    "i offer",
    "we offer",
    "i provide",
    "we provide",
    "offering",
    "offer of services",
    "dm me",
    "message me",
    "happy to help",
    "available for",
    "our services",
    "my services",
    "services available",
    "contractor applying",
    "pitching",
)
PROTOCOL_ASK_PHRASES = (
    "rfp",
    "request for proposal",
    "request for proposals",
    "submit proposal",
    "we need",
    "we are looking",
    "we're looking",
    "we are seeking",
    "we're seeking",
    "apply via",
    "apply by",
    "applications open",
    "budget",
)
CONSULTING_SCOPE_PHRASES = (
    "governance",
    "tokenomics",
    "research",
    "go-to-market",
    "go to market",
    "gtm",
    "market positioning",
    "incentive",
    "delegat",
)


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


_tag_re = re.compile(r"<[^>]+>")
_ws_re = re.compile(r"\s+")


def strip_html(html: str) -> str:
    text = _tag_re.sub(" ", html or "")
    text = unescape(text)
    text = _ws_re.sub(" ", text).strip()
    return text


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or now_utc()
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def gemini_gap(last_call_ts: float) -> float:
    elapsed = time.monotonic() - last_call_ts
    remaining = MIN_GEMINI_GAP_SECONDS - elapsed
    return max(0.0, remaining)


def discourse_post_ts(post: dict) -> Optional[str]:
    for key in ("created_at", "updated_at"):
        raw = post.get(key)
        if raw:
            return raw
    return None


def discourse_post_dt(post: dict) -> Optional[datetime]:
    raw = discourse_post_ts(post)
    if not raw:
        return None
    try:
        return parse_ts(raw)
    except ValueError:
        return None


def post_id(post: dict) -> int:
    try:
        return int(post.get("id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def fetch_posts_page(forum_url: str, before: Optional[int] = None) -> list[dict]:
    """Hit {forum_url}/posts.json, optionally paginated by the `before` cursor."""
    params: dict[str, str] = {}
    if before is not None:
        params["before"] = str(before)

    url = forum_url.rstrip("/") + "/posts.json"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("latest_posts", [])


def fetch_recent_posts(forum_url: str, cutoff: datetime) -> Tuple[list[dict], Optional[int]]:
    """Paginate public Discourse posts until the scan falls past the cutoff."""
    collected: list[dict] = []
    seen_post_ids: set[int] = set()
    before: Optional[int] = None
    latest_visible_id: Optional[int] = None
    empty_pages = 0

    while True:
        posts = fetch_posts_page(forum_url, before=before)
        if not posts:
            if before is None:
                break
            empty_pages += 1
            if empty_pages > MAX_EMPTY_BACKFILL_PAGES or before <= 1:
                break
            next_before = max(1, before - DISCOURSE_PAGE_WINDOW)
            if next_before >= before:
                break
            before = next_before
            continue

        empty_pages = 0
        visible_ids = [pid for pid in (post_id(post) for post in posts) if pid > 0]
        if not visible_ids:
            break

        page_latest_id = max(visible_ids)
        latest_visible_id = page_latest_id if latest_visible_id is None else max(latest_visible_id, page_latest_id)

        oldest_visible_dt: Optional[datetime] = None
        for post in posts:
            pid = post_id(post)
            if pid <= 0 or pid in seen_post_ids:
                continue

            seen_post_ids.add(pid)
            post_dt = discourse_post_dt(post)
            if post_dt is not None:
                oldest_visible_dt = post_dt if oldest_visible_dt is None else min(oldest_visible_dt, post_dt)
                if post_dt >= cutoff:
                    collected.append(post)

        if oldest_visible_dt is not None and oldest_visible_dt < cutoff:
            break

        next_before = min(visible_ids)
        if next_before <= 1:
            break
        if before is not None and next_before >= before:
            break
        before = next_before

    collected.sort(key=post_id)
    return collected, latest_visible_id


def compile_patterns(keywords: dict) -> tuple[list[re.Pattern], list[re.Pattern]]:
    gov = [re.compile(p, re.IGNORECASE) for p in keywords["gov_surface"]]
    ask = [re.compile(p, re.IGNORECASE) for p in keywords["ask_surface"]]
    return gov, ask


def keyword_match(text: str, gov: list[re.Pattern], ask: list[re.Pattern]) -> bool:
    has_gov = any(p.search(text) for p in gov)
    if not has_gov:
        return False
    return any(p.search(text) for p in ask)


def looks_like_service_provider_pitch(text: str) -> bool:
    lowered = text.lower()
    has_scope = any(phrase in lowered for phrase in CONSULTING_SCOPE_PHRASES)
    has_self_promo = any(phrase in lowered for phrase in SELF_PROMO_PHRASES)
    has_protocol_ask = any(phrase in lowered for phrase in PROTOCOL_ASK_PHRASES)
    return has_scope and has_self_promo and not has_protocol_ask


def post_url(forum_url: str, post: dict) -> str:
    slug = post.get("topic_slug", "")
    topic_id = post.get("topic_id", "")
    post_number = post.get("post_number", 1)
    suffix = f"/{post_number}" if post_number and post_number > 1 else ""
    return f"{forum_url.rstrip('/')}/t/{slug}/{topic_id}{suffix}"


def canonical_post_ts(item: dict[str, Any]) -> Optional[str]:
    value = item.get("post_ts") or item.get("ts") or item.get("detected_ts")
    return value if isinstance(value, str) and value else None


def canonical_detected_ts(item: dict[str, Any]) -> Optional[str]:
    value = item.get("detected_ts") or item.get("ts") or item.get("post_ts")
    return value if isinstance(value, str) and value else None


def canonical_ingest_mode(item: dict[str, Any]) -> str:
    raw = item.get("ingest_mode")
    if raw in {LIVE_MODE, BACKFILL_MODE}:
        return raw
    return LIVE_MODE


def normalize_opportunity(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    explicit_post_ts = normalized.get("post_ts")
    post_ts = explicit_post_ts if isinstance(explicit_post_ts, str) and explicit_post_ts else None
    detected_ts = canonical_detected_ts(normalized) or post_ts
    if post_ts is not None:
        normalized["post_ts"] = post_ts
    else:
        normalized.pop("post_ts", None)
    if detected_ts:
        normalized["detected_ts"] = detected_ts
    normalized["ingest_mode"] = canonical_ingest_mode(normalized)
    normalized.pop("ts", None)
    return normalized


def merge_opportunities(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged.update(incoming)
    merged["post_ts"] = existing.get("post_ts") or incoming["post_ts"] or canonical_post_ts(existing)
    merged["detected_ts"] = canonical_detected_ts(existing) or incoming["detected_ts"]
    merged["ingest_mode"] = canonical_ingest_mode(existing)
    merged.pop("ts", None)
    return normalize_opportunity(merged)


def load_opportunity_index(path: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in load_json(path, []):
        if not isinstance(item, dict):
            continue
        post_link = item.get("post_url")
        if not isinstance(post_link, str) or not post_link:
            continue
        normalized = normalize_opportunity(item)
        existing = index.get(post_link)
        index[post_link] = normalized if existing is None else merge_opportunities(existing, normalized)
    return index


class ClassifierSession:
    def __init__(self) -> None:
        self.client = None
        self.last_gemini_ts = 0.0
        self.disabled = False
        self.error_reported = False

    def classify(self, forum_name: str, title: str, excerpt: str):
        if self.disabled:
            return None

        time.sleep(gemini_gap(self.last_gemini_ts))
        if self.client is None:
            try:
                self.client = get_client()
            except RuntimeError as e:
                if not self.error_reported:
                    print(f"  ERROR: {e}")
                    print("  keyword-pass posts will be skipped this run (no key)")
                    self.error_reported = True
                self.disabled = True
                return None

        result = classify_post(forum_name, title, excerpt, client=self.client)
        self.last_gemini_ts = time.monotonic()
        return result


def build_opportunity(
    *,
    dao_name: str,
    forum_url: str,
    post: dict,
    detected_at: datetime,
    ingest_mode: str,
    title: str,
    classification,
) -> dict[str, Any]:
    detected_ts = now_iso(detected_at)
    post_ts = discourse_post_ts(post) or detected_ts
    return {
        "dao": dao_name,
        "forum_url": forum_url,
        "post_url": post_url(forum_url, post),
        "title": title,
        "post_ts": post_ts,
        "detected_ts": detected_ts,
        "ingest_mode": ingest_mode,
        "opportunity_type": classification.opportunity_type,
        "call_to_action": classification.call_to_action,
        "confidence": round(classification.confidence, 3),
        "one_line_reason": classification.one_line_reason,
    }


def process_posts(
    *,
    dao_name: str,
    forum_url: str,
    posts: list[dict],
    ingest_mode: str,
    gov_patterns: list[re.Pattern],
    ask_patterns: list[re.Pattern],
    classifier_session: ClassifierSession,
    opportunities: dict[str, dict[str, Any]],
    totals: dict[str, int],
) -> None:
    totals["posts_seen"] += len(posts)

    for post in sorted(posts, key=post_id):
        title = (post.get("topic_title") or "").strip()
        body_text = strip_html(post.get("cooked", ""))
        combined = f"{title}\n{body_text}"

        if not keyword_match(combined, gov_patterns, ask_patterns):
            continue
        totals["kw_pass"] += 1
        if looks_like_service_provider_pitch(combined):
            totals["rule_rejects"] += 1
            print(f"  SKIP: likely service-provider pitch {title!r}")
            continue

        classification = classifier_session.classify(dao_name, title, body_text[:EXCERPT_MAX_CHARS])
        if classification is None:
            continue
        if not classification.is_opportunity:
            continue
        if classification.opportunity_type in DISALLOWED_OPPORTUNITY_TYPES:
            totals["rule_rejects"] += 1
            print(f"  SKIP: out-of-scope {classification.opportunity_type} {title!r}")
            continue
        classification_context = "\n".join(
            part
            for part in (title, body_text, classification.call_to_action, classification.one_line_reason)
            if part
        )
        if looks_like_service_provider_pitch(classification_context):
            totals["rule_rejects"] += 1
            print(f"  SKIP: classifier accepted a service-provider pitch {title!r}")
            continue
        if classification.confidence < CONFIDENCE_THRESHOLD:
            continue
        totals["llm_pass"] += 1

        hit = build_opportunity(
            dao_name=dao_name,
            forum_url=forum_url,
            post=post,
            detected_at=now_utc(),
            ingest_mode=ingest_mode,
            title=title,
            classification=classification,
        )

        existing = opportunities.get(hit["post_url"])
        if existing is None:
            opportunities[hit["post_url"]] = hit
            totals["new_hits"] += 1
            print(f"  HIT: [{classification.opportunity_type}] {title!r} (conf={classification.confidence:.2f})")
            continue

        opportunities[hit["post_url"]] = merge_opportunities(existing, hit)
        totals["updated_hits"] += 1
        print(f"  REFRESH: [{classification.opportunity_type}] {title!r} (conf={classification.confidence:.2f})")


def update_state_cursor(state: dict[str, int], forum_url: str, latest_visible_id: Optional[int]) -> None:
    if latest_visible_id is None:
        return

    current = state.get(forum_url)
    if current is None:
        state[forum_url] = latest_visible_id
        return
    state[forum_url] = max(int(current), latest_visible_id)


def run_live_forum(
    *,
    dao: dict[str, str],
    state: dict[str, int],
    gov_patterns: list[re.Pattern],
    ask_patterns: list[re.Pattern],
    classifier_session: ClassifierSession,
    opportunities: dict[str, dict[str, Any]],
    totals: dict[str, int],
) -> None:
    name = dao["name"]
    forum_url = dao["forum_url"]
    print(f"[{name}] {forum_url}")

    try:
        posts = fetch_posts_page(forum_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        print(f"  WARN: fetch failed ({type(e).__name__}); skipping this forum")
        totals["forum_errors"] += 1
        return
    except Exception as e:
        print(f"  WARN: unexpected fetch error: {type(e).__name__}; skipping")
        totals["forum_errors"] += 1
        return

    if not posts:
        print("  (no posts returned)")
        return

    latest_visible_id = max((post_id(post) for post in posts), default=0) or None
    last_seen = state.get(forum_url)

    if last_seen is None:
        update_state_cursor(state, forum_url, latest_visible_id)
        totals["bootstrap"] += 1
        print(f"  BOOTSTRAP: recorded last_seen={latest_visible_id}; no alerts this run")
        return

    new_posts = [post for post in posts if post_id(post) > int(last_seen)]
    if not new_posts:
        update_state_cursor(state, forum_url, latest_visible_id)
        print("  (no new posts since last run)")
        return

    process_posts(
        dao_name=name,
        forum_url=forum_url,
        posts=new_posts,
        ingest_mode=LIVE_MODE,
        gov_patterns=gov_patterns,
        ask_patterns=ask_patterns,
        classifier_session=classifier_session,
        opportunities=opportunities,
        totals=totals,
    )
    update_state_cursor(state, forum_url, latest_visible_id)


def run_backfill_forum(
    *,
    dao: dict[str, str],
    state: dict[str, int],
    gov_patterns: list[re.Pattern],
    ask_patterns: list[re.Pattern],
    classifier_session: ClassifierSession,
    opportunities: dict[str, dict[str, Any]],
    totals: dict[str, int],
    cutoff: datetime,
) -> None:
    name = dao["name"]
    forum_url = dao["forum_url"]
    print(f"[{name}] {forum_url}")

    try:
        recent_posts, latest_visible_id = fetch_recent_posts(forum_url, cutoff)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        print(f"  WARN: fetch failed ({type(e).__name__}); skipping this forum")
        totals["forum_errors"] += 1
        return
    except Exception as e:
        print(f"  WARN: unexpected fetch error: {type(e).__name__}; skipping")
        totals["forum_errors"] += 1
        return

    update_state_cursor(state, forum_url, latest_visible_id)

    if not recent_posts:
        print("  (no public posts in backfill window)")
        return

    process_posts(
        dao_name=name,
        forum_url=forum_url,
        posts=recent_posts,
        ingest_mode=BACKFILL_MODE,
        gov_patterns=gov_patterns,
        ask_patterns=ask_patterns,
        classifier_session=classifier_session,
        opportunities=opportunities,
        totals=totals,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[LIVE_MODE, BACKFILL_MODE], default=LIVE_MODE)
    parser.add_argument("--days", type=int, default=DEFAULT_BACKFILL_DAYS, help="Backfill lookback window in days.")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be >= 1")
    return args


def main() -> int:
    args = parse_args()

    daos = load_json(BASE / "daos.json", [])
    keywords = load_json(BASE / "keywords.json", {"gov_surface": [], "ask_surface": []})
    state = load_json(BASE / "state.json", {})
    opportunities = load_opportunity_index(BASE / "opportunities.json")

    gov_patterns, ask_patterns = compile_patterns(keywords)
    classifier_session = ClassifierSession()
    totals = {
        "posts_seen": 0,
        "kw_pass": 0,
        "llm_pass": 0,
        "rule_rejects": 0,
        "new_hits": 0,
        "updated_hits": 0,
        "bootstrap": 0,
        "forum_errors": 0,
    }

    cutoff: Optional[datetime] = None
    if args.mode == BACKFILL_MODE:
        cutoff = now_utc() - timedelta(days=args.days)
        print(f"Running {BACKFILL_MODE} mode for posts since {now_iso(cutoff)}")
    else:
        print(f"Running {LIVE_MODE} mode")

    for dao in daos:
        if args.mode == BACKFILL_MODE:
            run_backfill_forum(
                dao=dao,
                state=state,
                gov_patterns=gov_patterns,
                ask_patterns=ask_patterns,
                classifier_session=classifier_session,
                opportunities=opportunities,
                totals=totals,
                cutoff=cutoff,
            )
            continue

        run_live_forum(
            dao=dao,
            state=state,
            gov_patterns=gov_patterns,
            ask_patterns=ask_patterns,
            classifier_session=classifier_session,
            opportunities=opportunities,
            totals=totals,
        )

    save_json(BASE / "state.json", state)
    save_json(BASE / "opportunities.json", list(opportunities.values()))

    print()
    print(
        f"Summary: mode={args.mode}  posts_seen={totals['posts_seen']}  "
        f"kw_pass={totals['kw_pass']}  llm_pass={totals['llm_pass']}  "
        f"rule_rejects={totals['rule_rejects']}  "
        f"new_hits={totals['new_hits']}  updated_hits={totals['updated_hits']}  "
        f"bootstrapped={totals['bootstrap']}  forum_errors={totals['forum_errors']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
