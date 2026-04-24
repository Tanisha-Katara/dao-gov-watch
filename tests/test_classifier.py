from __future__ import annotations

import unittest

import classifier


class ClassifierPromptTests(unittest.TestCase):
    def test_prompt_splits_grants_and_rejects_self_promo(self) -> None:
        prompt = classifier.SYSTEM_PROMPT.lower()

        # Grant distinction: programs accepted, requests rejected.
        self.assertIn("grant programs", prompt)
        self.assertIn("grant requests", prompt)
        self.assertIn("advertising their own services", prompt)
        self.assertIn("the dao must be the one asking", prompt)

    def test_few_shots_cover_grant_split_and_rejecting_noise(self) -> None:
        examples = "\n".join(user_text for user_text, _ in classifier.FEW_SHOTS)
        outputs = [classification for _, classification in classifier.FEW_SHOTS]

        # Uniswap research grants is now an ACCEPTED grant program.
        self.assertIn("Research Grants", examples)
        grant_accepts = [
            o for o in outputs if o.opportunity_type == "grant" and o.is_opportunity
        ]
        self.assertTrue(grant_accepts, "expected at least one grant program accept")

        # At least one rejecting example exists for common noise.
        self.assertTrue(any(not output.is_opportunity for output in outputs))
        # Self-promo rejection is still represented.
        self.assertTrue(any("pitching" in output.one_line_reason.lower() for output in outputs))


if __name__ == "__main__":
    unittest.main()
