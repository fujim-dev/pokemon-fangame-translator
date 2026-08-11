from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import safe_io
from adapters import ESSENTIALS_V21_1_READONLY_PROFILE, PokemonEssentialsAdapter
from analysis.integrity import compare_snapshots, snapshot_tree
from essentials_phone import (
    COMPILED_PHONE_FILE,
    COMPILED_PHONE_PROOF_FORMAT,
    PHONE_MESSAGES_FILE,
    PHONE_PBS_FILE,
    PHONE_PBS_PROOF_FORMAT,
    PHONE_RUNTIME_PROOF_FORMAT,
    PhoneIntegrityError,
    build_phone_entry_proofs,
    extract_phone_target_texts,
)
from reconstruction_engine import (
    V21_1_PHONE_VALIDATION_SCOPE,
    ReconstructionError,
    build_plan,
    build_v21_1_phone_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from translation_project import (
    TranslationProjectError,
    TranslationProjectSession,
)
from project_test_support import finalize_verified_essentials_project
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


def prepare_phone_project(base: Path):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, phone_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == PHONE_PBS_FILE
        and row["type"] == "PBS — End"
        and row["evenement_id"] == "YOUNGSTER,Synthetic Contact"
    )
    selected["traduction_fr"] = (
        selected["texte_source"] + " [TEST PFT v21.1 PHONE END]"
    )
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_phone"
    csv_path = project / "textes_structures.csv"
    write_extracted_project_csv(csv_path, rows)
    finalize_verified_essentials_project(
        root,
        csv_path,
        adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
        declared_version="21.1",
        version_detection_method=(
            "Game.ini:Game.Title + mkxp.json:windowTitle + "
            "Scripts.rxdata:Settings/Essentials::VERSION"
        ),
    )
    return root, csv_path, selected


def rewrite_csv(csv_path: Path, mutate) -> bytes:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";", strict=True)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    mutate(rows)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


class EssentialsPhoneTests(unittest.TestCase):
    def test_phone_extraction_binds_all_three_exact_structures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_phone_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, phone_validation=True)

            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [row for row in result.rows if row["fichier"] == PHONE_PBS_FILE]

        self.assertEqual(6, len(rows))
        self.assertEqual(
            {"Body": 1, "End": 2, "Intro": 3},
            {
                key: sum(row["commande"] == key for row in rows)
                for key in {row["commande"] for row in rows}
            },
        )
        for row in rows:
            pbs = json.loads(row["pbs_structure"])
            compiled = json.loads(row["pbs_compiled_structure"])
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(PHONE_PBS_PROOF_FORMAT, pbs["format"])
            self.assertEqual(COMPILED_PHONE_PROOF_FORMAT, compiled["format"])
            self.assertEqual(PHONE_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(COMPILED_PHONE_FILE, row["pbs_compiled_file"])
            self.assertEqual(PHONE_MESSAGES_FILE, row["pbs_runtime_file"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])

    def test_phone_private_roundtrip_changes_exactly_three_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_phone_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_phone_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)

            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                self.assertTrue(session.writable, session.read_only_reason)
                session.save(csv_path.read_bytes())
            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as reopened:
                reopened.check_current()

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")

            plan = build_v21_1_phone_validation_plan(root, csv_path)
            self.assertEqual(V21_1_PHONE_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(
                {PHONE_PBS_FILE, COMPILED_PHONE_FILE, PHONE_MESSAGES_FILE},
                set(plan.source_hashes),
            )
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(
                sorted([PHONE_PBS_FILE, COMPILED_PHONE_FILE, PHONE_MESSAGES_FILE]),
                result.modified_files,
            )
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            translated = extract_phone_target_texts(
                (target / PHONE_PBS_FILE).read_bytes(),
                (target / COMPILED_PHONE_FILE).read_bytes(),
                (target / PHONE_MESSAGES_FILE).read_bytes(),
                section=selected["evenement_id"],
                key="End",
                occurrence=int(selected["sous_index"]),
            )
            self.assertEqual((selected["traduction_fr"],) * 3, translated)
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed={
                    PHONE_PBS_FILE,
                    COMPILED_PHONE_FILE,
                    PHONE_MESSAGES_FILE,
                },
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_phone_proofs_refuse_structural_and_format_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_phone_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, phone_validation=True)
            pbs = (root / PHONE_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_PHONE_FILE).read_bytes()
            runtime = (root / PHONE_MESSAGES_FILE).read_bytes()

            compiled_root = load(root / COMPILED_PHONE_FILE)
            list(compiled_root.values())[1].ivars["@end"].append(
                list(compiled_root.values())[1].ivars["@intro"][0]
            )
            bad_compiled = dumps(compiled_root)
            with self.assertRaisesRegex(PhoneIntegrityError, "nombre|correspond"):
                build_phone_entry_proofs(pbs, bad_compiled, runtime)

            runtime_root = load(root / PHONE_MESSAGES_FILE)
            runtime_root[22].pop(next(iter(runtime_root[22])))
            with self.assertRaisesRegex(PhoneIntegrityError, "couvre"):
                build_phone_entry_proofs(pbs, compiled, dumps(runtime_root))

            for bad_pbs in (
                pbs[3:],
                pbs.replace(b"\r\n", b"\n"),
                pbs.replace(b"End = Synthetic trainer end", b"Body = Synthetic trainer end"),
            ):
                with self.subTest(prefix=bad_pbs[:12]), self.assertRaises(PhoneIntegrityError):
                    build_phone_entry_proofs(bad_pbs, compiled, runtime)

    def test_phone_plan_refuses_tampered_proof_and_post_plan_source_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_phone_plan_guard_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_phone_project(base)
            plan = build_v21_1_phone_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            original_proof = item.pbs_runtime_structure
            item.pbs_runtime_structure = "{}"
            with self.assertRaisesRegex(ReconstructionError, "preuve|correspond"):
                simulate_plan(plan)

            item.pbs_runtime_structure = original_proof
            (root / COMPILED_PHONE_FILE).write_bytes(
                (root / COMPILED_PHONE_FILE).read_bytes() + b"x"
            )
            with self.assertRaisesRegex(ReconstructionError, "preuve|Marshal|source"):
                simulate_plan(plan)

    def test_phone_structure_proof_is_immutable_in_studio(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_phone_studio_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_phone_project(base)
            previous = csv_path.read_bytes()
            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).__setitem__("pbs_runtime_path", "[22,\"entry\",999]"),
            )
            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                with self.assertRaisesRegex(
                    TranslationProjectError,
                    "occurrence|donnée source",
                ):
                    session.save(altered)
            self.assertEqual(previous, csv_path.read_bytes())

    def test_phone_bundle_failure_rolls_back_all_three_game_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_phone_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_phone_project(base)
            target = base / "candidate"
            plan = build_v21_1_phone_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (PHONE_PBS_FILE, COMPILED_PHONE_FILE, PHONE_MESSAGES_FILE)
            }
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic phone publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")

            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
