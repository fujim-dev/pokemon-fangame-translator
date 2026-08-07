from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from Pokemon_Fangame_Translator import create_private_diagnostic_sample


class DiagnosticSampleTests(unittest.TestCase):
    def test_private_sample_is_minimal_and_leaves_no_application_work_folder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_sample_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            data = game_root / "Data"
            pbs = game_root / "PBS"
            backup = pbs / "backup_old"
            application_dir = base / "application"
            output = base / "exports" / "sample.zip"
            data.mkdir(parents=True)
            backup.mkdir(parents=True)
            application_dir.mkdir()

            (data / "messages_game.dat").write_bytes(b"fixture messages")
            (data / "Map001.rxdata").write_bytes(b"fixture map")
            (data / "Scripts.rxdata").write_bytes(b"excluded script")
            (game_root / "Game.exe").write_bytes(b"excluded executable")
            (pbs / "items.txt").write_text("Name = Fixture\n", encoding="utf-8")
            (backup / "items.txt").write_text("Name = Backup\n", encoding="utf-8")

            create_private_diagnostic_sample(
                game_root,
                output,
                application_dir=application_dir,
            )

            self.assertTrue(output.is_file())
            self.assertFalse((application_dir / "Travail_Echantillon").exists())
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                manifest_name = next(
                    name for name in names if name.endswith("CONTENU_ECHANTILLON_PRIVE.txt")
                )
                manifest = archive.read(manifest_name).decode("utf-8")

            self.assertTrue(any(name.endswith("Data/messages_game.dat") for name in names))
            self.assertTrue(any(name.endswith("Data/Map001.rxdata") for name in names))
            self.assertTrue(any(name.endswith("PBS/items.txt") for name in names))
            self.assertFalse(any(name.endswith("Scripts.rxdata") for name in names))
            self.assertFalse(any(name.endswith("Game.exe") for name in names))
            self.assertFalse(any("backup_old" in name for name in names))
            self.assertIn("Ne la publiez pas", manifest)

    def test_refuses_output_inside_original_or_application(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_sample_path_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            application_dir = base / "application"
            (game_root / "Data").mkdir(parents=True)
            application_dir.mkdir()

            for output in (
                game_root / "sample.zip",
                application_dir / "sample.zip",
            ):
                with self.subTest(output=output):
                    with self.assertRaisesRegex(ValueError, "ne peut pas être enregistré"):
                        create_private_diagnostic_sample(
                            game_root,
                            output,
                            application_dir=application_dir,
                        )
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
