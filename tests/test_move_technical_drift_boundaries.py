"""Strict boundaries around readonly Move Target and Accuracy drift.

Synthetic fixtures only; never silently repair a real compiled game.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from essentials_move import MoveIntegrityError
from essentials_move_split import build_split_move_readonly_proofs
from ruby_marshal_writer import dumps
from test_move_split_readonly import fixtures


class MoveTechnicalDriftBoundaryTests(unittest.TestCase):
    def _parts(self):
        pbs, compiled, core, game = fixtures()
        return pbs, compiled, dumps(core), dumps(game)

    def test_accuracy_zero_is_valid_readonly_drift(self):
        pbs, compiled, core_raw, game_raw = self._parts()
        compiled["ALPHA"].ivars["@accuracy"] = 0
        proofs = build_split_move_readonly_proofs(
            pbs, dumps(compiled), core_raw, game_raw,
        )
        info = json.loads(proofs[("ALPHA", "Description", 1)].proof.compiled_structure)
        self.assertEqual(["Accuracy"], info["technical_mismatches"])
        self.assertIs(info["reconstruction_allowed"], False)

    def test_accuracy_outside_allowed_range_refused(self):
        for bad in (-1, 101):
            with self.subTest(value=bad):
                pbs, compiled, core_raw, game_raw = self._parts()
                compiled["ALPHA"].ivars["@accuracy"] = bad
                with self.assertRaises(MoveIntegrityError):
                    build_split_move_readonly_proofs(
                        pbs, dumps(compiled), core_raw, game_raw,
                    )

    def test_boolean_accuracy_is_not_valid_integer(self):
        pbs, compiled, core_raw, game_raw = self._parts()
        compiled["ALPHA"].ivars["@accuracy"] = True
        with self.assertRaises(MoveIntegrityError):
            build_split_move_readonly_proofs(
                pbs, dumps(compiled), core_raw, game_raw,
            )

    def test_bad_target_encoding_is_refused(self):
        for target in ("Near Other", "Near-Other", ""):
            with self.subTest(value=target):
                pbs, compiled, core_raw, game_raw = self._parts()
                compiled["ALPHA"].ivars["@target"] = target
                with self.assertRaises(MoveIntegrityError):
                    build_split_move_readonly_proofs(
                        pbs, dumps(compiled), core_raw, game_raw,
                    )

    def test_supported_drift_does_not_mutate_compiled_object_or_banks(self):
        pbs, compiled, core_raw, game_raw = self._parts()
        compiled["ALPHA"].ivars["@target"] = "User"
        compiled["ALPHA"].ivars["@accuracy"] = 88
        before = [hashlib.sha256(x).hexdigest() for x in
                  (pbs, dumps(compiled), core_raw, game_raw)]
        build_split_move_readonly_proofs(pbs, dumps(compiled), core_raw, game_raw)
        after = [hashlib.sha256(x).hexdigest() for x in
                 (pbs, dumps(compiled), core_raw, game_raw)]
        self.assertEqual(before, after)
        self.assertEqual("User", compiled["ALPHA"].ivars["@target"])
        self.assertEqual(88, compiled["ALPHA"].ivars["@accuracy"])


if __name__ == "__main__":
    unittest.main()
