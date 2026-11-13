"""Synthetic fail-closed tests: split Move provenance must not enable reconstruction."""
from __future__ import annotations
import hashlib
import unittest
from essentials_move import MoveIntegrityError, rebuild_move_description_payloads
from essentials_move_split import build_split_move_readonly_proofs
from ruby_marshal_writer import dumps
from test_move_split_readonly import fixtures

class MoveSplitReconstructionGuardTests(unittest.TestCase):
    def _reject(self, identifier, bank_index):
        pbs, compiled, core, game = fixtures()
        originals=[pbs,dumps(compiled),dumps(core),dumps(game)]
        before=[hashlib.sha256(x).hexdigest() for x in originals]
        proofs=build_split_move_readonly_proofs(*originals)
        proof=proofs[(identifier,"Description",1)]
        self.assertEqual(
            "Data/messages_core.dat" if bank_index==2 else "Data/messages_game.dat",
            proof.runtime_file)
        kwargs=dict(section=identifier,source=proof.proof.source,
                    translation="Description française synthétique.",
                    pbs_structure=proof.proof.pbs_structure,
                    compiled_path=proof.proof.compiled_path,
                    compiled_structure=proof.proof.compiled_structure,
                    runtime_path=proof.proof.runtime_path,
                    runtime_structure=proof.proof.runtime_structure)
        with self.assertRaises(MoveIntegrityError):
            rebuild_move_description_payloads(
                originals[0],originals[1],originals[bank_index],**kwargs)
        self.assertEqual(before,[hashlib.sha256(x).hexdigest() for x in originals])
        self.assertEqual(
            False,
            __import__("json").loads(proof.proof.runtime_structure)["reconstruction_allowed"])

    def test_core_move_split_refused(self):
        self._reject("ALPHA",2)

    def test_game_move_split_refused(self):
        self._reject("BETA",3)

if __name__=="__main__":
    unittest.main()
