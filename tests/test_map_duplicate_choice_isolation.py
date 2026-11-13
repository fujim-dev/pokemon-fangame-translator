"""Synthetic map: identical choice text must preserve distinct command indexes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters import PokemonEssentialsAdapter
from analysis.integrity import compare_snapshots, snapshot_tree
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    build_v21_1_map_validation_plan, reconstruct_copy, simulate_plan,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game, ruby_text, write_extracted_project_csv
from structured_extractor import extract_map


class MapDuplicateChoiceIsolationTests(unittest.TestCase):
    def test_duplicate_options_translate_only_selected_branch(self):
        with tempfile.TemporaryDirectory(prefix="pft_duplicate_choice_") as tmp:
            base=Path(tmp)
            game=base/"game"
            prepare_v21_game(game,map_validation=True)
            map_path=game/"Data/Map001.rxdata"
            original=load(map_path)
            commands=original.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
            duplicate="Same synthetic option"
            commands[2].ivars["@parameters"][0][0]=ruby_text(duplicate)
            commands[2].ivars["@parameters"][0][1]=ruby_text(duplicate)
            commands[3].ivars["@parameters"][1]=ruby_text(duplicate)
            commands[5].ivars["@parameters"][1]=ruby_text(duplicate)
            map_path.write_bytes(dumps(original))
            before=snapshot_tree(game)

            extracted=PokemonEssentialsAdapter().extract_with_provenance(game)
            self.assertFalse(extracted.errors)
            rows=[dict(row) for row in extracted.rows]
            options=[
                row for row in rows
                if row["type"]=="Choix"
                and row["fichier"]=="Data/Map001.rxdata"
                and row["texte_source"]==duplicate
            ]
            self.assertEqual(2,len(options))
            self.assertEqual(2,len({row["id_stable"] for row in options}))
            chosen=next(row for row in options if row["sous_index"]==0)
            other=next(row for row in options if row["sous_index"]==1)
            dialogue=next(row for row in rows if row["type"]=="Dialogue" and row["fichier"]=="Data/Map001.rxdata")
            dialogue["traduction_fr"]=dialogue["texte_source"]+" [FR]"
            dialogue["statut"]="Accepté"
            chosen["traduction_fr"]="Première option uniquement"
            chosen["statut"]="Accepté"
            csv=base/"project"/"textes_structures.csv"
            write_extracted_project_csv(csv,rows)
            finalize_verified_essentials_project(
                game,csv,adapter_profile="essentials_v21_1_readonly",
                declared_version="21.1",
            )
            plan=build_v21_1_map_validation_plan(game,csv)
            self.assertEqual(2,plan.counts()["applicable"])
            simulate_plan(plan)
            candidate=base/"candidate"
            result=reconstruct_copy(plan,candidate,base/"reports")
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(before,snapshot_tree(game)).passed)
            after=load(candidate/"Data/Map001.rxdata")
            modified=after.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
            self.assertEqual("Première option uniquement",modified[2].ivars["@parameters"][0][0].text())
            self.assertEqual("Première option uniquement",modified[3].ivars["@parameters"][1].text())
            self.assertEqual(duplicate,modified[2].ivars["@parameters"][0][1].text())
            self.assertEqual(duplicate,modified[5].ivars["@parameters"][1].text())
            again={r["id_stable"]:r["texte_source"] for r in extract_map(candidate/"Data/Map001.rxdata","Data/Map001.rxdata","Synthetic intro",strict=True)}
            self.assertEqual(chosen["traduction_fr"],again[chosen["id_stable"]])
            self.assertEqual(duplicate,again[other["id_stable"]])


if __name__=="__main__":
    unittest.main()
