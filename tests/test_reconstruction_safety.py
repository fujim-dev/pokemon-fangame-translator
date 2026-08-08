from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reconstruction_engine import (
    ReconstructionError,
    _safe_relative_parts,
    build_plan,
    reconstruct_copy,
    simulate_plan,
)
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


def write_projects(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROJECT_FIELDS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in PROJECT_FIELDS})


def write_project(path: Path, row: dict[str, str]) -> None:
    write_projects(path, [row])


def prepare_essentials_game(game_root: Path) -> None:
    """Ajoute les indices artificiels minimaux exigés par l'adaptateur."""
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


def pbs_row(relative: str, source: str = "Hello", translation: str = "Bonjour") -> dict[str, str]:
    return {
        "id_stable": stable_id("pbs", relative, "GLOBAL", "Name", 1),
        "type": "PBS — Name",
        "fichier": relative,
        "texte_source": source,
        "traduction_fr": translation,
        "statut": "Accepté",
        "evenement_id": "GLOBAL",
        "evenement_nom": "GLOBAL",
        "commande": "Name",
        "sous_index": "1",
    }


class ReconstructionSafetyTests(unittest.TestCase):
    def test_blocks_windows_ambiguous_names(self) -> None:
        for relative in (
            "PBS/fixture:stream.txt",
            "PBS/NUL.txt",
            "PBS/fixture.txt.",
        ):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ReconstructionError, "nom Windows interdit"):
                    _safe_relative_parts(relative)

    def test_blocks_parent_traversal_hidden_inside_relative_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_path_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            (game_root / "PBS").mkdir(parents=True)
            prepare_essentials_game(game_root)
            outside = base / "outside.txt"
            outside.write_text("Name = Hello\n", encoding="utf-8")
            relative = "PBS/../../outside.txt"
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row(relative))

            plan = build_plan(game_root, csv_path)

        self.assertEqual("blocked", plan.items[0].decision)
        self.assertIn("Chemin", plan.items[0].reason)

    def test_blocks_windows_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_absolute_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            game_root.mkdir()
            prepare_essentials_game(game_root)
            outside = base / "outside.txt"
            outside.write_text("Name = Hello\n", encoding="utf-8")
            relative = outside.as_posix()
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row(relative))

            plan = build_plan(game_root, csv_path)

        self.assertEqual("blocked", plan.items[0].decision)
        self.assertIn("Chemin", plan.items[0].reason)

    def test_reconstructs_synthetic_pbs_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_rebuild_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            prepare_essentials_game(game_root)
            legacy_temporary = pbs_path.with_suffix(pbs_path.suffix + ".pfttmp")
            legacy_temporary.write_bytes(b"unrelated synthetic file")
            original = pbs_path.read_bytes()

            relative = "PBS/fixture.txt"
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row(relative))
            plan = simulate_plan(build_plan(game_root, csv_path))
            target = base / "game_VERSION_FR"
            reports = base / "reports"

            result = reconstruct_copy(plan, target, reports)

            self.assertEqual(original, pbs_path.read_bytes())
            self.assertTrue(result.original_unchanged)
            self.assertEqual(1, result.applied)
            self.assertIn("Bonjour", (target / relative).read_text(encoding="utf-8"))
            self.assertEqual(
                b"unrelated synthetic file",
                (target / legacy_temporary.relative_to(game_root)).read_bytes(),
            )

    def test_refuses_source_changed_after_simulation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_stale_plan_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            prepare_essentials_game(game_root)

            relative = "PBS/fixture.txt"
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row(relative))
            plan = simulate_plan(build_plan(game_root, csv_path))

            pbs_path.write_text("Name = Hello\n# Mise à jour synthétique\n", encoding="utf-8")
            target = base / "game_VERSION_FR"

            with self.assertRaisesRegex(ReconstructionError, "changé depuis la simulation"):
                reconstruct_copy(plan, target, base / "reports")

            self.assertFalse(target.exists())

    def test_refuses_report_directory_inside_original(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_reports_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            prepare_essentials_game(game_root)

            relative = "PBS/fixture.txt"
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row(relative))
            plan = simulate_plan(build_plan(game_root, csv_path))
            reports_in_original = game_root / "Rapports"

            with self.assertRaisesRegex(ReconstructionError, "fangame original"):
                reconstruct_copy(plan, base / "game_VERSION_FR", reports_in_original)

            self.assertFalse(reports_in_original.exists())

    def test_reconstructs_safe_file_when_another_file_is_blocked_by_simulation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_partial_plan_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_dir = game_root / "PBS"
            pbs_dir.mkdir(parents=True)
            prepare_essentials_game(game_root)
            (pbs_dir / "safe.txt").write_text("Name = Hello\n", encoding="utf-8")
            (pbs_dir / "blocked.txt").write_text("Name = Actual\n", encoding="utf-8")

            safe_relative = "PBS/safe.txt"
            blocked_relative = "PBS/blocked.txt"
            csv_path = base / "project.csv"
            write_projects(
                csv_path,
                [
                    pbs_row(safe_relative),
                    pbs_row(blocked_relative, source="Expected", translation="Attendu"),
                ],
            )
            plan = simulate_plan(build_plan(game_root, csv_path))

            self.assertEqual(1, plan.counts().get("applicable", 0))
            self.assertEqual(1, plan.counts().get("blocked", 0))

            target = base / "game_VERSION_FR"
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(1, result.applied)
            self.assertIn("Bonjour", (target / safe_relative).read_text(encoding="utf-8"))
            self.assertIn("Actual", (target / blocked_relative).read_text(encoding="utf-8"))

    def test_refuses_to_copy_a_game_containing_a_directory_link(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_linked_game_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            prepare_essentials_game(game_root)
            outside = base / "outside"
            outside.mkdir()
            (outside / "private.txt").write_text("outside", encoding="utf-8")
            linked = game_root / "LinkedOutside"
            try:
                linked.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Création de lien indisponible sur ce système : {exc}")

            relative = "PBS/fixture.txt"
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row(relative))
            plan = simulate_plan(build_plan(game_root, csv_path))
            target = base / "game_VERSION_FR"

            with self.assertRaisesRegex(ReconstructionError, "Lien symbolique|jonction"):
                reconstruct_copy(plan, target, base / "reports")

            self.assertFalse(target.exists())

    def test_copy_policy_blocks_a_detected_link_before_creating_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_link_policy_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            prepare_essentials_game(game_root)
            (game_root / "LinkedOutside").mkdir()

            relative = "PBS/fixture.txt"
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row(relative))
            plan = simulate_plan(build_plan(game_root, csv_path))
            target = base / "game_VERSION_FR"

            with patch(
                "reconstruction_engine._is_link_or_junction",
                side_effect=lambda path: Path(path).name == "LinkedOutside",
            ):
                with self.assertRaisesRegex(ReconstructionError, "Lien symbolique|jonction"):
                    reconstruct_copy(plan, target, base / "reports")

            self.assertFalse(target.exists())

    def test_unknown_structure_cannot_build_a_reconstruction_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_unknown_rebuild_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "unknown_game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            original = pbs_path.read_bytes()
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row("PBS/fixture.txt"))

            with self.assertRaisesRegex(ReconstructionError, "adaptateur Pokémon Essentials"):
                build_plan(game_root, csv_path)

            self.assertEqual(original, pbs_path.read_bytes())

    def test_adapter_is_revalidated_immediately_before_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_adapter_recheck_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            prepare_essentials_game(game_root)
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row("PBS/fixture.txt"))
            plan = simulate_plan(build_plan(game_root, csv_path))

            (game_root / "Game.exe").unlink()
            target = base / "game_VERSION_FR"
            reports = base / "reports"

            with self.assertRaisesRegex(ReconstructionError, "adaptateur Pokémon Essentials"):
                reconstruct_copy(plan, target, reports)

            self.assertFalse(target.exists())
            self.assertFalse(reports.exists())

    def test_reserved_generated_file_is_never_overwritten_in_the_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_reserved_output_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            pbs_path = game_root / "PBS" / "fixture.txt"
            pbs_path.parent.mkdir(parents=True)
            pbs_path.write_text("Name = Hello\n", encoding="utf-8")
            prepare_essentials_game(game_root)
            reserved = game_root / "LANCER_VERSION_FR.bat"
            reserved.write_bytes(b"original synthetic launcher")
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row("PBS/fixture.txt"))
            plan = simulate_plan(build_plan(game_root, csv_path))
            target = base / "game_VERSION_FR"

            with self.assertRaisesRegex(ReconstructionError, "fichier réservé"):
                reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(b"original synthetic launcher", reserved.read_bytes())
            self.assertFalse(target.exists())

    def test_redirected_game_root_is_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_root_link_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "game"
            prepare_essentials_game(game_root)
            csv_path = base / "project.csv"
            write_project(csv_path, pbs_row("PBS/fixture.txt"))

            with patch(
                "reconstruction_engine._is_link_or_junction",
                side_effect=lambda path: Path(path) == game_root,
            ):
                with self.assertRaisesRegex(ReconstructionError, "lien symbolique|jonction"):
                    build_plan(game_root, csv_path)


if __name__ == "__main__":
    unittest.main()
