from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters import DetectionResult, GameCapability, UnknownAdapter
from Pokemon_Fangame_Translator import FangameTranslatorApp


class FakeButton:
    def __init__(self) -> None:
        self.manager = "pack"
        self.enabled = False

    def winfo_manager(self) -> str:
        return self.manager

    def pack(self, **_options) -> None:
        self.manager = "pack"

    def pack_forget(self) -> None:
        self.manager = ""

    def set_enabled(self, enabled) -> None:
        self.enabled = bool(enabled)


class FakeMenu:
    def __init__(self) -> None:
        self.state = "disabled"

    def entryconfigure(self, _label: str, *, state: str) -> None:
        self.state = state


class AdapterUiCapabilityTests(unittest.TestCase):
    @staticmethod
    def _app_without_tk() -> FangameTranslatorApp:
        app = object.__new__(FangameTranslatorApp)
        app.extract_btn = FakeButton()
        app.translate_btn = FakeButton()
        app.reconstruction_btn = FakeButton()
        app.file_menu = FakeMenu()
        app.translation_csv_path = None
        return app

    def test_unknown_profile_hides_incompatible_actions(self) -> None:
        app = self._app_without_tk()
        app.detection_result = UnknownAdapter().probe(Path("."))

        app._refresh_action_buttons()

        self.assertEqual("", app.extract_btn.manager)
        self.assertEqual("", app.translate_btn.manager)
        self.assertEqual("", app.reconstruction_btn.manager)
        self.assertFalse(app.extract_btn.enabled)
        self.assertEqual("disabled", app.file_menu.state)

    def test_supported_profile_shows_only_ready_actions(self) -> None:
        app = self._app_without_tk()
        app.detection_result = DetectionResult(
            adapter_id="pokemon_essentials",
            display_name="Pokémon Essentials classique",
            confidence=90,
            capabilities=frozenset(GameCapability),
            write_actions_allowed=True,
        )
        app.extract_btn.manager = ""
        app.translate_btn.manager = ""
        app.reconstruction_btn.manager = ""

        with tempfile.TemporaryDirectory(prefix="pft_test_ui_caps_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            csv_path.write_text("synthetic", encoding="utf-8")
            app.translation_csv_path = csv_path

            app._refresh_action_buttons()

        self.assertEqual("pack", app.extract_btn.manager)
        self.assertEqual("pack", app.translate_btn.manager)
        self.assertEqual("pack", app.reconstruction_btn.manager)
        self.assertTrue(app.extract_btn.enabled)
        self.assertTrue(app.translate_btn.enabled)
        self.assertTrue(app.reconstruction_btn.enabled)
        self.assertEqual("normal", app.file_menu.state)


if __name__ == "__main__":
    unittest.main()
