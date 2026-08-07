from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Pokemon_Fangame_Translator import merge_project_rows, project_directory_for_game


class ProjectMergeTests(unittest.TestCase):
    def test_project_directory_is_never_inside_original_game(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_project_path_") as temp_dir:
            game_root = Path(temp_dir) / "Jeu"
            game_root.mkdir()

            with self.assertRaisesRegex(ValueError, "à l'intérieur"):
                project_directory_for_game(game_root, game_root / "Projets")

    def test_project_directory_can_be_created_beside_original_game(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_project_path_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "Jeu"
            game_root.mkdir()

            project = project_directory_for_game(game_root, base / "Projets")

            self.assertEqual(base / "Projets", project.parent)
            self.assertFalse(project.is_relative_to(game_root))

    def test_project_directory_preserves_the_requested_windows_path_spelling(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_project_spelling_") as temp_dir:
            base = Path(temp_dir)
            game_root = base / "Jeu"
            projects_root = base / "Projets"
            short_projects_root = base / "PROJET~1"
            game_root.mkdir()
            original_resolve = Path.resolve

            def fake_resolve(path: Path, *args, **kwargs) -> Path:
                if path == projects_root:
                    return short_projects_root
                if path.parent == projects_root:
                    return short_projects_root / path.name
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, "resolve", autospec=True, side_effect=fake_resolve):
                project = project_directory_for_game(game_root, projects_root)

            self.assertEqual(projects_root, project.parent)

    def test_preserves_translation_when_stable_id_and_source_match(self) -> None:
        new_row = {
            "id_stable": "ligne-1",
            "type": "Dialogue",
            "fichier": "Data/Map001.rxdata",
            "carte_id": "1",
            "evenement_id": "2",
            "page": "1",
            "commande": "3",
            "sous_index": "",
            "texte_source": "Hello!",
            "traduction_fr": "",
            "statut": "À traduire",
        }
        previous = dict(new_row)
        previous.update(
            {
                "traduction_fr": "Bonjour !",
                "statut": "Accepté",
                "niveau_relecture": "pret",
            }
        )

        with tempfile.TemporaryDirectory(prefix="pft_test_merge_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(previous), delimiter=";")
                writer.writeheader()
                writer.writerow(previous)

            rows, preserved, fields = merge_project_rows([new_row], csv_path)

        self.assertEqual(1, preserved)
        self.assertEqual("Bonjour !", rows[0]["traduction_fr"])
        self.assertEqual("Accepté", rows[0]["statut"])
        self.assertIn("niveau_relecture", fields)

    def test_does_not_preserve_translation_when_source_changed_at_same_location(self) -> None:
        new_row = {
            "id_stable": "ligne-1",
            "type": "Dialogue",
            "fichier": "Data/Map001.rxdata",
            "carte_id": "1",
            "evenement_id": "2",
            "page": "1",
            "commande": "3",
            "sous_index": "",
            "texte_source": "A completely new sentence.",
            "traduction_fr": "",
            "statut": "À traduire",
        }
        previous = dict(new_row)
        previous.update(
            {
                "texte_source": "The old sentence.",
                "traduction_fr": "L'ancienne phrase.",
                "statut": "Accepté",
            }
        )

        with tempfile.TemporaryDirectory(prefix="pft_test_changed_source_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(previous), delimiter=";")
                writer.writeheader()
                writer.writerow(previous)

            rows, preserved, _fields = merge_project_rows([new_row], csv_path)

        self.assertEqual(0, preserved)
        self.assertEqual("", rows[0]["traduction_fr"])
        self.assertEqual("À traduire", rows[0]["statut"])
        self.assertEqual("source_modifiee", rows[0]["niveau_relecture"])
        self.assertIn("ancienne traduction non réutilisée", rows[0]["alertes_relecture"])


if __name__ == "__main__":
    unittest.main()
