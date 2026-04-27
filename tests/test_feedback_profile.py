from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import feedback_profile


class FeedbackProfileTests(unittest.TestCase):
    def test_load_feedback_entries_reads_repo_store_shape(self) -> None:
        payload = {
            "version": 1,
            "updated_at": "2026-04-27T00:00:00Z",
            "items": {
                "https://example.com/post/1": {
                    "label": "done",
                    "dao": "ENS",
                    "opportunity_type": "hire",
                    "title": "Governance Advisor Search",
                    "call_to_action": "Reply in thread",
                    "one_line_reason": "Relevant role",
                    "updated_at": "2026-04-27T00:00:00Z",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.json"
            path.write_text(json.dumps(payload))
            entries = feedback_profile.load_feedback_entries(path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["dao"], "ENS")
        self.assertEqual(entries[0]["label"], "done")

    def test_profile_scores_positive_and_negative_examples(self) -> None:
        entries = [
            {
                "post_url": "https://example.com/ens",
                "label": "done",
                "dao": "ENS",
                "opportunity_type": "hire",
                "title": "Governance Advisor Search",
                "call_to_action": "Seeking a governance advisor",
                "one_line_reason": "Relevant advisory role",
                "updated_at": "2026-04-27T00:00:00Z",
            },
            {
                "post_url": "https://example.com/grant",
                "label": "not_relevant",
                "dao": "Aave",
                "opportunity_type": "grant",
                "title": "Governance Research Grants",
                "call_to_action": "Apply via form",
                "one_line_reason": "Not relevant",
                "updated_at": "2026-04-27T00:01:00Z",
            },
        ]
        profile = feedback_profile.build_feedback_profile(entries)

        positive = feedback_profile.score_feedback(
            profile,
            dao_name="ENS",
            opportunity_type="hire",
            text="Seeking governance advisor support",
        )
        negative = feedback_profile.score_feedback(
            profile,
            dao_name="Aave",
            opportunity_type="grant",
            text="Governance research grant round",
        )

        self.assertGreater(positive, 0.0)
        self.assertLess(negative, 0.0)

    def test_preference_note_mentions_positive_and_negative_patterns(self) -> None:
        entries = [
            {
                "post_url": "https://example.com/ens",
                "label": "done",
                "dao": "ENS",
                "opportunity_type": "hire",
                "title": "Governance Advisor Search",
                "call_to_action": "Seeking a governance advisor",
                "one_line_reason": "Relevant advisory role",
                "updated_at": "2026-04-27T00:00:00Z",
            },
            {
                "post_url": "https://example.com/aave",
                "label": "not_relevant",
                "dao": "Aave",
                "opportunity_type": "grant",
                "title": "Governance Research Grants",
                "call_to_action": "Apply via form",
                "one_line_reason": "Not relevant",
                "updated_at": "2026-04-27T00:01:00Z",
            },
        ]
        profile = feedback_profile.build_feedback_profile(entries)
        note = feedback_profile.build_preference_note(profile)

        self.assertIn("Positive labels", note)
        self.assertIn("Negative labels", note)
        self.assertIn("ENS", note)


if __name__ == "__main__":
    unittest.main()
