"""Synthetic safeguards: split Species Category/FormName proofs must stay read-only."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from essentials_species import SpeciesIntegrityError
from essentials_species_category import rebuild_species_category_payloads
from essentials_species_category_split import build_split_category_readonly_proofs
from essentials_species_form import rebuild_species_form_name_payloads
from essentials_species_form_split import build_split_form_name_readonly_proofs
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game


def fixtures():
    with tempfile.TemporaryDirectory(prefix="pft_species_split_guard_") as temporary:
        game = Path(temporary) / "synthetic_game"
        prepare_v21_game(game, species_validation=True)
        base = (game / "PBS/pokemon.txt").read_bytes()
        forms = (game / "PBS/pokemon_forms.txt").read_bytes()
        compiled = (game / "Data/species.dat").read_bytes()
        core = load(game / "Data/messages_core.dat")
        game_bank = [{} for _ in range(len(core))]  # species-only fixture has no game bank array
    for index, match in (
        (2, "Synthetic Lizard"),
        (4, "Synthetic explicit form"),
    ):
        key = next(key for key in core[index] if key.text() == match)
        game_bank[index][key] = core[index].pop(key)
    return base, forms, compiled, dumps(core), dumps(game_bank)


class SpeciesSplitReconstructionGuardTests(unittest.TestCase):
    def _reject(self, field, section, index, from_game):
        payloads = fixtures()
        before = tuple(hashlib.sha256(x).hexdigest() for x in payloads)
        if field == "Category":
            proofs = build_split_category_readonly_proofs(*payloads)
            rebuild = rebuild_species_category_payloads
        else:
            proofs = build_split_form_name_readonly_proofs(*payloads)
            rebuild = rebuild_species_form_name_payloads
        bound = proofs[(section, field, 1)]
        self.assertEqual(
            "Data/messages_game.dat" if from_game else "Data/messages_core.dat",
            bound.runtime_file,
        )
        proof = bound.proof
        metadata = json.loads(proof.runtime_structure)
        self.assertIs(metadata["reconstruction_allowed"], False)
        kwargs = {
            "section": section,
            "source": proof.source,
            "translation": "Traduction française synthétique",
            "pbs_structure": proof.pbs_structure,
            "compiled_path": proof.compiled_path,
            "compiled_structure": proof.compiled_structure,
            "runtime_path": proof.runtime_path,
            "runtime_structure": proof.runtime_structure,
        }
        bank = payloads[4] if from_game else payloads[3]
        with self.assertRaises(SpeciesIntegrityError):
            rebuild(payloads[0], payloads[1], payloads[2], bank, **kwargs)
        self.assertEqual(before, tuple(hashlib.sha256(x).hexdigest() for x in payloads))

    def test_category_core_refused(self):
        self._reject("Category", "BULBASAUR", 2, False)

    def test_category_game_refused(self):
        self._reject("Category", "CHARMANDER", 2, True)

    def test_form_name_core_refused(self):
        self._reject("FormName", "CUBONE,1", 4, False)

    def test_form_name_game_refused(self):
        self._reject("FormName", "CHARMANDER,1", 4, True)


if __name__ == "__main__":
    unittest.main()
