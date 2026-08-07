from __future__ import annotations

import unittest

from ruby_marshal_reader import MarshalReader, RubyObject, RubyString
from ruby_marshal_writer import dumps


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


if __name__ == "__main__":
    unittest.main()
