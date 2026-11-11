"""Read-only Ability runtime diagnostics must never imply certified reconstruction."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analysis.ability_runtime_diagnostic import inspect_ability_runtime_banks
from analysis.deep_analyzer import analyze_game
from essentials_ability import ABILITY_MESSAGES_FILE
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game


class AbilityReadOnlyDiagnosticTests(unittest.TestCase):
    def test_complete_synthetic_fixture_is_not_flagged(self):
        with tempfile.TemporaryDirectory(prefix="pft_ability_complete_diag_") as tmp:
            root = Path(tmp) / "game"
            prepare_v21_game(root, ability_validation=True)
            report = inspect_ability_runtime_banks(root)
            self.assertTrue(report["complete"])
            self.assertEqual(3, report["sections"])
            analysis = analyze_game(
                root, adapter_id="pokemon_essentials",
                adapter_display_name="Pokémon Essentials", adapter_confidence=100,
            )
            self.assertNotIn("ability_runtime_incomplete", [x.code for x in analysis.issues])

    def test_incomplete_prefix_is_reported_blocking_without_game_text(self):
        with tempfile.TemporaryDirectory(prefix="pft_ability_incomplete_diag_") as tmp:
            root = Path(tmp) / "game"
            prepare_v21_game(root, ability_validation=True)
            path = root / ABILITY_MESSAGES_FILE
            runtime = load(path)
            runtime[10].pop(next(reversed(runtime[10])))
            path.write_bytes(dumps(runtime))
            report = inspect_ability_runtime_banks(root)
            self.assertFalse(report["complete"])
            names = report["fields"]["Name"]
            self.assertTrue(names["exact_prefix"])
            self.assertEqual(1, names["missing_unique"])
            analysis = analyze_game(
                root, adapter_id="pokemon_essentials",
                adapter_display_name="Pokémon Essentials", adapter_confidence=100,
            )
            flagged = [issue for issue in analysis.issues if issue.code == "ability_runtime_incomplete"]
            self.assertEqual(1, len(flagged))
            self.assertTrue(flagged[0].blocking)
            self.assertEqual("rouge", analysis.status)
            self.assertNotIn("Synthetic", flagged[0].message)

    def test_missing_files_do_not_trigger_ability_diagnosis(self):
        with tempfile.TemporaryDirectory(prefix="pft_ability_missing_diag_") as tmp:
            root = Path(tmp) / "game"
            root.mkdir()
            self.assertIsNone(inspect_ability_runtime_banks(root))

    def test_read_only_diagnostic_does_not_change_sources(self):
        with tempfile.TemporaryDirectory(prefix="pft_ability_readonly_diag_") as tmp:
            root = Path(tmp) / "game"
            prepare_v21_game(root, ability_validation=True)
            files = [root / "PBS/abilities.txt", root / "Data/abilities.dat", root / ABILITY_MESSAGES_FILE]
            original = [path.read_bytes() for path in files]
            inspect_ability_runtime_banks(root)
            self.assertEqual(original, [path.read_bytes() for path in files])


if __name__ == "__main__":
    unittest.main()
