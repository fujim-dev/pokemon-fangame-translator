from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import safe_io
from safe_io import atomic_copy_file, atomic_write_bundle


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


class AtomicBundleTests(unittest.TestCase):
    def test_rollback_failure_preserves_exact_previous_artifact_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_bundle_failed_rollback_") as temp_dir:
            base = Path(temp_dir)
            first = base / "project.csv"
            second = base / "report.txt"
            first.write_bytes(b"old csv")
            second.write_bytes(b"old report")
            real_replace = safe_io._replace_file
            calls = 0

            def fail_publication_then_rollback(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls in {2, 3}:
                    raise OSError("synthetic replace failure")
                real_replace(source, destination)

            with patch(
                "safe_io._replace_file",
                side_effect=fail_publication_then_rollback,
            ):
                with self.assertRaisesRegex(OSError, "rollback incomplet"):
                    atomic_write_bundle(
                        {
                            first: b"new csv",
                            second: b"new report",
                        }
                    )

            recovery_files = list(base.glob(".project.csv.pft-bundle-old-*.tmp"))
            self.assertEqual(1, len(recovery_files))
            self.assertEqual(b"old csv", recovery_files[0].read_bytes())
            self.assertEqual(b"new csv", first.read_bytes())
            self.assertEqual(b"old report", second.read_bytes())

    def test_late_publication_failure_restores_every_previous_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_bundle_rollback_") as temp_dir:
            base = Path(temp_dir)
            first = base / "project.csv"
            second = base / "report.txt"
            first.write_bytes(b"old csv")
            second.write_bytes(b"old report")
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic late publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(OSError, "publication"):
                    atomic_write_bundle(
                        {
                            first: b"new csv",
                            second: b"new report",
                        }
                    )

            self.assertEqual(b"old csv", first.read_bytes())
            self.assertEqual(b"old report", second.read_bytes())
            self.assertEqual([], list(base.glob(".*.pft-bundle-*")))

    def test_concurrent_artifact_change_is_preserved_and_bundle_is_cancelled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_bundle_race_") as temp_dir:
            base = Path(temp_dir)
            first = base / "project.csv"
            second = base / "report.txt"
            first.write_bytes(b"old csv")
            second.write_bytes(b"old report")
            real_replace = safe_io._replace_file
            calls = 0

            def change_second_after_first_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                real_replace(source, destination)
                if calls == 1:
                    second.write_bytes(b"concurrent report")

            with patch(
                "safe_io._replace_file",
                side_effect=change_second_after_first_publish,
            ):
                with self.assertRaisesRegex(OSError, "chang"):
                    atomic_write_bundle(
                        {
                            first: b"new csv",
                            second: b"new report",
                        }
                    )

            self.assertEqual(b"old csv", first.read_bytes())
            self.assertEqual(b"concurrent report", second.read_bytes())


if __name__ == "__main__":
    unittest.main()
