"""Cross-bank collision adversarial tests for split Item and Move provenance.

All sources are synthetic in-memory Ruby Marshal and PBS. No reconstruction
rights are granted, no game files are accessed or written.
"""
from __future__ import annotations

import hashlib
import unittest

from essentials_item import ItemIntegrityError
from essentials_item_split_readonly import build_split_item_readonly_proofs
from essentials_move import MoveIntegrityError
from essentials_move_split import build_split_move_readonly_proofs
from ruby_marshal_reader import MarshalReader
from ruby_marshal_writer import dumps
from test_item_split_readonly import synthetic_sources, ruby_text as item_text
from test_move_split_readonly import fixtures as move_fixtures, t as move_text


def unmarshal(raw: bytes):
    reader = MarshalReader(raw)
    reader.pos = 2
    return reader.read_object()


class SplitBankCollisionGuardsTests(unittest.TestCase):
    def _assert_item_duplicate_rejected(self, index: int, source: str):
        parts = list(synthetic_sources())
        before = tuple(hashlib.sha256(x).hexdigest() for x in parts)
        core = unmarshal(parts[3])
        game = unmarshal(parts[4])
        # This item's canonical key already resides in game; recreate it in core.
        self.assertTrue(any(k.text() == source for k in game[index]))
        core[index][item_text(source)] = item_text(source)
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(
                parts[0], parts[1], parts[2], dumps(core), parts[4]
            )
        self.assertEqual(before, tuple(hashlib.sha256(x).hexdigest() for x in parts))

    def _assert_move_duplicate_rejected(self, index: int, source: str):
        pbs, compiled, core, game = move_fixtures()
        raw = (pbs, dumps(compiled), dumps(core), dumps(game))
        before = tuple(hashlib.sha256(x).hexdigest() for x in raw)
        self.assertTrue(any(k.text() == source for k in game[index]))
        core[index][move_text(source)] = move_text(source)
        with self.assertRaises(MoveIntegrityError):
            build_split_move_readonly_proofs(
                raw[0], raw[1], dumps(core), raw[3]
            )
        self.assertEqual(before, tuple(hashlib.sha256(x).hexdigest() for x in raw))

    def test_item_name_duplicate_core_and_game_refused(self):
        self._assert_item_duplicate_rejected(7, "Synthetic Potion")

    def test_item_description_duplicate_core_and_game_refused(self):
        self._assert_item_duplicate_rejected(9, "A synthetic double-spaced description.")

    def test_move_name_duplicate_core_and_game_refused(self):
        self._assert_move_duplicate_rejected(5, "Beta")

    def test_move_description_duplicate_core_and_game_refused(self):
        self._assert_move_duplicate_rejected(6, "Beta Desc")


if __name__ == "__main__":
    unittest.main()
