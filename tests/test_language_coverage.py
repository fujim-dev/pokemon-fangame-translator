from __future__ import annotations

import unittest

from analysis.language_coverage import calculate_coverage, classify_text


class LanguageCoverageTests(unittest.TestCase):
    def test_clear_french_sentence(self) -> None:
        result = classify_text(
            "Bonjour, je suis heureux de vous rencontrer dans ce village."
        )
        self.assertEqual("francais_probable", result.category)

    def test_clear_english_sentence(self) -> None:
        result = classify_text(
            "Hello, I am happy to meet you in this village."
        )
        self.assertEqual("anglais_probable", result.category)

    def test_significant_french_and_english_segments_are_mixed(self) -> None:
        result = classify_text(
            "Bonjour, welcome to this village avec tes amis."
        )
        self.assertEqual("mixte", result.category)

    def test_short_proper_name_stays_ambiguous(self) -> None:
        self.assertEqual("ambigu", classify_text("Pikachu").category)

    def test_technical_commands_alone_are_excluded(self) -> None:
        commands_only = r"\c[1]\PN"
        self.assertEqual("technique_exclu", classify_text(commands_only).category)
        metrics = calculate_coverage([commands_only])
        self.assertEqual(1, metrics.protected_command_lines)

    def test_incomplete_sources_prevent_a_global_full_coverage_claim(self) -> None:
        texts = ["Bonjour, je suis heureux de vous rencontrer dans ce village."]

        first = calculate_coverage(texts, incomplete_sources=True)
        second = calculate_coverage(texts, incomplete_sources=True)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(100.0, first.french_line_percent)
        self.assertFalse(first.can_claim_complete_coverage)


if __name__ == "__main__":
    unittest.main()
