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
from essentials_move import (
    COMPILED_MOVE_FILE,
    COMPILED_MOVE_PROOF_FORMAT,
    MOVE_MESSAGES_FILE,
    MOVE_PBS_FILE,
    MOVE_PBS_PROOF_FORMAT,
    MOVE_RUNTIME_PROOF_FORMAT,
    MoveIntegrityError,
    build_move_text_proofs,
    extract_move_description_texts,
    rebuild_move_description_payloads,
)
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    V21_1_MOVE_DESCRIPTION_VALIDATION_SCOPE,
    ReconstructionError,
    PlanItem,
    _apply_pbs_items,
    build_plan,
    build_v21_1_move_description_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from structured_extractor import extract_pbs, extract_structured_verified, stable_id
from translation_project import TranslationProjectError, TranslationProjectSession
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


TARGET_SECTION = "TACKLE"
SHARED_SECTION = "EMBER"
TEST_TRANSLATION = "[TEST PFT v21.1 MOVE DESCRIPTION]"


def prepare_move_project(base: Path, *, shared: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, move_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == MOVE_PBS_FILE
        and row["type"] == "PBS — Description"
        and row["evenement_id"] == (SHARED_SECTION if shared else TARGET_SECTION)
    )
    selected["traduction_fr"] = TEST_TRANSLATION
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_move"
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


class EssentialsMoveDescriptionTests(unittest.TestCase):
    def test_extraction_links_only_name_and_description_and_proves_category(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, move_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [row for row in result.rows if row["fichier"] == MOVE_PBS_FILE]

        self.assertEqual(6, len(rows))
        self.assertEqual(
            {"Name", "Description"},
            {row["commande"] for row in rows},
        )
        self.assertNotIn("PBS — Category", {row["type"] for row in rows})
        descriptions = [row for row in rows if row["commande"] == "Description"]
        self.assertEqual(
            [1, 2, 2],
            [json.loads(row["pbs_runtime_structure"])["source_usage_count"] for row in descriptions],
        )
        self.assertEqual(
            {0, 1, 2},
            {json.loads(row["pbs_compiled_structure"])["category_code"] for row in rows},
        )
        for row in rows:
            pbs = json.loads(row["pbs_structure"])
            compiled = json.loads(row["pbs_compiled_structure"])
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(MOVE_PBS_PROOF_FORMAT, pbs["format"])
            self.assertEqual(COMPILED_MOVE_PROOF_FORMAT, compiled["format"])
            self.assertEqual(MOVE_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(COMPILED_MOVE_FILE, row["pbs_compiled_file"])
            self.assertEqual(MOVE_MESSAGES_FILE, row["pbs_runtime_file"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])

    def test_category_is_file_aware_and_remains_textual_for_species(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_category_") as temporary:
            base = Path(temporary)
            moves = base / "moves.txt"
            pokemon = base / "pokemon.txt"
            moves.write_text(
                "[TEST]\nName = Test Move\nCategory = Physical\nDescription = Visible move text\n",
                encoding="utf-8",
            )
            pokemon.write_text(
                "[TEST]\nName = Test Species\nCategory = Seed Pokémon\n",
                encoding="utf-8",
            )
            move_rows = extract_pbs(moves, "PBS/moves.txt")
            species_rows = extract_pbs(pokemon, "PBS/pokemon.txt")

        self.assertNotIn("Category", {row["commande"] for row in move_rows})
        self.assertIn("Category", {row["commande"] for row in species_rows})

    def test_generic_pbs_reinjection_can_never_write_move_category(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_category_write_") as temporary:
            path = Path(temporary) / "moves.txt"
            original = (
                "[TACKLE]\r\n"
                "Name = Tackle\r\n"
                "Category = Physical\r\n"
                "Description = Visible move text\r\n"
            ).encode("utf-8")
            path.write_bytes(original)
            item = PlanItem(
                id_stable=stable_id(
                    "pbs", "PBS/moves.txt", "TACKLE", "Category", 1
                ),
                type="PBS — Category",
                fichier="PBS/moves.txt",
                source="Physical",
                translation="Special",
                status="Accepté",
            )

            with self.assertRaisesRegex(ReconstructionError, "introuvable"):
                _apply_pbs_items(path, "PBS/moves.txt", [item])

            self.assertEqual(original, path.read_bytes())

    def test_move_correlation_is_never_applied_to_a_legacy_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_legacy_move_guard_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, move_validation=True)
            result = extract_structured_verified(
                root,
                essentials_profile="essentials_legacy_rxmp",
            )
            rows = [row for row in result.rows if row["fichier"] == MOVE_PBS_FILE]

        self.assertEqual(6, len(rows))
        self.assertTrue(all(not row["pbs_compiled_structure"] for row in rows))
        self.assertTrue(all(not row["pbs_runtime_structure"] for row in rows))

    def test_private_roundtrip_changes_exactly_three_files_and_not_category(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_move_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)
            compiled_before = load(root / COMPILED_MOVE_FILE)

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

            plan = build_v21_1_move_description_validation_plan(root, csv_path)
            self.assertEqual(V21_1_MOVE_DESCRIPTION_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(
                {MOVE_PBS_FILE, COMPILED_MOVE_FILE, MOVE_MESSAGES_FILE},
                set(plan.source_hashes),
            )
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(
                sorted([MOVE_PBS_FILE, COMPILED_MOVE_FILE, MOVE_MESSAGES_FILE]),
                result.modified_files,
            )
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            translated = extract_move_description_texts(
                (target / MOVE_PBS_FILE).read_bytes(),
                (target / COMPILED_MOVE_FILE).read_bytes(),
                (target / MOVE_MESSAGES_FILE).read_bytes(),
                section=selected["evenement_id"],
            )
            self.assertEqual((selected["traduction_fr"],) * 3, translated)
            compiled_after = load(target / COMPILED_MOVE_FILE)
            before_object = compiled_before[TARGET_SECTION]
            after_object = compiled_after[TARGET_SECTION]
            for ivar in before_object.ivars:
                if ivar == "@real_description":
                    continue
                self.assertEqual(
                    dumps(before_object.ivars[ivar]),
                    dumps(after_object.ivars[ivar]),
                    ivar,
                )
            self.assertEqual(0, after_object.ivars["@category"])
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed={MOVE_PBS_FILE, COMPILED_MOVE_FILE, MOVE_MESSAGES_FILE},
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_proofs_refuse_category_bank_and_format_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, move_validation=True)
            pbs = (root / MOVE_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_MOVE_FILE).read_bytes()
            runtime = (root / MOVE_MESSAGES_FILE).read_bytes()

            compiled_root = load(root / COMPILED_MOVE_FILE)
            compiled_root[TARGET_SECTION].ivars["@category"] = 1
            with self.assertRaisesRegex(MoveIntegrityError, "techniques|Category|métadonnées"):
                build_move_text_proofs(pbs, dumps(compiled_root), runtime)

            with self.assertRaisesRegex(MoveIntegrityError, "techniques|Category|métadonnées"):
                build_move_text_proofs(
                    pbs.replace(b"Category = Physical", b"Category = Special", 1),
                    compiled,
                    runtime,
                )

            runtime_root = load(root / MOVE_MESSAGES_FILE)
            runtime_root[6].pop(next(iter(runtime_root[6])))
            with self.assertRaisesRegex(MoveIntegrityError, "banque|couvre"):
                build_move_text_proofs(pbs, compiled, dumps(runtime_root))

            for bad_pbs in (pbs[3:], pbs.replace(b"\r\n", b"\n")):
                with self.subTest(prefix=bad_pbs[:12]), self.assertRaises(MoveIntegrityError):
                    build_move_text_proofs(bad_pbs, compiled, runtime)

    def test_shared_description_is_extractable_but_reconstruction_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_shared_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_move_project(base, shared=True)
            with self.assertRaisesRegex(ReconstructionError, "partagée|correspond"):
                build_v21_1_move_description_validation_plan(root, csv_path)

            proofs = build_move_text_proofs(
                (root / MOVE_PBS_FILE).read_bytes(),
                (root / COMPILED_MOVE_FILE).read_bytes(),
                (root / MOVE_MESSAGES_FILE).read_bytes(),
            )
            proof = proofs[(selected["evenement_id"], "Description", 1)]
            with self.assertRaisesRegex(MoveIntegrityError, "partagée"):
                rebuild_move_description_payloads(
                    (root / MOVE_PBS_FILE).read_bytes(),
                    (root / COMPILED_MOVE_FILE).read_bytes(),
                    (root / MOVE_MESSAGES_FILE).read_bytes(),
                    section=selected["evenement_id"],
                    source=selected["texte_source"],
                    translation=selected["traduction_fr"],
                    pbs_structure=proof.pbs_structure,
                    compiled_path=proof.compiled_path,
                    compiled_structure=proof.compiled_structure,
                    runtime_path=proof.runtime_path,
                    runtime_structure=proof.runtime_structure,
                )

    def test_plan_and_studio_refuse_tampered_proof_or_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_guard_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_move_project(base)
            plan = build_v21_1_move_description_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            item.pbs_runtime_structure = "{}"
            with self.assertRaisesRegex(ReconstructionError, "preuve|correspond"):
                simulate_plan(plan)

            previous = csv_path.read_bytes()
            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).__setitem__("pbs_runtime_path", "[6,\"entry\",999]"),
            )
            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                with self.assertRaisesRegex(TranslationProjectError, "occurrence|donnée source"):
                    session.save(altered)
            self.assertEqual(previous, csv_path.read_bytes())

    def test_plan_refuses_technical_source_changed_after_planning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_toctou_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_move_project(base)
            plan = build_v21_1_move_description_validation_plan(root, csv_path)
            compiled_root = load(root / COMPILED_MOVE_FILE)
            compiled_root[TARGET_SECTION].ivars["@category"] = 1
            (root / COMPILED_MOVE_FILE).write_bytes(dumps(compiled_root))

            with self.assertRaisesRegex(ReconstructionError, "preuve|source|métadonnées"):
                simulate_plan(plan)

    def test_bundle_failure_rolls_back_all_three_game_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_move_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_move_project(base)
            target = base / "candidate"
            plan = build_v21_1_move_description_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (MOVE_PBS_FILE, COMPILED_MOVE_FILE, MOVE_MESSAGES_FILE)
            }
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic move publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")

            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
