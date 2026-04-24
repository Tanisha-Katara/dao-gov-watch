from __future__ import annotations

import unittest

import classifier


class ClassifierPromptTests(unittest.TestCase):
    def test_prompt_rejects_grants_and_self_promo(self) -> None:
        prompt = classifier.SYSTEM_PROMPT.lower()

        self.assertIn("grant programs", prompt)
        self.assertIn("advertising their own services", prompt)
        self.assertIn("the dao must be the one asking", prompt)

    def test_few_shots_include_rejecting_examples_for_common_noise(self) -> None:
        examples = "\n".join(user_text for user_text, _ in classifier.FEW_SHOTS)
        outputs = [classification for _, classification in classifier.FEW_SHOTS]

        self.assertIn("Research Grants", examples)
        self.assertTrue(any(not output.is_opportunity for output in outputs))
        self.assertTrue(any("pitching" in output.one_line_reason.lower() for output in outputs))


if __name__ == "__main__":
    unittest.main()
