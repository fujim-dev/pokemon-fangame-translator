"""UI regression: incomplete runtime bank disables certified extraction."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from adapters import DetectionResult, GameCapability
from analysis.models import AnalysisIssue
from Pokemon_Fangame_Translator import FangameTranslatorApp


class FakeButton:
    def __init__(self):
        self.manager = "pack"
        self.enabled = False

    def winfo_manager(self):
        return self.manager

    def pack(self, **kwargs):
        self.manager = "pack"

    def pack_forget(self):
        self.manager = ""

    def set_enabled(self, value):
        self.enabled = bool(value)


class FakeMenu:
    def __init__(self):
        self.states = {}

    def entryconfigure(self, label, *, state):
        self.states[label] = state


def make_app(blocking: bool):
    app = object.__new__(FangameTranslatorApp)
    app.extract_btn = FakeButton()
    app.translate_btn = FakeButton()
    app.reconstruction_btn = FakeButton()
    app.deep_analyze_btn = FakeButton()
    app.file_menu = FakeMenu()
    app.translation_csv_path = None
    app.last_diagnostic = Mock()
    app.game_dir = Path("synthetic-game")
    app.detection_result = DetectionResult(
        adapter_id="pokemon_essentials",
        display_name="Pokémon Essentials v21.1",
        confidence=100,
        capabilities=frozenset({
            GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE,
            GameCapability.EXTRACT, GameCapability.TRANSLATE,
        }),
        adapter_recognized=True,
        write_actions_allowed=False,
    )
    app.last_deep_analysis = SimpleNamespace(
        issues=[AnalysisIssue(
            code="ability_runtime_incomplete",
            severity="error",
            category="integrity",
            message="Banques Ability incomplètes (fixture synthétique).",
            blocking=blocking,
        )] if blocking else []
    )
    return app


class UiRuntimeBlockerTests(unittest.TestCase):
    def test_incomplete_bank_disables_extraction_button(self):
        app = make_app(True)
        app._refresh_action_buttons()
        self.assertFalse(app.extract_btn.enabled)
        self.assertTrue(app.deep_analyze_btn.enabled)
        self.assertTrue(app._certified_extraction_blocker())

    def test_incomplete_bank_blocks_direct_extraction_before_writes(self):
        app = make_app(True)
        with patch("Pokemon_Fangame_Translator.messagebox.showerror") as error:
            app.extract_texts()
        error.assert_called_once()
        self.assertIn("Ability", error.call_args.args[1])

    def test_incomplete_bank_disables_translation_button_and_menu(self):
        app = make_app(True)
        with unittest.mock.patch("pathlib.Path.is_file", return_value=True):
            app.translation_csv_path = Path("synthetic-project.csv")
            app._refresh_action_buttons()
        self.assertFalse(app.translate_btn.enabled)
        self.assertEqual(
            "disabled",
            app.file_menu.states["Ouvrir le studio de traduction"],
        )
        self.assertTrue(app.deep_analyze_btn.enabled)

    def test_incomplete_bank_blocks_opening_translation_studio(self):
        app = make_app(True)
        with patch("Pokemon_Fangame_Translator.messagebox.showerror") as error:
            app.open_translation_studio()
        error.assert_called_once()
        self.assertIn("Ability", error.call_args.args[1])

    def test_valid_profile_retains_studio_action(self):
        app = make_app(False)
        with unittest.mock.patch("pathlib.Path.is_file", return_value=True):
            app.translation_csv_path = Path("synthetic-project.csv")
            app._refresh_action_buttons()
        self.assertTrue(app.translate_btn.enabled)
        self.assertEqual(
            "normal",
            app.file_menu.states["Ouvrir le studio de traduction"],
        )

    def test_without_incomplete_bank_extraction_remains_capability_controlled(self):
        app = make_app(False)
        app._refresh_action_buttons()
        self.assertTrue(app.extract_btn.enabled)
        self.assertFalse(app._certified_extraction_blocker())


if __name__ == "__main__":
    unittest.main()
