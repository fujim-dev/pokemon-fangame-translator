from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ruby_marshal_reader import MarshalReader, RubyHashKey, RubyObject, RubyString
from ruby_marshal_writer import dump, dumps


def loads_bytes(payload: bytes):
    reader = MarshalReader(payload)
    if payload[:2] != b"\x04\x08":
        raise ValueError("En-tête Marshal invalide")
    reader.pos = 2
    return reader.read_object()


class MarshalRoundtripTests(unittest.TestCase):
    def test_minimal_supported_object_roundtrip(self) -> None:
        shared = RubyString("Bonjour".encode("utf-8"), {"E": True})
        root = RubyObject(
            "PFT::Fixture",
            {
                "@name": shared,
                "@values": [1, True, None, shared],
            },
        )

        restored = loads_bytes(dumps(root))

        self.assertIsInstance(restored, RubyObject)
        self.assertEqual("PFT::Fixture", restored.class_name)
        self.assertEqual("Bonjour", restored.ivars["@name"].text())
        self.assertIs(restored.ivars["@name"], restored.ivars["@values"][3])

    def test_float_is_registered_for_object_links(self) -> None:
        shared = 1.5

        restored = loads_bytes(dumps([shared, shared]))

        self.assertEqual([1.5, 1.5], restored)
        self.assertIs(restored[0], restored[1])

    def test_unhashable_ruby_array_key_is_preserved(self) -> None:
        payload = b'\x04\x08{\x06[\x06i\x06"\x06x'

        restored = loads_bytes(payload)

        key = next(iter(restored))
        self.assertIsInstance(key, RubyHashKey)
        self.assertEqual([1], key.value)
        self.assertEqual("x", restored[key].text())
        roundtripped = loads_bytes(dumps(restored))
        roundtrip_key = next(iter(roundtripped))
        self.assertEqual([1], roundtrip_key.value)
        self.assertEqual("x", roundtripped[roundtrip_key].text())

    def test_dump_validation_failure_preserves_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "Map001.rxdata"
            previous = b"previous marshal file"
            destination.write_bytes(previous)

            with patch(
                "ruby_marshal_writer.load",
                side_effect=ValueError("validation simulee"),
            ):
                with self.assertRaises(ValueError):
                    dump([RubyString(b"Hello")], destination)

            self.assertEqual(previous, destination.read_bytes())


if __name__ == "__main__":
    unittest.main()
