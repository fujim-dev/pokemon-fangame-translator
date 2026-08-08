from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repair import (
    RepairError,
    apply_repair_plan,
    plan_csv_repairs,
    restore_csv_backup,
    save_repair_plan,
)
from repair.rollback import atomic_write_bytes


FIELDS = [
    "id_stable",
    "type",
    "texte_source",
    "traduction_fr",
    "statut",
    "niveau_relecture",
    "alertes_relecture",
    "origine_traduction",
]


def write_fixture(path: Path) -> None:
    rows = [
        {
            "id_stable": "safe-leading",
            "type": "Dialogue",
            "texte_source": r"\c[1]Hello",
            "traduction_fr": "Bonjour",
            "statut": "Bloqué",
        },
        {
            "id_stable": "ambiguous-internal",
            "type": "Dialogue",
            "texte_source": r"Hello \c[1]trainer",
            "traduction_fr": "Bonjour dresseur",
            "statut": "Bloqué",
        },
        {
            "id_stable": "already-valid",
            "type": "Dialogue",
            "texte_source": r"\PN, hello!",
            "traduction_fr": r"\PN, bonjour !",
            "statut": "Prêt",
        },
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SafeRepairTests(unittest.TestCase):
    def test_plan_separates_safe_and_ambiguous_repairs_without_dialogues(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_plan_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)

            plan = plan_csv_repairs(csv_path)
            saved = save_repair_plan(plan, base / "reports" / "plan.json")
            plan_text = Path(saved.saved_path).read_text(encoding="utf-8")

        self.assertEqual(1, len(saved.safe_actions))
        self.assertEqual(1, len(saved.human_actions))
        self.assertEqual("safe-leading", saved.safe_actions[0].row_id)
        self.assertEqual("ambiguous-internal", saved.human_actions[0].row_id)
        self.assertEqual("verification_humaine_requise", saved.human_actions[0].decision)
        self.assertNotIn("Hello", plan_text)
        self.assertNotIn("Bonjour", plan_text)
        self.assertNotIn("dresseur", plan_text)

    def test_application_creates_backup_and_keeps_ambiguous_row_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_apply_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)
            original_hash = sha256(csv_path)
            saved = save_repair_plan(
                plan_csv_repairs(csv_path),
                base / "reports" / "plan.json",
            )

            result = apply_repair_plan(
                saved,
                backup_dir=base / "backups",
                report_dir=base / "reports",
            )
            rows = {row["id_stable"]: row for row in read_rows(csv_path)}
            journal_text = Path(result.journal_path).read_text(encoding="utf-8")
            backup_hash = sha256(Path(result.backup_path))

        self.assertEqual(1, result.applied)
        self.assertFalse(result.rolled_back)
        self.assertEqual(original_hash, backup_hash)
        self.assertEqual(r"\c[1]Bonjour", rows["safe-leading"]["traduction_fr"])
        self.assertEqual("À vérifier", rows["safe-leading"]["statut"])
        self.assertEqual("Bonjour dresseur", rows["ambiguous-internal"]["traduction_fr"])
        self.assertNotIn("Hello", journal_text)
        self.assertNotIn("Bonjour", journal_text)

    def test_unsaved_plan_cannot_be_applied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_unsaved_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)

            with self.assertRaisesRegex(RepairError, "plan.*enregistr"):
                apply_repair_plan(
                    plan_csv_repairs(csv_path),
                    backup_dir=base / "backups",
                    report_dir=base / "reports",
                )

    def test_modified_saved_plan_cannot_be_applied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_tampered_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)
            saved = save_repair_plan(
                plan_csv_repairs(csv_path),
                base / "reports" / "plan.json",
            )
            plan_path = Path(saved.saved_path)
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            payload["actions"][0]["decision"] = "verification_humaine_requise"
            plan_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(RepairError, "plan enregistré.*chang"):
                apply_repair_plan(
                    saved,
                    backup_dir=base / "backups",
                    report_dir=base / "reports",
                )

    def test_validation_failure_rolls_back_exact_original_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_rollback_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)
            original = csv_path.read_bytes()
            saved = save_repair_plan(
                plan_csv_repairs(csv_path),
                base / "reports" / "plan.json",
            )

            def fail_validation(_path: Path) -> None:
                raise ValueError("synthetic validation failure")

            with self.assertRaisesRegex(RepairError, "rollback"):
                apply_repair_plan(
                    saved,
                    backup_dir=base / "backups",
                    report_dir=base / "reports",
                    post_write_validator=fail_validation,
                )

            failure_reports = list((base / "reports").glob("RAPPORT_REPARATION_ECHEC_*.json"))
            rolled_back_bytes = csv_path.read_bytes()

        self.assertEqual(original, rolled_back_bytes)
        self.assertEqual(1, len(failure_reports))

    def test_explicit_restore_is_itself_reversible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_restore_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)
            original = csv_path.read_bytes()
            saved = save_repair_plan(
                plan_csv_repairs(csv_path),
                base / "reports" / "plan.json",
            )
            result = apply_repair_plan(
                saved,
                backup_dir=base / "backups",
                report_dir=base / "reports",
            )
            repaired = csv_path.read_bytes()

            restoration = restore_csv_backup(
                csv_path,
                Path(result.backup_path),
                backup_dir=base / "backups",
                report_dir=base / "reports",
            )
            restored = csv_path.read_bytes()
            safety_backup = Path(restoration.safety_backup_path).read_bytes()

        self.assertEqual(original, restored)
        self.assertEqual(repaired, safety_backup)

    def test_journal_failure_also_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_journal_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)
            original = csv_path.read_bytes()
            saved = save_repair_plan(
                plan_csv_repairs(csv_path),
                base / "reports" / "plan.json",
            )

            with patch("repair.engine.atomic_write_json", side_effect=OSError("synthetic")):
                with self.assertRaisesRegex(RepairError, "journal.*rollback"):
                    apply_repair_plan(
                        saved,
                        backup_dir=base / "backups",
                        report_dir=base / "reports",
                    )

            restored = csv_path.read_bytes()

        self.assertEqual(original, restored)

    def test_restore_report_failure_rolls_back_to_the_pre_restore_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_restore_journal_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)
            saved = save_repair_plan(
                plan_csv_repairs(csv_path),
                base / "reports" / "plan.json",
            )
            result = apply_repair_plan(
                saved,
                backup_dir=base / "backups",
                report_dir=base / "reports",
            )
            repaired = csv_path.read_bytes()

            with patch("repair.rollback.atomic_write_json", side_effect=OSError("synthetic")):
                with self.assertRaisesRegex(RepairError, "journal.*rollback"):
                    restore_csv_backup(
                        csv_path,
                        Path(result.backup_path),
                        backup_dir=base / "backups",
                        report_dir=base / "reports",
                    )

            after_failure = csv_path.read_bytes()

        self.assertEqual(repaired, after_failure)

    def test_unique_atomic_temporary_file_preserves_legacy_tmp_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_atomic_name_") as temp_dir:
            base = Path(temp_dir)
            destination = base / "project.csv"
            legacy_temporary = base / "project.csv.tmp"
            legacy_temporary.write_bytes(b"unrelated sentinel")

            atomic_write_bytes(destination, b"new payload")

            self.assertEqual(b"new payload", destination.read_bytes())
            self.assertEqual(b"unrelated sentinel", legacy_temporary.read_bytes())

    def test_repair_preserves_preexisting_legacy_repairtmp_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_temp_name_") as temp_dir:
            base = Path(temp_dir)
            csv_path = base / "project.csv"
            write_fixture(csv_path)
            legacy_temporary = base / "project.csv.repairtmp"
            legacy_temporary.write_bytes(b"unrelated sentinel")
            saved = save_repair_plan(
                plan_csv_repairs(csv_path),
                base / "reports" / "plan.json",
            )

            apply_repair_plan(
                saved,
                backup_dir=base / "backups",
                report_dir=base / "reports",
            )

            self.assertEqual(b"unrelated sentinel", legacy_temporary.read_bytes())


if __name__ == "__main__":
    unittest.main()
