from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reconstruction_engine
from analysis.integrity import compare_snapshots, snapshot_tree
from reconstruction_engine import ReconstructionError, build_plan, reconstruct_copy, simulate_plan
from structured_extractor import stable_id


PROJECT_FIELDS = [
    "id_stable",
    "type",
    "fichier",
    "texte_source",
    "traduction_fr",
    "statut",
    "carte_id",
    "carte_nom",
    "evenement_id",
    "evenement_nom",
    "page",
    "commande",
    "sous_index",
]


def write_project(path: Path, relative: str = "PBS/fixture.txt") -> None:
    row = {
        "id_stable": stable_id("pbs", relative, "GLOBAL", "Name", 1),
        "type": "PBS — Name",
        "fichier": relative,
        "texte_source": "Hello",
        "traduction_fr": "Bonjour",
        "statut": "Accepté",
        "evenement_id": "GLOBAL",
        "evenement_nom": "GLOBAL",
        "commande": "Name",
        "sous_index": "1",
    }
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROJECT_FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in PROJECT_FIELDS})


def prepare_essentials_game(game_root: Path) -> None:
    markers = {
        "Game.exe": b"synthetic executable",
        "Game.ini": b"[Game]\nLibrary=RGSS104E.dll\n",
        "Data/System.rxdata": b"synthetic system",
        "Data/messages_game.dat": b"synthetic messages",
        "PBS/pokemon.txt": b"Pokemon Essentials v21.1",
    }
    for relative, payload in markers.items():
        path = game_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def synthetic_plan(base: Path):
    game_root = base / "game"
    pbs_path = game_root / "PBS" / "fixture.txt"
    pbs_path.parent.mkdir(parents=True)
    pbs_path.write_text("Name = Hello\n", encoding="utf-8")
    prepare_essentials_game(game_root)
    untouched = game_root / "Graphics" / "untouched.bin"
    untouched.parent.mkdir(parents=True)
    untouched.write_bytes(b"synthetic-untouched-file")
    csv_path = base / "project.csv"
    write_project(csv_path)
    return game_root, simulate_plan(build_plan(game_root, csv_path))


class IntegrityServiceTests(unittest.TestCase):
    def test_comparison_reports_every_unexpected_change_without_file_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_integrity_") as temp_dir:
            base = Path(temp_dir)
            reference = base / "reference"
            candidate = base / "candidate"
            for root in (reference, candidate):
                (root / "PBS").mkdir(parents=True)

            (reference / "PBS" / "target.txt").write_text("source target", encoding="utf-8")
            (reference / "PBS" / "untouched.txt").write_text("must stay", encoding="utf-8")
            (reference / "PBS" / "emptied.txt").write_text("not empty", encoding="utf-8")
            (reference / "PBS" / "missing.txt").write_text("present", encoding="utf-8")

            (candidate / "PBS" / "target.txt").write_text("translated target", encoding="utf-8")
            (candidate / "PBS" / "untouched.txt").write_text("tampered", encoding="utf-8")
            (candidate / "PBS" / "emptied.txt").write_bytes(b"")
            (candidate / "PBS" / "unexpected.txt").write_text("extra", encoding="utf-8")

            comparison = compare_snapshots(
                snapshot_tree(reference),
                snapshot_tree(candidate),
                allowed_changed={"PBS/target.txt"},
            )

        self.assertFalse(comparison.passed)
        self.assertEqual(("PBS/missing.txt",), comparison.missing_files)
        self.assertEqual(("PBS/unexpected.txt",), comparison.unexpected_files)
        self.assertIn("PBS/untouched.txt", comparison.changed_files)
        self.assertIn("PBS/emptied.txt", comparison.emptied_files)
        self.assertIn("PBS/target.txt", comparison.allowed_fingerprints)
        self.assertNotIn("source target", json.dumps(comparison.to_manifest()))
        self.assertNotIn("translated target", json.dumps(comparison.to_manifest()))


class ReconstructionIntegrityTests(unittest.TestCase):
    def test_manifest_proves_targeted_and_untargeted_file_integrity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_integrity_manifest_") as temp_dir:
            base = Path(temp_dir)
            game_root, plan = synthetic_plan(base)
            target = base / "game_VERSION_FR"

            result = reconstruct_copy(plan, target, base / "reports")

            manifest_text = Path(result.manifest_path).read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            integrity = manifest["controle_integrite"]
            target_untouched = (target / "Graphics" / "untouched.bin").read_bytes()
            source_untouched = (game_root / "Graphics" / "untouched.bin").read_bytes()

        self.assertTrue(result.integrity_valid)
        self.assertEqual("valide", integrity["statut"])
        self.assertEqual([], integrity["copie"]["fichiers_manquants"])
        self.assertEqual([], integrity["copie"]["fichiers_inattendus"])
        self.assertEqual([], integrity["copie"]["fichiers_modifies_hors_plan"])
        self.assertEqual([], integrity["original"]["fichiers_modifies_hors_plan"])
        fingerprints = integrity["copie"]["empreintes_fichiers_cibles"]["PBS/fixture.txt"]
        self.assertNotEqual(fingerprints["avant"], fingerprints["apres"])
        self.assertNotIn("Hello", manifest_text)
        self.assertNotIn("Bonjour", manifest_text)
        self.assertEqual(b"synthetic-untouched-file", target_untouched)
        self.assertEqual(b"synthetic-untouched-file", source_untouched)

    def test_changed_untargeted_file_marks_the_copy_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_integrity_failure_") as temp_dir:
            base = Path(temp_dir)
            _game_root, plan = synthetic_plan(base)
            target = base / "game_VERSION_FR"
            real_apply_file = reconstruction_engine._apply_file

            def apply_then_corrupt(target_root: Path, relative: str, items) -> None:
                real_apply_file(target_root, relative, items)
                (target_root / "Graphics" / "untouched.bin").write_bytes(b"corrupted")

            with patch("reconstruction_engine._apply_file", side_effect=apply_then_corrupt):
                with self.assertRaisesRegex(ReconstructionError, "intégrité|intégrite"):
                    reconstruct_copy(plan, target, base / "reports")

            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
