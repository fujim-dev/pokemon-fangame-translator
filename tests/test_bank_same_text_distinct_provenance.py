"""Synthetic cross-bank same-text collision: physical provenance must stay distinct."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters import PokemonEssentialsAdapter
from analysis.integrity import compare_snapshots, snapshot_tree
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    V21_1_BANK_CORPUS_VALIDATION_SCOPE,
    build_v21_1_bank_corpus_validation_plan, reconstruct_copy, simulate_plan,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game, ruby_text, write_extracted_project_csv
from structured_extractor import extract_message_bank


class BankSameTextDistinctProvenanceTests(unittest.TestCase):
    def test_same_source_text_in_core_and_game_translates_independently(self):
        with tempfile.TemporaryDirectory(prefix="pft_crossbank_collision_") as temp:
            base=Path(temp)
            game=base/"game"
            prepare_v21_game(game,bank_corpus=True)
            core_path=game/"Data/messages_core.dat"
            game_path=game/"Data/messages_game.dat"
            core=load(core_path)
            game_data=load(game_path)
            shared="Shared synthetic text in different physical banks"
            core_mapping=core[1]
            game_mapping=game_data[1]
            core_key=next(key for key in core_mapping if key.text()=="Direct synthetic core bank text")
            game_key=next(key for key in game_mapping if key.text()=="Direct synthetic game bank text")
            core_mapping.pop(core_key)
            game_mapping.pop(game_key)
            core_mapping[ruby_text(shared)]=ruby_text(shared)
            game_mapping[ruby_text(shared)]=ruby_text(shared)
            core_path.write_bytes(dumps(core))
            game_path.write_bytes(dumps(game_data))
            source_before=snapshot_tree(game)

            extracted=PokemonEssentialsAdapter().extract_with_provenance(game)
            self.assertFalse(extracted.errors)
            rows=[dict(row) for row in extracted.rows]
            duplicate=[row for row in rows if row["type"]=="Banque de messages" and row["texte_source"]==shared]
            self.assertEqual(2,len(duplicate))
            self.assertEqual(
                {"Data/messages_core.dat","Data/messages_game.dat"},
                {row["fichier"] for row in duplicate},
            )
            self.assertEqual(2,len({row["id_stable"] for row in duplicate}))

            chosen_nested=next(row for row in rows if row["type"]=="Banque de messages" and row["texte_source"]=="Nested synthetic game bank text")
            selected=[*duplicate,chosen_nested]
            for row in selected:
                row["traduction_fr"]= (
                    "Version CORE" if row["fichier"]=="Data/messages_core.dat"
                    else "Version GAME DIRECT" if row["texte_source"]==shared
                    else "Version GAME NESTED"
                )
                row["statut"]="Accepté"
            csv=base/"project"/"textes_structures.csv"
            write_extracted_project_csv(csv,rows)
            finalize_verified_essentials_project(
                game,csv,adapter_profile="essentials_v21_1_readonly",
                declared_version="21.1",
            )
            plan=build_v21_1_bank_corpus_validation_plan(game,csv)
            self.assertEqual(V21_1_BANK_CORPUS_VALIDATION_SCOPE,plan.validation_scope)
            self.assertEqual(3,plan.counts()["applicable"])
            simulate_plan(plan)
            candidate=base/"candidate"
            result=reconstruct_copy(plan,candidate,base/"reports")
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before,snapshot_tree(game)).passed)
            before={}
            after={}
            for relative in ("Data/messages_core.dat","Data/messages_game.dat"):
                before.update({row["id_stable"]:row["traduction_fr"] for row in extract_message_bank(game/relative,relative)})
                after.update({row["id_stable"]:row["traduction_fr"] for row in extract_message_bank(candidate/relative,relative)})
            selected_ids={row["id_stable"] for row in selected}
            for row in selected:
                self.assertEqual(row["traduction_fr"],after[row["id_stable"]])
            self.assertEqual(
                {k:v for k,v in before.items() if k not in selected_ids},
                {k:v for k,v in after.items() if k not in selected_ids},
            )


if __name__=="__main__":
    unittest.main()
