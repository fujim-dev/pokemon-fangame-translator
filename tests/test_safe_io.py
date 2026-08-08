from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import safe_io
from safe_io import atomic_copy_file


class AtomicCopyTests(unittest.TestCase):
    def test_exact_expected_copy_replaces_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_copy_") as temp_dir:
            base = Path(temp_dir)
            source = base / "source.bin"
            destination = base / "destination.bin"
            payload = b"contenu source stable"
            source.write_bytes(payload)
            destination.write_bytes(b"ancienne destination")

            atomic_copy_file(
                source,
                destination,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )

            self.assertEqual(payload, destination.read_bytes())
            self.assertEqual([], list(base.glob(".destination.bin.*.tmp")))

    def test_wrong_expected_hash_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_hash_") as temp_dir:
            base = Path(temp_dir)
            source = base / "source.bin"
            destination = base / "destination.bin"
            source.write_bytes(b"source")
            destination.write_bytes(b"destination intacte")

            with self.assertRaisesRegex(OSError, "empreinte SHA-256"):
                atomic_copy_file(source, destination, expected_sha256="0" * 64)

            self.assertEqual(b"destination intacte", destination.read_bytes())
            self.assertEqual([], list(base.glob(".destination.bin.*.tmp")))

    def test_source_changed_during_read_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_race_") as temp_dir:
            base = Path(temp_dir)
            source = base / "source.bin"
            destination = base / "destination.bin"
            source.write_bytes(b"premiere version")
            destination.write_bytes(b"destination intacte")
            real_copy = safe_io._copy_stream_and_hash

            def copy_then_change(source_handle, target_handle):
                digest = real_copy(source_handle, target_handle)
                source.write_bytes(b"seconde version!")
                return digest

            with patch("safe_io._copy_stream_and_hash", side_effect=copy_then_change):
                with self.assertRaisesRegex(OSError, "changé pendant sa copie"):
                    atomic_copy_file(source, destination)

            self.assertEqual(b"destination intacte", destination.read_bytes())
            self.assertEqual([], list(base.glob(".destination.bin.*.tmp")))

    def test_create_only_mode_never_overwrites_competing_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_create_only_") as temp_dir:
            base = Path(temp_dir)
            source = base / "source.bin"
            destination = base / "destination.bin"
            source.write_bytes(b"source")
            destination.write_bytes(b"fichier concurrent")

            with self.assertRaises(FileExistsError):
                atomic_copy_file(source, destination, replace_existing=False)

            self.assertEqual(b"fichier concurrent", destination.read_bytes())
            self.assertEqual([], list(base.glob(".destination.bin.*.tmp")))


if __name__ == "__main__":
    unittest.main()
