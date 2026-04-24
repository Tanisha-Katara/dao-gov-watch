from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Optional

import render_dashboard


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_item(title: str, *, post_ts: Optional[str] = None, detected_ts: Optional[str] = None, ingest_mode: str = "live") -> dict:
    item = {
        "dao": "Aave",
        "forum_url": "https://governance.aave.com",
        "post_url": f"https://governance.aave.com/t/{title.lower().replace(' ', '-')}/1",
        "title": title,
        "opportunity_type": "rfp",
        "call_to_action": "Submit a proposal",
        "confidence": 0.91,
        "one_line_reason": "Test item",
    }
    if post_ts is not None:
        item["post_ts"] = post_ts
    if detected_ts is not None:
        item["detected_ts"] = detected_ts
    if ingest_mode:
        item["ingest_mode"] = ingest_mode
    return item


class RenderDashboardTests(unittest.TestCase):
    def test_render_sorts_by_post_timestamp_and_marks_historical_items(self) -> None:
        now = datetime.now(timezone.utc)
        older_live = make_item(
            "Older Live",
            post_ts=iso(now - timedelta(days=20)),
            detected_ts=iso(now - timedelta(days=1)),
            ingest_mode="live",
        )
        newer_historical = make_item(
            "Newer Historical",
            post_ts=iso(now - timedelta(days=2)),
            detected_ts=iso(now),
            ingest_mode="backfill",
        )

        html = render_dashboard.render([older_live, newer_historical], [{"name": "Aave"}])

        self.assertLess(html.index("Newer Historical"), html.index("Older Live"))
        self.assertIn("Historical</span>", html)
        self.assertIn("Posted ", html)
        self.assertNotIn("Captured ", html)

    def test_render_accepts_legacy_timestamp_only_records(self) -> None:
        now = datetime.now(timezone.utc)
        legacy = make_item("Legacy Record", detected_ts=None)
        legacy["ts"] = iso(now - timedelta(days=1))
        legacy.pop("ingest_mode", None)

        html = render_dashboard.render([legacy], [{"name": "Aave"}])

        self.assertIn("Legacy Record", html)
        self.assertIn("Posted ", html)


if __name__ == "__main__":
    unittest.main()
