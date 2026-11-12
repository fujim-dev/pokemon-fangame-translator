"""Read-only split Pokedex proof contract; no reconstruction authorization."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SpeciesIntegrityError,
)
from essentials_species_split import (
    GAME_MESSAGES_FILE,
    SPLIT_POKEDEX_READONLY_FORMAT,
    build_split_pokedex_readonly_proofs,
)
from ruby_marshal_reader import RubyString, load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game


class PokedexSplitReadonlyTests(unittest.TestCase):
    def make_fixture(self, root):
        prepare_v21_game(root, species_validation=True)
        core_path = root / SPECIES_MESSAGES_FILE
        core = load(core_path)
        game = [{} for _ in range(len(core))]
        key = next(iter(core[3]))
        value = core[3].pop(key)
        game[3][key] = value
        core[3][RubyString(b"Synthetic obsolete extra entry", {"E": True})] = RubyString(
            b"Synthetic obsolete extra entry", {"E": True}
        )
        core_path.write_bytes(dumps(core))
        (root / GAME_MESSAGES_FILE).write_bytes(dumps(game))
        return [root / x for x in (
            SPECIES_PBS_FILE, SPECIES_FORMS_PBS_FILE,
            COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE, GAME_MESSAGES_FILE,
        )]

    def test_split_and_obsolete_core_extra_are_readonly(self):
        with tempfile.TemporaryDirectory(prefix="pft_pokedex_split_") as temp:
            root = Path(temp) / "game"
            paths = self.make_fixture(root)
            originals = [p.read_bytes() for p in paths]
            results = build_split_pokedex_readonly_proofs(*originals)
            self.assertEqual(3, len(results))
            self.assertEqual(
                {SPECIES_MESSAGES_FILE, GAME_MESSAGES_FILE},
                {r.runtime_file for r in results.values()},
            )
            for found in results.values():
                meta = json.loads(found.proof.runtime_structure)
                self.assertEqual(SPLIT_POKEDEX_READONLY_FORMAT, meta["format"])
                self.assertFalse(meta["reconstruction_allowed"])
                self.assertEqual(1, meta["historical_core_extra_keys"])
            self.assertEqual(originals, [p.read_bytes() for p in paths])

    def test_missing_game_key_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="pft_pokedex_split_missing_") as temp:
            root = Path(temp) / "game"
            paths = self.make_fixture(root)
            game_path = root / GAME_MESSAGES_FILE
            game = load(game_path)
            game[3].clear()
            game_path.write_bytes(dumps(game))
            with self.assertRaises(SpeciesIntegrityError):
                build_split_pokedex_readonly_proofs(
                    *(p.read_bytes() for p in paths)
                )

    def test_wrong_game_value_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="pft_pokedex_split_wrong_") as temp:
            root = Path(temp) / "game"
            paths = self.make_fixture(root)
            game_path = root / GAME_MESSAGES_FILE
            game = load(game_path)
            k = next(iter(game[3]))
            game[3][k] = RubyString(b"WRONG", {"E": True})
            game_path.write_bytes(dumps(game))
            with self.assertRaises(SpeciesIntegrityError):
                build_split_pokedex_readonly_proofs(
                    *(p.read_bytes() for p in paths)
                )


if __name__ == "__main__":
    unittest.main()
