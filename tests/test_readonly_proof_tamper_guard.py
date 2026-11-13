"""Reject attempts to forge readonly provenance into reconstruction permission.

All input is synthetic and held in memory; no user games or candidates touched.
"""
from __future__ import annotations

import json
import unittest

from essentials_item import ItemIntegrityError, rebuild_item_description_payloads
from essentials_item_split_readonly import build_split_item_readonly_proofs
from essentials_move import MoveIntegrityError, rebuild_move_description_payloads
from essentials_move_split import build_split_move_readonly_proofs
from essentials_species import SpeciesIntegrityError
from essentials_species_category import rebuild_species_category_payloads
from essentials_species_category_split import build_split_category_readonly_proofs
from essentials_species_form import rebuild_species_form_name_payloads
from essentials_species_form_split import build_split_form_name_readonly_proofs
from test_item_split_readonly import synthetic_sources
from test_move_split_readonly import fixtures as move_fixtures
from test_species_split_reconstruction_guard import fixtures as species_fixtures
from ruby_marshal_writer import dumps


def forged_structure(original: str) -> str:
    data = json.loads(original)
    assert data["reconstruction_allowed"] is False
    data["reconstruction_allowed"] = True
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class ReadonlyProofTamperGuardTests(unittest.TestCase):
    def test_move_flag_forgery_rejected(self):
        pbs, compiled, core, game = move_fixtures()
        pbs_compiled = dumps(compiled)
        core_bytes = dumps(core)
        game_bytes = dumps(game)
        bound = build_split_move_readonly_proofs(pbs, pbs_compiled, core_bytes, game_bytes)[("BETA", "Description", 1)]
        proof = bound.proof
        with self.assertRaises(MoveIntegrityError):
            rebuild_move_description_payloads(
                pbs, pbs_compiled, game_bytes,
                section="BETA",source=proof.source,translation="Texte fictif",
                pbs_structure=proof.pbs_structure,compiled_path=proof.compiled_path,
                compiled_structure=proof.compiled_structure,
                runtime_path=proof.runtime_path,
                runtime_structure=forged_structure(proof.runtime_structure),
            )

    def test_item_flag_forgery_rejected(self):
        pbs,extra,compiled,core,game = synthetic_sources()
        bound = build_split_item_readonly_proofs(pbs,extra,compiled,core,game)[("POTION", "Description", 1)]
        proof = bound.proof
        with self.assertRaises(ItemIntegrityError):
            rebuild_item_description_payloads(
                pbs,compiled,core,
                section="POTION",source=proof.source,translation="Texte fictif",
                pbs_structure=proof.pbs_structure,compiled_path=proof.compiled_path,
                compiled_structure=proof.compiled_structure,
                runtime_path=proof.runtime_path,
                runtime_structure=forged_structure(proof.runtime_structure),
            )

    def test_category_flag_forgery_rejected(self):
        pbs,forms,compiled,core,game = species_fixtures()
        proof = build_split_category_readonly_proofs(pbs,forms,compiled,core,game)[("BULBASAUR","Category",1)].proof
        with self.assertRaises(SpeciesIntegrityError):
            rebuild_species_category_payloads(
                pbs,forms,compiled,core,
                section="BULBASAUR",source=proof.source,translation="Texte fictif",
                pbs_structure=proof.pbs_structure,compiled_path=proof.compiled_path,
                compiled_structure=proof.compiled_structure,
                runtime_path=proof.runtime_path,
                runtime_structure=forged_structure(proof.runtime_structure),
            )

    def test_form_name_flag_forgery_rejected(self):
        pbs,forms,compiled,core,game = species_fixtures()
        proof = build_split_form_name_readonly_proofs(pbs,forms,compiled,core,game)[("CHARMANDER,1","FormName",1)].proof
        with self.assertRaises(SpeciesIntegrityError):
            rebuild_species_form_name_payloads(
                pbs,forms,compiled,game,
                section="CHARMANDER,1",source=proof.source,translation="Texte fictif",
                pbs_structure=proof.pbs_structure,compiled_path=proof.compiled_path,
                compiled_structure=proof.compiled_structure,
                runtime_path=proof.runtime_path,
                runtime_structure=forged_structure(proof.runtime_structure),
            )


if __name__=="__main__":
    unittest.main()
