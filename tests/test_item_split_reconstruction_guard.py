"""Fail-closed reconstruction tests for split Item provenance.

Synthetic data only: two PBS sources, two compiled message banks.
Never executes user games or enables reconstruction of readonly proofs.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters import PokemonEssentialsAdapter
from analysis.integrity import compare_snapshots, snapshot_tree
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    ReconstructionError,
    build_v21_1_item_description_validation_plan,
)
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv
from test_item_split_readonly import synthetic_sources


class ItemSplitReconstructionGuardTests(unittest.TestCase):
    def _assert_split_source_is_not_reconstructible(self, item_file: str) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_item_split_guard_") as temporary:
            base = Path(temporary)
            source_root = base / "synthetic_game"
            prepare_v21_game(source_root, item_validation=True)
            for relative, payload in zip(
                (
                    "PBS/items.txt", "PBS/items_raid_bait.txt",
                    "Data/items.dat", "Data/messages_core.dat",
                    "Data/messages_game.dat",
                ), synthetic_sources()
            ):
                target = source_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            original = snapshot_tree(source_root)
            extraction = PokemonEssentialsAdapter().extract_with_provenance(source_root)
            self.assertFalse(extraction.errors)
            rows = [dict(row) for row in extraction.rows]
            candidates = [
                row for row in rows
                if row.get("fichier") == item_file
                and row.get("commande") == "Description"
            ]
            self.assertTrue(candidates)
            chosen = candidates[0]
            metadata = json.loads(chosen["pbs_runtime_structure"])
            self.assertFalse(metadata["reconstruction_allowed"])
            self.assertEqual(
                "pft_v21_1_items_multi_pbs_split_readonly_v1",
                metadata["format"],
            )
            chosen["traduction_fr"] = "Description française synthétique."
            chosen["statut"] = "Accepté"
            csv_path = base / "project" / "textes_structures.csv"
            write_extracted_project_csv(csv_path, rows)
            finalize_verified_essentials_project(
                source_root, csv_path,
                adapter_profile="essentials_v21_1_readonly",
                declared_version="21.1",
            )

            with self.assertRaises(ReconstructionError):
                build_v21_1_item_description_validation_plan(source_root, csv_path)
            self.assertTrue(compare_snapshots(original, snapshot_tree(source_root)).passed)
            self.assertFalse((base / "candidate").exists())

    def test_supplemental_pbs_reconstruction_refused(self) -> None:
        self._assert_split_source_is_not_reconstructible("PBS/items_raid_bait.txt")

    def test_main_pbs_reconstruction_refused(self) -> None:
        self._assert_split_source_is_not_reconstructible("PBS/items.txt")


if __name__ == "__main__":
    unittest.main()
