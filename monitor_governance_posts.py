"""Orchestrator: fetch DAO forum posts, pre-filter by keyword, classify with Gemini,
append qualified opportunities, update state. Designed to be idempotent and safe
to run every 30 minutes via GitHub Actions cron.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from classifier import classify_post, get_client


BASE = Path(__file__).parent
USER_AGENT = "dao-gov-watch/0.1 (governance consulting lead monitor)"
CONFIDENCE_THRESHOLD = 0.7
EXCERPT_MAX_CHARS = 1500
FETCH_TIMEOUT = 20

# Free tier: 15 req/min. Keep a 4.5s minimum gap between Gemini calls.
MIN_GEMINI_GAP_SECONDS = 4.5


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


def fetch_latest_posts(forum_url: str) -> list[dict]:
    """Hit {forum_url}/posts.json; return list of post dicts (newest first)."""
    url = forum_url.rstrip("/") + "/posts.json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("latest_posts", [])


def compile_patterns(keywords: dict) -> tuple[list[re.Pattern], list[re.Pattern]]:
    gov = [re.compile(p, re.IGNORECASE) for p in keywords["gov_surface"]]
    ask = [re.compile(p, re.IGNORECASE) for p in keywords["ask_surface"]]
    return gov, ask


def keyword_match(text: str, gov: list[re.Pattern], ask: list[re.Pattern]) -> bool:
    """AND-match: needs at least one gov hit AND at least one ask hit."""
    has_gov = any(p.search(text) for p in gov)
    if not has_gov:
        return False
    return any(p.search(text) for p in ask)


def post_url(forum_url: str, post: dict) -> str:
    slug = post.get("topic_slug", "")
    topic_id = post.get("topic_id", "")
    post_number = post.get("post_number", 1)
    suffix = f"/{post_number}" if post_number and post_number > 1 else ""
    return f"{forum_url.rstrip('/')}/t/{slug}/{topic_id}{suffix}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def gemini_gap(last_call_ts: float) -> float:
    """Return seconds to sleep before the next call to respect the free-tier RPM."""
    elapsed = time.monotonic() - last_call_ts
    remaining = MIN_GEMINI_GAP_SECONDS - elapsed
    return max(0.0, remaining)


def main() -> int:
    daos = load_json(BASE / "daos.json", [])
    keywords = load_json(BASE / "keywords.json", {"gov_surface": [], "ask_surface": []})
    state = load_json(BASE / "state.json", {})
    opportunities = load_json(BASE / "opportunities.json", [])

    gov_patterns, ask_patterns = compile_patterns(keywords)

    # Only build the Gemini client if we actually need it (so bootstrap-only runs
    # with no keyword matches don't fail when GEMINI_API_KEY is missing).
    client = None
    last_gemini_ts = 0.0

    totals = {"fetched": 0, "kw_pass": 0, "llm_pass": 0, "new_hits": 0, "bootstrap": 0, "forum_errors": 0}

    for dao in daos:
        name = dao["name"]
        forum_url = dao["forum_url"]
        print(f"[{name}] {forum_url}")

        try:
            posts = fetch_latest_posts(forum_url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
            print(f"  WARN: fetch failed ({type(e).__name__}); skipping this forum")
            totals["forum_errors"] += 1
            continue
        except Exception as e:
            print(f"  WARN: unexpected fetch error: {type(e).__name__}; skipping")
            totals["forum_errors"] += 1
            continue

        if not posts:
            print("  (no posts returned)")
            continue

        # Bootstrap: first time we see this forum, record the max id without alerting.
        last_seen = state.get(forum_url)
        max_id_in_batch = max(p.get("id", 0) for p in posts)

        if last_seen is None:
            state[forum_url] = max_id_in_batch
            totals["bootstrap"] += 1
            print(f"  BOOTSTRAP: recorded last_seen={max_id_in_batch}; no alerts this run")
            continue

        new_posts = [p for p in posts if p.get("id", 0) > last_seen]
        totals["fetched"] += len(new_posts)
        if not new_posts:
            print("  (no new posts since last run)")
            continue

        for post in sorted(new_posts, key=lambda p: p.get("id", 0)):
            title = (post.get("topic_title") or "").strip()
            body_text = strip_html(post.get("cooked", ""))
            combined = f"{title}\n{body_text}"

            if not keyword_match(combined, gov_patterns, ask_patterns):
                continue
            totals["kw_pass"] += 1

            # Rate-limit before each Gemini call.
            time.sleep(gemini_gap(last_gemini_ts))
            if client is None:
                try:
                    client = get_client()
                except RuntimeError as e:
                    print(f"  ERROR: {e}")
                    print(f"  keyword-pass posts will be skipped this run (no key)")
                    break
            classification = classify_post(name, title, body_text[:EXCERPT_MAX_CHARS], client=client)
            last_gemini_ts = time.monotonic()

            if classification is None:
                continue
            if not classification.is_opportunity:
                continue
            if classification.confidence < CONFIDENCE_THRESHOLD:
                continue
            totals["llm_pass"] += 1

            hit = {
                "ts": now_iso(),
                "dao": name,
                "forum_url": forum_url,
                "post_url": post_url(forum_url, post),
                "title": title,
                "opportunity_type": classification.opportunity_type,
                "call_to_action": classification.call_to_action,
                "confidence": round(classification.confidence, 3),
                "one_line_reason": classification.one_line_reason,
            }
            opportunities.append(hit)
            totals["new_hits"] += 1
            print(f"  HIT: [{classification.opportunity_type}] {title!r} (conf={classification.confidence:.2f})")

        state[forum_url] = max(last_seen, max_id_in_batch)

    save_json(BASE / "state.json", state)
    save_json(BASE / "opportunities.json", opportunities)

    print()
    print(
        f"Summary: fetched_new={totals['fetched']}  kw_pass={totals['kw_pass']}  "
        f"llm_pass={totals['llm_pass']}  new_hits={totals['new_hits']}  "
        f"bootstrapped={totals['bootstrap']}  forum_errors={totals['forum_errors']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
