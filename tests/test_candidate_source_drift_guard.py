"""Fail closed if a synthetic source changes after a reconstruction plan is approved."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters import PokemonEssentialsAdapter
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    ReconstructionError,
    build_v21_1_map_validation_plan,
    build_v21_1_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


class CandidateSourceDriftGuardTests(unittest.TestCase):
    def _make_plan(self, base: Path, kind: str):
        game = base / "synthetic_game"
        prepare_v21_game(
            game,
            map_validation=kind == "map",
            nested_message_bank=kind == "bank",
            internal_line_control=kind == "map",
        )
        extraction = PokemonEssentialsAdapter().extract_with_provenance(game)
        self.assertFalse(extraction.errors)
        rows = [dict(row) for row in extraction.rows]
        if kind == "map":
            chosen = next(row for row in rows if row["type"] == "Dialogue")
            chosen["traduction_fr"] = chosen["texte_source"] + " [FR TEST]"
            chosen["statut"] = "Accepté"
            choice = next(row for row in rows if row["type"] == "Choix")
            choice["traduction_fr"] = choice["texte_source"] + " [FR TEST]"
            choice["statut"] = "Accepté"
        else:
            chosen = next(
                row for row in rows
                if row["type"] == "Banque de messages"
                and row["fichier"] == "Data/messages_game.dat"
                and not row["traduction_fr"]
            )
            chosen["traduction_fr"] = chosen["texte_source"] + " [FR TEST]"
            chosen["statut"] = "Accepté"
        csv_path = base / "project" / "textes_structures.csv"
        write_extracted_project_csv(csv_path, rows)
        finalize_verified_essentials_project(
            game, csv_path,
            adapter_profile="essentials_v21_1_readonly",
            declared_version="21.1",
        )
        plan = (
            build_v21_1_map_validation_plan(game, csv_path)
            if kind == "map"
            else build_v21_1_validation_plan(game, csv_path)
        )
        simulate_plan(plan)
        return game, plan

    def _test_drift(self, kind: str):
        with tempfile.TemporaryDirectory(prefix="pft_candidate_source_drift_") as tmp:
            base = Path(tmp)
            game, plan = self._make_plan(base, kind)
            relative = (
                "Data/Map001.rxdata" if kind == "map"
                else "Data/messages_game.dat"
            )
            path = game / relative
            original = path.read_bytes()
            # Synthetic fixture corruption occurs after approval; no game originals.
            path.write_bytes(original + b"\x00")
            candidate = base / "candidate"
            with self.assertRaises(ReconstructionError):
                reconstruct_copy(plan, candidate, base / "reports")
            self.assertFalse(candidate.exists())
            self.assertEqual(original + b"\x00", path.read_bytes())

    def test_map_source_change_after_plan_refused(self):
        self._test_drift("map")

    def test_message_bank_source_change_after_plan_refused(self):
        self._test_drift("bank")


if __name__ == "__main__":
    unittest.main()
