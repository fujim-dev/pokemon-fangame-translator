"""End-to-end read-only extraction for Essentials v21.1 split Ability banks."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters.pokemon_essentials import PokemonEssentialsAdapter
from essentials_ability import ABILITY_MESSAGES_FILE
from essentials_ability_split import (
    SPLIT_READONLY_FORMAT,
    build_split_ability_readonly_proofs,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game


def prepare_split_fixture(root: Path) -> tuple[Path, Path]:
    prepare_v21_game(root, ability_validation=True)
    core_file = root / ABILITY_MESSAGES_FILE
    game_file = root / "Data/messages_game.dat"
    core = load(core_file)
    game = [{} for _ in range(len(core))]
    for index in (10, 11):
        key, value = core[index].popitem()
        game[index][key] = value
    core_file.write_bytes(dumps(core))
    game_file.write_bytes(dumps(game))
    return core_file, game_file


class AbilitySplitExtractionTests(unittest.TestCase):
    def test_split_extraction_uses_physical_runtime_file_without_writing(self):
        with tempfile.TemporaryDirectory(prefix="pft_split_extract_") as temporary:
            root = Path(temporary) / "game"
            core_file, game_file = prepare_split_fixture(root)
            tracked = (
                root / "PBS/abilities.txt", root / "Data/abilities.dat",
                core_file, game_file
            )
            before = [p.read_bytes() for p in tracked]
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [
                row for row in result.rows
                if row.get("fichier") == "PBS/abilities.txt"
                and row.get("commande") in {"Name", "Description"}
            ]
            self.assertEqual(6, len(rows))
            self.assertEqual(
                {"Data/messages_core.dat", "Data/messages_game.dat"},
                {row["pbs_runtime_file"] for row in rows},
            )
            self.assertTrue(all(
                json.loads(row["pbs_runtime_structure"])["format"]
                == SPLIT_READONLY_FORMAT for row in rows
            ))
            self.assertTrue(all(
                json.loads(row["pbs_runtime_structure"])["reconstruction_allowed"] is False
                for row in rows
            ))
            self.assertEqual(before, [p.read_bytes() for p in tracked])

    def test_bad_game_bank_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pft_split_wrong_") as temporary:
            root = Path(temporary) / "game"
            core_file, game_file = prepare_split_fixture(root)
            game = load(game_file)
            game[10].clear()
            game_file.write_bytes(dumps(game))
            from essentials_ability import AbilityIntegrityError
            with self.assertRaises((AbilityIntegrityError, ValueError)):
                build_split_ability_readonly_proofs(
                    (root / "PBS/abilities.txt").read_bytes(),
                    (root / "Data/abilities.dat").read_bytes(),
                    core_file.read_bytes(),
                    game_file.read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
