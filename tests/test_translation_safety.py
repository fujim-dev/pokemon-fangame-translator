from __future__ import annotations

import unittest
from pathlib import Path

from translation_studio import (
    install_argos_english_french_model,
    reconciled_status,
    restore_simple_commands,
    review_translation,
    translate_preserving_codes,
)


class _UppercaseTranslator:
    def translate(self, text: str) -> str:
        return text.upper()


class _FakeArgosPackage:
    def __init__(self, version: str):
        self.from_code = "en"
        self.to_code = "fr"
        self.package_version = version

    def download(self) -> str:
        return f"model-{self.package_version}.argosmodel"


class _FakeArgosModule:
    def __init__(self):
        self.updated = False
        self.installed_path = ""
        self.packages = [
            _FakeArgosPackage("1.9"),
            _FakeArgosPackage("1.10"),
        ]

    def update_package_index(self) -> None:
        self.updated = True

    def get_available_packages(self):
        return list(self.packages)

    def install_from_path(self, path: str) -> None:
        self.installed_path = path


class TranslationSafetyTests(unittest.TestCase):
    def test_application_does_not_run_pip_to_prepare_argos(self) -> None:
        source = Path(__file__).resolve().parent.parent / "translation_studio.py"
        content = source.read_text(encoding="utf-8-sig")

        self.assertNotIn('"-m", "pip"', content)

    def test_installs_latest_available_argos_model_without_pip(self) -> None:
        package_module = _FakeArgosModule()

        model = install_argos_english_french_model(package_module)

        self.assertTrue(package_module.updated)
        self.assertEqual("1.10", model.package_version)
        self.assertEqual("model-1.10.argosmodel", package_module.installed_path)

    def test_accepted_translation_becomes_blocked_if_commands_are_invalid(self) -> None:
        self.assertEqual("Bloqué", reconciled_status("Accepté", "bloque"))
        self.assertEqual("Accepté", reconciled_status("Accepté", "verifier"))
        self.assertEqual("Ignoré", reconciled_status("Ignoré", "bloque"))

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
