from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Pokemon_Fangame_Translator import (
    ProjectMergeError,
    backup_project_csv,
    merge_project_rows,
    project_directory_for_game,
    write_project_csv,
)
from extraction_project import EXTRACTION_MANIFEST_NAME
from project_identity import (
    ProjectIdentityError,
    read_project_identity,
    write_project_identity,
)


class ProjectMergeTests(unittest.TestCase):
    def test_diagnostic_identity_refresh_preserves_verified_extraction_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_identity_binding_") as temp_dir:
            project = Path(temp_dir) / "project"
            game = Path(temp_dir) / "game"
            project.mkdir()
            game.mkdir()
            csv_path = project / "textes_structures.csv"
            csv_path.write_text("synthetic csv", encoding="utf-8")
            source_manifest = "a" * 64
            csv_sha256 = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            run_id = "synthetic-run"
            manifest = {
                "format": "pft_essentials_extraction_v1",
                "extraction_id": run_id,
                "adapter_id": "pokemon_essentials",
                "game_root": str(game.resolve()),
                "source_manifest_sha256": source_manifest,
                "csv_sha256": csv_sha256,
            }
            manifest_payload = json.dumps(manifest).encode("utf-8")
            manifest_path = project / EXTRACTION_MANIFEST_NAME
            manifest_path.write_bytes(manifest_payload)
            manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
            write_project_identity(
                project,
                game,
                adapter_id="pokemon_essentials",
                adapter_version="21.1",
                source_manifest_sha256=source_manifest,
                extraction_manifest_name=EXTRACTION_MANIFEST_NAME,
                extraction_manifest_sha256=manifest_sha256,
                extraction_id=run_id,
                extracted_csv_sha256=csv_sha256,
            )

            write_project_identity(
                project,
                game,
                adapter_id="pokemon_essentials",
                adapter_version="21.1",
            )
            identity = read_project_identity(
                csv_path,
                game,
                expected_adapter_id="pokemon_essentials",
            )

            self.assertEqual(source_manifest, identity.source_manifest_sha256)
            self.assertEqual(manifest_sha256, identity.extraction_manifest_sha256)

            manifest_path.write_bytes(manifest_payload + b"\n")
            with self.assertRaisesRegex(ProjectIdentityError, "ne correspond plus"):
                read_project_identity(
                    csv_path,
                    game,
                    expected_adapter_id="pokemon_essentials",
                )

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

    def test_flux_occurrence_fields_and_exact_text_survive_common_csv_roundtrip(self) -> None:
        source = "\\pnFirst line.\n<color=blue>Second line!</color>"
        new_row = {
            "id_stable": "a" * 64,
            "type": "Dialogue",
            "fichier": "Data/Map001.rxdata",
            "carte_id": "1",
            "carte_nom": "Test",
            "evenement_id": "2",
            "evenement_nom": "Event",
            "page": "1",
            "commande": "3",
            "sous_index": "lignes:2",
            "texte_source": source,
            "traduction_fr": "",
            "codes_proteges": "\\pn",
            "statut": "À traduire",
            "adaptateur": "pokemon_flux",
            "conteneur": "Data/Data_0.fpk",
            "source_flux": "map_events",
            "chemin_structurel": '["events",2,"pages",0,"commands",3,"dialogue",2]',
            "empreinte_source": "b" * 64,
            "empreinte_texte_csv": "c" * 64,
            "empreinte_valeur_actuelle": "",
        }

        rows, preserved, fields = merge_project_rows([new_row], None)
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_csv_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            write_project_csv(csv_path, rows, fields)
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                restored = list(csv.DictReader(handle, delimiter=";"))

        self.assertEqual(0, preserved)
        self.assertEqual(1, len(restored))
        self.assertEqual(source, restored[0]["texte_source"])
        self.assertEqual(new_row["chemin_structurel"], restored[0]["chemin_structurel"])
        self.assertEqual("pokemon_flux", restored[0]["adaptateur"])
        self.assertEqual("b" * 64, restored[0]["empreinte_source"])

    def test_unreadable_existing_project_is_never_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_invalid_project_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            csv_path.write_bytes(b"\xff\xfe\x00invalid synthetic csv")
            original = csv_path.read_bytes()
            new_rows = [{
                "id_stable": "new-row",
                "texte_source": "Hello",
                "traduction_fr": "",
                "statut": "À traduire",
            }]

            with self.assertRaisesRegex(ProjectMergeError, "illisible"):
                merge_project_rows(new_rows, csv_path)

            self.assertEqual(original, csv_path.read_bytes())

    def test_incompatible_existing_project_is_never_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_incompatible_project_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            csv_path.write_text("id_stable;texte_source\nrow;Hello\n", encoding="utf-8")

            with self.assertRaisesRegex(ProjectMergeError, "colonnes manquantes"):
                merge_project_rows(
                    [{"id_stable": "row", "texte_source": "Hello"}],
                    csv_path,
                )

    def test_empty_reextraction_keeps_existing_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_empty_reextract_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            csv_path.write_text(
                "id_stable;texte_source;traduction_fr;statut\nrow;Hello;Bonjour;Accepté\n",
                encoding="utf-8",
            )
            original = csv_path.read_bytes()

            with self.assertRaisesRegex(ProjectMergeError, "aucune ligne"):
                merge_project_rows([], csv_path)

            self.assertEqual(original, csv_path.read_bytes())

    def test_reextraction_backups_are_exact_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_reextract_backup_") as temp_dir:
            csv_path = Path(temp_dir) / "textes_structures.csv"
            csv_path.write_bytes(b"synthetic project bytes")

            first = backup_project_csv(csv_path)
            second = backup_project_csv(csv_path)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            self.assertEqual(csv_path.read_bytes(), first.read_bytes())
            self.assertEqual(csv_path.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
