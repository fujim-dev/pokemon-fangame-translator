from __future__ import annotations

import unittest

from translation_studio import (
    restore_simple_commands,
    review_translation,
    translate_preserving_codes,
)


class _UppercaseTranslator:
    def translate(self, text: str) -> str:
        return text.upper()


class TranslationSafetyTests(unittest.TestCase):
    def test_missing_protected_command_blocks_translation(self) -> None:
        level, alerts = review_translation(r"Hello \c[1]world", "Bonjour le monde")

        self.assertEqual("bloque", level)
        self.assertIn("Commande du jeu modifiée", alerts)

    def test_changed_command_order_blocks_translation(self) -> None:
        level, _alerts = review_translation(
            r"\c[1]Hello\c[0]",
            r"\c[0]Bonjour\c[1]",
        )

        self.assertEqual("bloque", level)

    def test_translation_preserves_commands(self) -> None:
        translated = translate_preserving_codes(
            _UppercaseTranslator(),
            r"Hello \c[1]trainer\c[0]!",
            glossary=[],
        )

        self.assertEqual(r"HELLO \c[1]TRAINER\c[0]!", translated)

    def test_restores_unambiguous_leading_command(self) -> None:
        repaired, _actions, success = restore_simple_commands(
            r"\c[1]Hello",
            "Bonjour",
        )

        self.assertTrue(success)
        self.assertEqual(r"\c[1]Bonjour", repaired)


if __name__ == "__main__":
    unittest.main()
