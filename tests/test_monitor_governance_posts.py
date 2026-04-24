from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import monitor_governance_posts as monitor


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_post(post_id: int, created_at: datetime) -> dict:
    return {
        "id": post_id,
        "created_at": iso(created_at),
        "topic_title": f"Post {post_id}",
        "cooked": "We need governance research help.",
        "topic_slug": f"post-{post_id}",
        "topic_id": post_id,
        "post_number": 1,
    }


class FetchRecentPostsTests(unittest.TestCase):
    def test_fetch_recent_posts_handles_empty_pages_and_cutoff(self) -> None:
        cutoff = datetime(2026, 4, 1, tzinfo=timezone.utc)
        page_one = [
            make_post(300, datetime(2026, 4, 24, tzinfo=timezone.utc)),
            make_post(299, datetime(2026, 4, 20, tzinfo=timezone.utc)),
        ]
        page_two = [
            make_post(248, datetime(2026, 4, 8, tzinfo=timezone.utc)),
            make_post(200, datetime(2026, 3, 15, tzinfo=timezone.utc)),
        ]

        with patch.object(monitor, "fetch_posts_page", side_effect=[page_one, [], page_two]):
            posts, latest_visible_id = monitor.fetch_recent_posts("https://example.org", cutoff)

        self.assertEqual([monitor.post_id(post) for post in posts], [248, 299, 300])
        self.assertEqual(latest_visible_id, 300)


class OpportunityMergeTests(unittest.TestCase):
    def test_merge_prefers_real_post_timestamp_and_preserves_first_detection(self) -> None:
        existing = monitor.normalize_opportunity(
            {
                "dao": "Aave",
                "forum_url": "https://governance.aave.com",
                "post_url": "https://governance.aave.com/t/example/1",
                "title": "Legacy record",
                "ts": "2026-04-20T00:00:00Z",
                "opportunity_type": "other",
                "call_to_action": "Reply in thread",
                "confidence": 0.75,
                "one_line_reason": "Legacy item",
            }
        )
        incoming = {
            "dao": "Aave",
            "forum_url": "https://governance.aave.com",
            "post_url": "https://governance.aave.com/t/example/1",
            "title": "Historical record",
            "post_ts": "2026-04-02T00:00:00Z",
            "detected_ts": "2026-04-24T00:00:00Z",
            "ingest_mode": monitor.BACKFILL_MODE,
            "opportunity_type": "rfp",
            "call_to_action": "Submit a proposal",
            "confidence": 0.91,
            "one_line_reason": "Historical import",
        }

        merged = monitor.merge_opportunities(existing, incoming)

        self.assertEqual(merged["post_ts"], "2026-04-02T00:00:00Z")
        self.assertEqual(merged["detected_ts"], "2026-04-20T00:00:00Z")
        self.assertEqual(merged["ingest_mode"], monitor.LIVE_MODE)
        self.assertEqual(merged["title"], "Historical record")
        self.assertNotIn("ts", merged)


class FilteringRuleTests(unittest.TestCase):
    def test_detects_service_provider_self_promo(self) -> None:
        text = (
            "I offer governance and tokenomics reviews for DAO proposals. "
            "DM me if your delegates need support."
        )

        self.assertTrue(monitor.looks_like_service_provider_pitch(text))

    def test_does_not_flag_protocol_originated_request(self) -> None:
        text = (
            "We are seeking an external tokenomics advisor. "
            "Reply in thread with relevant prior work."
        )

        self.assertFalse(monitor.looks_like_service_provider_pitch(text))


if __name__ == "__main__":
    unittest.main()
