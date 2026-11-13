"""Synthetic regression: incomplete Ability message banks must block certified extraction."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from essentials_ability import (
    ABILITY_MESSAGES_FILE,
    ABILITY_PBS_FILE,
    COMPILED_ABILITY_FILE,
    AbilityIntegrityError,
    build_ability_text_proofs,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game


class AbilityIncompleteRuntimeTests(unittest.TestCase):
    def test_truncated_prefix_bank_is_identified_and_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pft_ability_prefix_") as tmp:
            root = Path(tmp) / "game"
            prepare_v21_game(root, ability_validation=True)
            pbs = (root / ABILITY_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_ABILITY_FILE).read_bytes()
            runtime_file = root / ABILITY_MESSAGES_FILE
            original = runtime_file.read_bytes()
            runtime = load(runtime_file)
            runtime[10].pop(next(reversed(runtime[10])))
            with self.assertRaisesRegex(AbilityIntegrityError, "préfixe exact.*incomplète"):
                build_ability_text_proofs(pbs, compiled, dumps(runtime))
            self.assertEqual(original, runtime_file.read_bytes())

    def test_nonprefix_bank_is_rejected_without_prefix_diagnosis(self):
        with tempfile.TemporaryDirectory(prefix="pft_ability_nonprefix_") as tmp:
            root = Path(tmp) / "game"
            prepare_v21_game(root, ability_validation=True)
            pbs = (root / ABILITY_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_ABILITY_FILE).read_bytes()
            runtime_file = root / ABILITY_MESSAGES_FILE
            runtime = load(runtime_file)
            runtime[10].pop(next(iter(runtime[10])))
            with self.assertRaisesRegex(AbilityIntegrityError, "ne couvre pas exactement"):
                build_ability_text_proofs(pbs, compiled, dumps(runtime))

    def test_complete_bank_still_produces_verified_proofs(self):
        with tempfile.TemporaryDirectory(prefix="pft_ability_complete_") as tmp:
            root = Path(tmp) / "game"
            prepare_v21_game(root, ability_validation=True)
            proofs = build_ability_text_proofs(
                (root / ABILITY_PBS_FILE).read_bytes(),
                (root / COMPILED_ABILITY_FILE).read_bytes(),
                (root / ABILITY_MESSAGES_FILE).read_bytes(),
            )
            self.assertGreater(len(proofs), 0)


if __name__ == "__main__":
    unittest.main()
