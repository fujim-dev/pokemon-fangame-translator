"""Synthetic map: equal dialogue text in separate events must not cross-update."""
from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from adapters import PokemonEssentialsAdapter
from analysis.integrity import compare_snapshots, snapshot_tree
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    build_v21_1_map_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from structured_extractor import extract_map
from test_essentials_v21 import prepare_v21_game, ruby_text, write_extracted_project_csv


class MapDuplicateDialogueEventsTests(unittest.TestCase):
    def test_same_dialogue_in_two_events_updates_selected_event_only(self):
        with tempfile.TemporaryDirectory(prefix="pft_duplicate_event_dialogue_") as temp:
            base = Path(temp)
            game = base / "game"
            prepare_v21_game(game, map_validation=True)
            map_path = game / "Data" / "Map001.rxdata"
            map_obj = load(map_path)
            events = map_obj.ivars["@events"]
            first = events[1]
            second = deepcopy(first)
            second.ivars["@id"] = 2
            second.ivars["@name"] = ruby_text("Synthetic second event")
            second.ivars["@x"] = 5
            events[2] = second
            second_before = dumps(second)
            map_path.write_bytes(dumps(map_obj))
            source_before = snapshot_tree(game)

            extraction = PokemonEssentialsAdapter().extract_with_provenance(game)
            self.assertFalse(extraction.errors)
            rows = [dict(r) for r in extraction.rows]
            dialogues = [
                r for r in rows
                if r["type"] == "Dialogue"
                and r["fichier"] == "Data/Map001.rxdata"
                and r["texte_source"] == "Synthetic map dialogue.\\nSecond synthetic line."
            ]
            self.assertEqual(2, len(dialogues))
            self.assertEqual(2, len({r["id_stable"] for r in dialogues}))
            first_dialogue = next(r for r in dialogues if str(r["evenement_id"]) == "1")
            second_dialogue = next(r for r in dialogues if str(r["evenement_id"]) == "2")
            first_dialogue["traduction_fr"] = first_dialogue["texte_source"] + " [FR]"
            first_dialogue["statut"] = "Accepté"
            choice = next(
                r for r in rows
                if r["type"] == "Choix"
                and str(r["evenement_id"]) == "1"
                and r["sous_index"] == 0
            )
            choice["traduction_fr"] = "Première option traduite"
            choice["statut"] = "Accepté"

            csv = base / "project" / "textes_structures.csv"
            write_extracted_project_csv(csv, rows)
            finalize_verified_essentials_project(
                game, csv,
                adapter_profile="essentials_v21_1_readonly",
                declared_version="21.1",
            )
            plan = build_v21_1_map_validation_plan(game, csv)
            self.assertEqual(2, plan.counts()["applicable"])
            simulate_plan(plan)
            candidate = base / "candidate"
            result = reconstruct_copy(plan, candidate, base / "reports")
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(game)).passed)
            new_map = load(candidate / "Data" / "Map001.rxdata")
            new_events = new_map.ivars["@events"]
            self.assertEqual(second_before, dumps(new_events[2]))
            after_rows = {
                r["id_stable"]: r["texte_source"]
                for r in extract_map(
                    candidate / "Data" / "Map001.rxdata",
                    "Data/Map001.rxdata",
                    "Synthetic intro",
                    strict=True,
                )
            }
            self.assertEqual(
                first_dialogue["traduction_fr"],
                after_rows[first_dialogue["id_stable"]],
            )
            self.assertEqual(
                second_dialogue["texte_source"],
                after_rows[second_dialogue["id_stable"]],
            )


if __name__ == "__main__":
    unittest.main()
