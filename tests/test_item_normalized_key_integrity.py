"""Synthetic Item normalization: canonical key and full value remain distinct.

The reader may normalize Ruby message keys, never silently normalize values
or grant reconstruction permission. No game assets or write operations.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from essentials_item import ItemIntegrityError
from essentials_item_split_readonly import (
    _ruby_message_key, build_split_item_readonly_proofs,
)
from ruby_marshal_writer import dumps
from test_item_split_readonly import (
    load_from_bytes, ruby_text, synthetic_sources,
)


class ItemNormalizedKeyIntegrityTests(unittest.TestCase):
    def test_whitespace_key_normalization_preserves_meaningful_single_spaces(self):
        cases = {
            " A  synthetic\titem  ": "A synthetic\titem",
            "A\r\nsynthetic": "A synthetic",
            "A\x01synthetic": "A\x01synthetic",
            "A synthetic": "A synthetic",
        }
        for source, expected in cases.items():
            with self.subTest(source=repr(source)):
                self.assertEqual(expected, _ruby_message_key(source))

    def test_canonical_normalized_key_and_unmodified_full_value_have_distinct_hashes(self):
        inputs = synthetic_sources()
        proof = build_split_item_readonly_proofs(*inputs)[("RAIDBAIT", "Description", 1)]
        meta = json.loads(proof.proof.runtime_structure)
        self.assertTrue(meta["key_normalization_applied"])
        self.assertEqual("Data/messages_game.dat", proof.runtime_file)
        self.assertEqual("A synthetic  double-spaced description.", proof.proof.source)
        self.assertEqual(
            hashlib.sha256(b"A synthetic double-spaced description.").hexdigest(),
            meta["normalized_key_sha256"],
        )
        self.assertNotEqual(
            meta["normalized_key_sha256"], meta["source_sha256"],
        )
        self.assertFalse(meta["reconstruction_allowed"])

    def test_unormalized_physical_key_is_refused(self):
        inputs = list(synthetic_sources())
        before = [hashlib.sha256(value).hexdigest() for value in inputs]
        bank = load_from_bytes(inputs[4])
        mapping = bank[9]
        key = next(k for k in mapping if k.text() == "A synthetic double-spaced description.")
        value = mapping.pop(key)
        mapping[ruby_text("A synthetic  double-spaced description.")] = value
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(
                inputs[0], inputs[1], inputs[2], inputs[3], dumps(bank),
            )
        self.assertEqual(before, [hashlib.sha256(value).hexdigest() for value in inputs])

    def test_normalized_value_instead_of_original_full_text_is_refused(self):
        inputs = list(synthetic_sources())
        before = [hashlib.sha256(value).hexdigest() for value in inputs]
        bank = load_from_bytes(inputs[4])
        mapping = bank[9]
        key = next(k for k in mapping if k.text() == "A synthetic double-spaced description.")
        mapping[key] = ruby_text("A synthetic double-spaced description.")
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(
                inputs[0], inputs[1], inputs[2], inputs[3], dumps(bank),
            )
        self.assertEqual(before, [hashlib.sha256(value).hexdigest() for value in inputs])


if __name__ == "__main__":
    unittest.main()
