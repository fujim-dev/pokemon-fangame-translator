from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import safe_io
from repair import (
    RepairError,
    apply_repair_plan,
    apply_repair_plan_transactional,
    plan_csv_repairs,
    restore_csv_backup,
    restore_csv_backup_transactional,
    save_repair_plan,
)
from extraction_project import (
    BASELINE_CSV_NAME,
    EXTRACTION_MANIFEST_NAME,
    EXTRACTION_REPORT_NAME,
    PROJECT_CSV_NAME,
)
from project_identity import (
    PROJECT_METADATA_NAME,
    build_project_identity_bytes,
    write_project_identity,
)
from translation_project import (
    RESUME_STATE_FORMAT,
    TRANSLATION_STATE_FORMAT,
    TRANSLATION_STATE_NAME,
    TranslationProjectError,
    TranslationProjectInUseError,
    TranslationProjectSession,
)


FIELDS = [
    "id_stable",
    "type",
    "fichier",
    "carte_id",
    "carte_nom",
    "evenement_id",
    "evenement_nom",
    "page",
    "commande",
    "sous_index",
    "texte_source",
    "traduction_fr",
    "codes_proteges",
    "statut",
    "adaptateur",
    "source_sha256",
    "source_manifest_sha256",
    "niveau_relecture",
]


def csv_payload(
    source_manifest: str,
    source_sha256: str,
    *,
    translation: str = "",
    source: str = "Hello",
) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter=";")
    writer.writeheader()
    writer.writerow(
        {
            "id_stable": "synthetic-occurrence",
            "type": "PBS — Name",
            "fichier": "PBS/fixture.txt",
            "evenement_id": "GLOBAL",
            "evenement_nom": "GLOBAL",
            "commande": "Name",
            "sous_index": "1",
            "texte_source": source,
            "traduction_fr": translation,
            "statut": "Accepté" if translation else "À traduire",
            "adaptateur": "pokemon_essentials",
            "source_sha256": source_sha256,
            "source_manifest_sha256": source_manifest,
            "niveau_relecture": "pret" if translation else "non_traduit",
        }
    )
    return handle.getvalue().encode("utf-8-sig")


def prepare_verified_project(
    base: Path,
    *,
    source: str = "Hello",
    translation: str = "",
) -> tuple[Path, Path, bytes]:
    game_root = base / "game"
    project_dir = base / "project"
    game_root.mkdir()
    project_dir.mkdir()
    source_manifest = "a" * 64
    source_sha256 = "b" * 64
    initial_csv = csv_payload(
        source_manifest,
        source_sha256,
        source=source,
        translation=translation,
    )
    report = b"synthetic extraction report"
    csv_path = project_dir / PROJECT_CSV_NAME
    csv_path.write_bytes(initial_csv)
    (project_dir / BASELINE_CSV_NAME).write_bytes(initial_csv)
    (project_dir / EXTRACTION_REPORT_NAME).write_bytes(report)
    run_id = "synthetic-extraction"
    manifest = {
        "format": "pft_essentials_extraction_v1",
        "extraction_id": run_id,
        "adapter_id": "pokemon_essentials",
        "adapter_version": "21.1",
        "game_root": str(game_root.resolve()),
        "source_manifest_sha256": source_manifest,
        "source_count": 1,
        "sources": [
            {
                "kind": "pbs",
                "relative_path": "PBS/fixture.txt",
                "size": 12,
                "sha256": source_sha256,
            }
        ],
        "row_count": 1,
        "project_csv_name": PROJECT_CSV_NAME,
        "baseline_csv_name": BASELINE_CSV_NAME,
        "report_name": EXTRACTION_REPORT_NAME,
        "csv_sha256": hashlib.sha256(initial_csv).hexdigest(),
        "report_sha256": hashlib.sha256(report).hexdigest(),
    }
    manifest_payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
    (project_dir / EXTRACTION_MANIFEST_NAME).write_bytes(manifest_payload)
    (project_dir / PROJECT_METADATA_NAME).write_bytes(
        build_project_identity_bytes(
            game_root,
            adapter_id="pokemon_essentials",
            adapter_version="21.1",
            source_manifest_sha256=source_manifest,
            extraction_manifest_name=EXTRACTION_MANIFEST_NAME,
            extraction_manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
            extraction_id=run_id,
            extracted_csv_sha256=hashlib.sha256(initial_csv).hexdigest(),
        )
    )
    return game_root, csv_path, initial_csv


class TranslationProjectLifecycleTests(unittest.TestCase):
    def test_verified_save_binds_csv_state_and_resume_atomically(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_project_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(Path(temporary))
            translated = csv_payload("a" * 64, "b" * 64, translation="Bonjour")

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                self.assertTrue(session.writable)
                session.save(
                    translated,
                    resume_state={
                        "active": True,
                        "total": 10,
                        "completed": 4,
                        "remaining": 6,
                    },
                )
                session.check_current()
                resume = session.read_resume_state()

            state = json.loads(
                (csv_path.parent / TRANSLATION_STATE_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(TRANSLATION_STATE_FORMAT, state["format"])
            self.assertEqual(hashlib.sha256(translated).hexdigest(), state["csv_sha256"])
            self.assertEqual(RESUME_STATE_FORMAT, resume["format"])
            self.assertEqual(state["csv_sha256"], resume["csv_sha256"])
            self.assertEqual(translated, csv_path.read_bytes())

    def test_external_csv_change_is_preserved_and_blocks_save(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_conflict_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(Path(temporary))
            session = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            external = csv_payload("a" * 64, "b" * 64, translation="Externe")
            csv_path.write_bytes(external)

            with self.assertRaisesRegex(TranslationProjectError, "changé|modifié"):
                session.save(csv_payload("a" * 64, "b" * 64, translation="Studio"))

            self.assertEqual(external, csv_path.read_bytes())
            self.assertFalse((csv_path.parent / TRANSLATION_STATE_NAME).exists())
            session.close()

    def test_same_bytes_replacement_is_detected_by_file_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_replace_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(Path(temporary))
            session = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            replacement = csv_path.with_name("replacement.csv")
            replacement.write_bytes(initial)
            replacement.replace(csv_path)

            with self.assertRaisesRegex(TranslationProjectError, "modifié|remplacé"):
                session.check_current()

            self.assertEqual(initial, csv_path.read_bytes())
            session.close()

    def test_second_studio_session_is_refused_until_first_closes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_lock_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(Path(temporary))
            first = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            with self.assertRaises(TranslationProjectInUseError):
                TranslationProjectSession(
                    csv_path,
                    game_root=game_root,
                    expected_adapter_id="pokemon_essentials",
                )
            first.close()

            reopened = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            self.assertTrue(reopened.writable)
            reopened.close()

    def test_legacy_project_is_read_only_and_keeps_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_legacy_") as temporary:
            base = Path(temporary)
            game_root = base / "game"
            project = base / "project"
            game_root.mkdir()
            project.mkdir()
            csv_path = project / PROJECT_CSV_NAME
            legacy = b"id_stable;type;fichier;texte_source;traduction_fr;statut\r\n1;PBS;PBS/a.txt;Hello;;A traduire\r\n"
            csv_path.write_bytes(legacy)
            write_project_identity(
                project,
                game_root,
                adapter_id="pokemon_essentials",
                adapter_version="21.1",
            )

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                self.assertFalse(session.writable)
                self.assertIn("nouvelle extraction", session.read_only_reason)
                with self.assertRaises(TranslationProjectError):
                    session.save(legacy)

            self.assertEqual(legacy, csv_path.read_bytes())

    def test_immutable_source_change_is_never_saved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_structure_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(Path(temporary))
            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                changed_source = csv_payload(
                    "a" * 64,
                    "b" * 64,
                    translation="Bonjour",
                    source="Changed source",
                )
                with self.assertRaisesRegex(TranslationProjectError, "occurrence|source"):
                    session.save(changed_source)

            self.assertEqual(initial, csv_path.read_bytes())

    def test_manifest_change_while_open_blocks_save_without_touching_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_manifest_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(Path(temporary))
            session = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            manifest = csv_path.parent / EXTRACTION_MANIFEST_NAME
            manifest.write_bytes(manifest.read_bytes() + b" ")

            with self.assertRaisesRegex(TranslationProjectError, "manifeste|changé"):
                session.save(csv_payload("a" * 64, "b" * 64, translation="Bonjour"))

            self.assertEqual(initial, csv_path.read_bytes())
            self.assertFalse((csv_path.parent / TRANSLATION_STATE_NAME).exists())
            session.close()

    def test_missing_associated_report_keeps_project_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_report_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(Path(temporary))
            (csv_path.parent / EXTRACTION_REPORT_NAME).unlink()

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                self.assertFalse(session.writable)
                self.assertIn("rapport", session.read_only_reason.casefold())
                with self.assertRaises(TranslationProjectError):
                    session.save(initial)

            self.assertEqual(initial, csv_path.read_bytes())

    def test_preupgrade_translated_csv_without_state_requires_reextraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_preupgrade_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(Path(temporary))
            translated = csv_payload("a" * 64, "b" * 64, translation="Ancienne traduction")
            csv_path.write_bytes(translated)

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                self.assertFalse(session.writable)
                self.assertIn("aucun état", session.read_only_reason)

            self.assertEqual(translated, csv_path.read_bytes())

    def test_resume_bound_to_another_csv_state_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_resume_conflict_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(Path(temporary))
            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                session.save(
                    csv_payload("a" * 64, "b" * 64, translation="Bonjour"),
                    resume_state={
                        "active": True,
                        "total": 3,
                        "completed": 1,
                        "remaining": 2,
                    },
                )
            resume_path = csv_path.parent / "etat_traduction.json"
            resume = json.loads(resume_path.read_text(encoding="utf-8"))
            resume["csv_sha256"] = "0" * 64
            resume_path.write_text(json.dumps(resume), encoding="utf-8")

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as reopened:
                self.assertFalse(reopened.writable)
                self.assertIn("reprise", reopened.read_only_reason.casefold())

    def test_late_bundle_failure_restores_csv_state_and_resume_exactly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_translation_rollback_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(Path(temporary))
            session = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            session.save(
                csv_payload("a" * 64, "b" * 64, translation="Première"),
                resume_state={"active": True, "total": 2, "completed": 1, "remaining": 1},
            )
            paths = (
                csv_path,
                csv_path.parent / TRANSLATION_STATE_NAME,
                csv_path.parent / "etat_traduction.json",
            )
            previous = {path: path.read_bytes() for path in paths}
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_replace(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("late synthetic failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_replace):
                with self.assertRaisesRegex(TranslationProjectError, "état précédent"):
                    session.save(
                        csv_payload("a" * 64, "b" * 64, translation="Deuxième"),
                        resume_state={
                            "active": False,
                            "total": 2,
                            "completed": 2,
                            "remaining": 0,
                        },
                    )

            self.assertGreaterEqual(calls, 3)
            self.assertEqual(previous, {path: path.read_bytes() for path in paths})
            session.check_current()
            session.close()

    def test_transactional_repair_publishes_csv_state_backup_and_journal_together(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_transactional_repair_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(
                Path(temporary),
                source=r"\c[1]Hello",
                translation="Bonjour",
            )
            reports = csv_path.parent / "Rapports"
            plan = save_repair_plan(
                plan_csv_repairs(csv_path),
                reports / "PLAN_REPARATIONS_TEST.json",
            )

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                result = apply_repair_plan_transactional(session, plan)
                session.check_current()
                resume = session.read_resume_state()

            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle, delimiter=";"))
            state = json.loads(
                (csv_path.parent / TRANSLATION_STATE_NAME).read_text(encoding="utf-8")
            )
            journal = json.loads(Path(result.journal_path).read_text(encoding="utf-8"))

            self.assertEqual(r"\c[1]Bonjour", row["traduction_fr"])
            self.assertEqual("À vérifier", row["statut"])
            self.assertEqual(initial, Path(result.backup_path).read_bytes())
            self.assertEqual(hashlib.sha256(csv_path.read_bytes()).hexdigest(), state["csv_sha256"])
            self.assertFalse(resume["active"])
            self.assertEqual(state["csv_sha256"], resume["csv_sha256"])
            self.assertEqual("transaction_provenance_essentials", journal["mode"])
            self.assertNotIn("Hello", Path(result.journal_path).read_text(encoding="utf-8"))

    def test_legacy_direct_repair_and_restore_are_refused_for_essentials_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_direct_repair_guard_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(
                Path(temporary),
                source=r"\c[1]Hello",
                translation="Bonjour",
            )
            del game_root
            reports = csv_path.parent / "Rapports"
            backups = csv_path.parent / "Sauvegardes"
            plan = save_repair_plan(
                plan_csv_repairs(csv_path),
                reports / "PLAN_REPARATIONS_TEST.json",
            )
            backups.mkdir()
            backup = backups / "avant_reparation_test.csv"
            backup.write_bytes(initial)

            with self.assertRaisesRegex(RepairError, "service transactionnel"):
                apply_repair_plan(plan, backup_dir=backups, report_dir=reports)
            with self.assertRaisesRegex(RepairError, "service transactionnel"):
                restore_csv_backup(
                    csv_path,
                    backup,
                    backup_dir=backups,
                    report_dir=reports,
                )

            self.assertEqual(initial, csv_path.read_bytes())

    def test_external_csv_change_after_repair_plan_is_preserved_and_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_external_change_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(
                Path(temporary),
                source=r"\c[1]Hello",
                translation="Bonjour",
            )
            plan = save_repair_plan(
                plan_csv_repairs(csv_path),
                csv_path.parent / "Rapports" / "PLAN_REPARATIONS_TEST.json",
            )
            session = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            external = csv_payload(
                "a" * 64,
                "b" * 64,
                source=r"\c[1]Hello",
                translation="Modification externe",
            )
            csv_path.write_bytes(external)

            with self.assertRaisesRegex(RepairError, "modifié|remplacé|changé"):
                apply_repair_plan_transactional(session, plan)

            self.assertEqual(external, csv_path.read_bytes())
            self.assertFalse((csv_path.parent / TRANSLATION_STATE_NAME).exists())
            self.assertFalse((csv_path.parent / "Sauvegardes").exists())
            session.close()

    def test_restore_refuses_backup_from_another_extraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_restore_wrong_extraction_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(Path(temporary))
            backups = csv_path.parent / "Sauvegardes"
            backups.mkdir()
            wrong = backups / "avant_reparation_autre_extraction.csv"
            wrong.write_bytes(
                csv_payload(
                    "a" * 64,
                    "b" * 64,
                    source="Another source",
                    translation="Autre",
                )
            )

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                with self.assertRaisesRegex(RepairError, "autre extraction|sources"):
                    restore_csv_backup_transactional(session, wrong)
                session.check_current()

            self.assertEqual(initial, csv_path.read_bytes())
            self.assertFalse((csv_path.parent / TRANSLATION_STATE_NAME).exists())
            self.assertFalse((csv_path.parent / "Rapports").exists())

    def test_transactional_restore_refuses_backup_outside_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_restore_outside_project_") as temporary:
            base = Path(temporary)
            game_root, csv_path, initial = prepare_verified_project(base)
            outside = base / "outside"
            outside.mkdir()
            external_backup = outside / "avant_reparation_externe.csv"
            external_backup.write_bytes(initial)
            (csv_path.parent / "Sauvegardes").mkdir()

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                with self.assertRaisesRegex(RepairError, "n'appartient pas au projet"):
                    restore_csv_backup_transactional(session, external_backup)
                session.check_current()

            self.assertEqual(initial, csv_path.read_bytes())
            self.assertFalse((csv_path.parent / TRANSLATION_STATE_NAME).exists())

    def test_restore_refuses_redirected_backup_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_restore_redirected_") as temporary:
            base = Path(temporary)
            game_root, csv_path, initial = prepare_verified_project(base)
            redirected = csv_path.parent / "Sauvegardes"
            redirected.mkdir()
            backup = redirected / "avant_reparation_redirigee.csv"
            backup.write_bytes(initial)

            real_redirect_check = safe_io._is_link_or_junction

            def marks_backup_directory_as_redirected(path: Path) -> bool:
                return path == redirected or real_redirect_check(path)

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                with patch(
                    "safe_io._is_link_or_junction",
                    side_effect=marks_backup_directory_as_redirected,
                ):
                    with self.assertRaisesRegex(RepairError, "redirigée|instable"):
                        restore_csv_backup_transactional(session, backup)
                session.check_current()

            self.assertEqual(initial, csv_path.read_bytes())

    def test_transactional_restore_rebinds_state_and_keeps_safety_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_transactional_restore_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(
                Path(temporary),
                source=r"\c[1]Hello",
                translation="Bonjour",
            )
            plan = save_repair_plan(
                plan_csv_repairs(csv_path),
                csv_path.parent / "Rapports" / "PLAN_REPARATIONS_TEST.json",
            )

            with TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                repaired = apply_repair_plan_transactional(session, plan)
                repaired_payload = csv_path.read_bytes()
                restored = restore_csv_backup_transactional(
                    session, Path(repaired.backup_path)
                )
                session.check_current()
                resume = session.read_resume_state()

            state = json.loads(
                (csv_path.parent / TRANSLATION_STATE_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(initial, csv_path.read_bytes())
            self.assertEqual(repaired_payload, Path(restored.safety_backup_path).read_bytes())
            self.assertEqual(hashlib.sha256(initial).hexdigest(), state["csv_sha256"])
            self.assertEqual(2, state["revision"])
            self.assertFalse(resume["active"])
            self.assertEqual(state["csv_sha256"], resume["csv_sha256"])

    def test_backup_changed_during_restore_rolls_back_all_published_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_restore_guard_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(
                Path(temporary),
                source=r"\c[1]Hello",
                translation="Bonjour",
            )
            plan = save_repair_plan(
                plan_csv_repairs(csv_path),
                csv_path.parent / "Rapports" / "PLAN_REPARATIONS_TEST.json",
            )
            session = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            repaired = apply_repair_plan_transactional(session, plan)
            backup = Path(repaired.backup_path)
            core_paths = (
                csv_path,
                csv_path.parent / TRANSLATION_STATE_NAME,
                csv_path.parent / "etat_traduction.json",
            )
            before = {path: path.read_bytes() for path in core_paths}
            reports_before = set((csv_path.parent / "Rapports").iterdir())
            backups_before = set((csv_path.parent / "Sauvegardes").iterdir())
            real_replace = safe_io._replace_file
            calls = 0

            def change_backup_during_commit(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    backup.write_bytes(backup.read_bytes() + b"external")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=change_backup_during_commit):
                with self.assertRaisesRegex(RepairError, "état précédent|annulée"):
                    restore_csv_backup_transactional(session, backup)

            self.assertEqual(before, {path: path.read_bytes() for path in core_paths})
            self.assertEqual(reports_before, set((csv_path.parent / "Rapports").iterdir()))
            self.assertEqual(backups_before, set((csv_path.parent / "Sauvegardes").iterdir()))
            session.check_current()
            session.close()

    def test_incomplete_transactional_repair_rollback_preserves_recovery_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_repair_recovery_") as temporary:
            game_root, csv_path, initial = prepare_verified_project(
                Path(temporary),
                source=r"\c[1]Hello",
                translation="Bonjour",
            )
            session = TranslationProjectSession(
                csv_path,
                game_root=game_root,
                expected_adapter_id="pokemon_essentials",
            )
            session.save(
                initial,
                resume_state={"active": False, "total": 0, "completed": 0, "remaining": 0},
            )
            plan = save_repair_plan(
                plan_csv_repairs(csv_path),
                csv_path.parent / "Rapports" / "PLAN_REPARATIONS_TEST.json",
            )
            real_replace = safe_io._replace_file
            calls = 0

            def fail_publication_then_rollback(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls in {2, 3}:
                    raise OSError("synthetic publication/rollback failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_publication_then_rollback):
                with self.assertRaisesRegex(
                    RepairError, "rollback est incomplet|Détails de récupération"
                ):
                    apply_repair_plan_transactional(session, plan)

            recovery_files = list(csv_path.parent.glob(".*.pft-bundle-old-*.tmp"))
            self.assertTrue(recovery_files)
            self.assertTrue(any(path.read_bytes() for path in recovery_files))
            session.close()

    def test_diagnostic_refresh_preserves_verified_identity_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_identity_stable_") as temporary:
            game_root, csv_path, _initial = prepare_verified_project(Path(temporary))
            identity_path = csv_path.parent / PROJECT_METADATA_NAME
            before = identity_path.read_bytes()

            write_project_identity(
                csv_path.parent,
                game_root,
                adapter_id="pokemon_essentials",
                adapter_version="21.1",
            )

            self.assertEqual(before, identity_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
