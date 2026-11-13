"""Adversarial synthetic Item key normalization and Move technical drift.

Proof construction only; every returned proof remains non-reconstructible.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from essentials_item import ItemIntegrityError
from essentials_item_split_readonly import (
    _ruby_message_key, build_split_item_readonly_proofs,
)
from essentials_move import MoveIntegrityError
from essentials_move_split import build_split_move_readonly_proofs
from ruby_marshal_writer import dumps
from test_item_split_readonly import synthetic_sources, load_from_bytes, ruby_text
from test_move_split_readonly import fixtures as move_fixtures


class ItemNormalizationAndMoveDriftTests(unittest.TestCase):
    def test_two_distinct_item_sources_normalizing_to_same_key_are_refused(self):
        parts = list(synthetic_sources())
        original = tuple(hashlib.sha256(b).hexdigest() for b in parts)
        self.assertEqual("Synthetic Potion", _ruby_message_key("Synthetic  Potion"))
        self.assertEqual("Synthetic Potion", _ruby_message_key("Synthetic Potion"))
        self.assertNotEqual("Synthetic  Potion", "Synthetic Potion")
        # The supplemental item now names itself like the base Potion after
        # Ruby whitespace normalization; keep PBS and compiled name consistent.
        expected = b"Name = Synthetic Raid Bait\r\n"
        self.assertEqual(1, parts[1].count(expected))
        parts[1] = parts[1].replace(expected, b"Name = Synthetic  Potion\r\n", 1)
        compiled = load_from_bytes(parts[2])
        compiled["RAIDBAIT"].ivars["@real_name"] = ruby_text("Synthetic  Potion")
        parts[2] = dumps(compiled)
        with self.assertRaisesRegex(ItemIntegrityError, "normalis"):
            build_split_item_readonly_proofs(*parts)
        self.assertEqual(
            original,
            tuple(hashlib.sha256(b).hexdigest() for b in synthetic_sources())
        )

    def test_both_supported_move_technical_drifts_are_reported_readonly(self):
        pbs, compiled, core, game = move_fixtures()
        compiled["ALPHA"].ivars["@target"] = "User"
        compiled["ALPHA"].ivars["@accuracy"] = 88
        proofs = build_split_move_readonly_proofs(
            pbs, dumps(compiled), dumps(core), dumps(game)
        )
        alpha = proofs[("ALPHA", "Description", 1)]
        details = json.loads(alpha.proof.compiled_structure)
        self.assertEqual(["Target", "Accuracy"], details["technical_mismatches"])
        self.assertFalse(details["reconstruction_allowed"])
        self.assertFalse(
            json.loads(alpha.proof.runtime_structure)["reconstruction_allowed"]
        )
        # The other move must not inherit Alpha's technical discrepancy.
        beta = proofs[("BETA", "Description", 1)]
        self.assertEqual([], json.loads(beta.proof.compiled_structure)["technical_mismatches"])

    def test_unrelated_move_technical_change_still_rejected(self):
        pbs, compiled, core, game = move_fixtures()
        compiled["ALPHA"].ivars["@priority"] = 1
        with self.assertRaises(MoveIntegrityError):
            build_split_move_readonly_proofs(
                pbs, dumps(compiled), dumps(core), dumps(game)
            )


if __name__ == "__main__":
    unittest.main()
