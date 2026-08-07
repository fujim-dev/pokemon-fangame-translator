from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from Pokemon_Fangame_Translator import merge_project_rows


class ProjectMergeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
