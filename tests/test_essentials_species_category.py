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
from adapters.base import GameCapability
from analysis.integrity import compare_snapshots, snapshot_tree
from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SpeciesIntegrityError,
)
from essentials_species_category import (
    COMPILED_SPECIES_CATEGORY_PROOF_FORMAT,
    SPECIES_CATEGORY_PBS_PROOF_FORMAT,
    SPECIES_CATEGORY_RUNTIME_PROOF_FORMAT,
    build_species_category_proofs,
    extract_species_category_texts,
    rebuild_species_category_payloads,
)
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    V21_1_SPECIES_CATEGORY_VALIDATION_SCOPE,
    ReconstructionError,
    build_plan,
    build_v21_1_species_category_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import RubyString, load
from ruby_marshal_writer import dumps
from structured_extractor import stable_id
from translation_project import TranslationProjectError, TranslationProjectSession
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


TARGET_SECTION = "BULBASAUR"
SHARED_SECTION = "CUBONE"
TEST_TRANSLATION = "Seed [TEST PFT v21.1 SPECIES CATEGORY]"


def prepare_species_category_project(base: Path, *, shared: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, species_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == SPECIES_PBS_FILE
        and row["type"] == "PBS — Category"
        and row["evenement_id"] == (SHARED_SECTION if shared else TARGET_SECTION)
    )
    selected["traduction_fr"] = TEST_TRANSLATION
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = (
        "validation_synthetique_v21_1_species_category"
    )
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


class EssentialsSpeciesCategoryTests(unittest.TestCase):
    def test_extraction_proves_file_aware_category_and_excludes_technical_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            species_rows = [
                row for row in result.rows if row["fichier"] == SPECIES_PBS_FILE
            ]
            category_rows = [
                row for row in species_rows if row["commande"] == "Category"
            ]

        self.assertEqual(3, len(category_rows))
        self.assertEqual(
            {"Name", "Category", "Pokedex"},
            {row["commande"] for row in species_rows},
        )
        self.assertNotIn("Color", {row["commande"] for row in species_rows})
        self.assertNotIn("GrowthRate", {row["commande"] for row in species_rows})
        self.assertEqual(
            [1, 2, 1],
            [
                json.loads(row["pbs_runtime_structure"])["source_usage_count"]
                for row in category_rows
            ],
        )
        for row in category_rows:
            self.assertEqual(
                SPECIES_CATEGORY_PBS_PROOF_FORMAT,
                json.loads(row["pbs_structure"])["format"],
            )
            self.assertEqual(
                COMPILED_SPECIES_CATEGORY_PROOF_FORMAT,
                json.loads(row["pbs_compiled_structure"])["format"],
            )
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(SPECIES_CATEGORY_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(2, runtime["message_type_index"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])

    def test_private_roundtrip_changes_only_three_files_and_public_reconstruct_stays_off(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_species_category_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)
            compiled_before = load(root / COMPILED_SPECIES_FILE)
            detection = PokemonEssentialsAdapter().probe(root)
            self.assertFalse(detection.can(GameCapability.RECONSTRUCT))

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

            plan = build_v21_1_species_category_validation_plan(root, csv_path)
            self.assertEqual(V21_1_SPECIES_CATEGORY_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(
                {
                    SPECIES_PBS_FILE,
                    SPECIES_FORMS_PBS_FILE,
                    COMPILED_SPECIES_FILE,
                    SPECIES_MESSAGES_FILE,
                },
                set(plan.source_hashes),
            )
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            expected_changed = sorted(
                [SPECIES_PBS_FILE, COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE]
            )
            self.assertEqual(expected_changed, result.modified_files)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            self.assertEqual(
                (selected["traduction_fr"],) * 3,
                extract_species_category_texts(
                    (target / SPECIES_PBS_FILE).read_bytes(),
                    (target / SPECIES_FORMS_PBS_FILE).read_bytes(),
                    (target / COMPILED_SPECIES_FILE).read_bytes(),
                    (target / SPECIES_MESSAGES_FILE).read_bytes(),
                    section=selected["evenement_id"],
                ),
            )
            compiled_after = load(target / COMPILED_SPECIES_FILE)
            self.assertEqual(
                compiled_before[TARGET_SECTION].ivars["@real_pokedex_entry"].data,
                compiled_after[TARGET_SECTION].ivars["@real_pokedex_entry"].data,
            )
            self.assertEqual(
                compiled_before[TARGET_SECTION].ivars["@color"],
                compiled_after[TARGET_SECTION].ivars["@color"],
            )
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed=set(expected_changed),
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_shared_or_inherited_category_is_extractable_but_not_reconstructible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_shared_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_species_category_project(
                base, shared=True
            )
            with self.assertRaisesRegex(ReconstructionError, "partagée|correspond"):
                build_v21_1_species_category_validation_plan(root, csv_path)

            proofs = build_species_category_proofs(
                (root / SPECIES_PBS_FILE).read_bytes(),
                (root / SPECIES_FORMS_PBS_FILE).read_bytes(),
                (root / COMPILED_SPECIES_FILE).read_bytes(),
                (root / SPECIES_MESSAGES_FILE).read_bytes(),
            )
            proof = proofs[(selected["evenement_id"], "Category", 1)]
            with self.assertRaisesRegex(SpeciesIntegrityError, "partagée|héritée"):
                rebuild_species_category_payloads(
                    (root / SPECIES_PBS_FILE).read_bytes(),
                    (root / SPECIES_FORMS_PBS_FILE).read_bytes(),
                    (root / COMPILED_SPECIES_FILE).read_bytes(),
                    (root / SPECIES_MESSAGES_FILE).read_bytes(),
                    section=selected["evenement_id"],
                    source=selected["texte_source"],
                    translation=selected["traduction_fr"],
                    pbs_structure=proof.pbs_structure,
                    compiled_path=proof.compiled_path,
                    compiled_structure=proof.compiled_structure,
                    runtime_path=proof.runtime_path,
                    runtime_structure=proof.runtime_structure,
                )

    def test_proofs_refuse_type_order_inheritance_bank_and_pbs_format_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            pbs = (root / SPECIES_PBS_FILE).read_bytes()
            forms = (root / SPECIES_FORMS_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_SPECIES_FILE).read_bytes()
            runtime = (root / SPECIES_MESSAGES_FILE).read_bytes()

            compiled_root = load(root / COMPILED_SPECIES_FILE)
            compiled_root[TARGET_SECTION].ivars["@real_category"] = "not RubyString"
            with self.assertRaisesRegex(SpeciesIntegrityError, "RubyString"):
                build_species_category_proofs(
                    pbs, forms, dumps(compiled_root), runtime
                )

            compiled_root = load(root / COMPILED_SPECIES_FILE)
            inherited = compiled_root["CUBONE_1"].ivars["@real_category"]
            compiled_root["CUBONE_1"].ivars["@real_category"] = RubyString(
                inherited.data, dict(inherited.ivars)
            )
            with self.assertRaisesRegex(SpeciesIntegrityError, "héritée"):
                build_species_category_proofs(
                    pbs, forms, dumps(compiled_root), runtime
                )

            runtime_root = load(root / SPECIES_MESSAGES_FILE)
            runtime_root[2].pop(next(iter(runtime_root[2])))
            with self.assertRaisesRegex(SpeciesIntegrityError, "couvre"):
                build_species_category_proofs(
                    pbs, forms, compiled, dumps(runtime_root)
                )

            for bad_pbs, bad_forms in (
                (pbs[3:], forms),
                (pbs.replace(b"\r\n", b"\n"), forms),
                (pbs, forms[3:]),
                (pbs, forms.replace(b"\r\n", b"\n")),
            ):
                with self.assertRaises(SpeciesIntegrityError):
                    build_species_category_proofs(
                        bad_pbs, bad_forms, compiled, runtime
                    )

    def test_forged_move_category_csv_and_plan_remain_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_forged_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_species_category_project(base)
            previous = csv_path.read_bytes()
            forged = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).update(
                    {
                        "id_stable": stable_id(
                            "pbs", "PBS/moves.txt", "TACKLE", "Category", 1
                        ),
                        "fichier": "PBS/moves.txt",
                        "evenement_id": "TACKLE",
                        "evenement_nom": "TACKLE",
                        "commande": "Category",
                        "texte_source": "Physical",
                        "traduction_fr": "Special",
                    }
                ),
            )
            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                with self.assertRaisesRegex(
                    TranslationProjectError,
                    "occurrence|source|provenance",
                ):
                    session.save(forged)
            self.assertEqual(previous, csv_path.read_bytes())

            plan = build_v21_1_species_category_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            item.fichier = "PBS/moves.txt"
            item.type = "PBS — Category"
            item.event_id = "TACKLE"
            item.event_name = "TACKLE"
            item.id_stable = stable_id(
                "pbs", "PBS/moves.txt", "TACKLE", "Category", 1
            )
            with self.assertRaisesRegex(
                ReconstructionError,
                "moves.txt|limitée|Category",
            ):
                simulate_plan(plan)

            technical_plan = build_v21_1_species_category_validation_plan(
                root, csv_path
            )
            technical = next(
                item for item in technical_plan.items if item.decision == "applicable"
            )
            technical.type = "PBS — Color"
            technical.command = "Color"
            technical.source = "Green"
            technical.translation = "Blue"
            technical.id_stable = stable_id(
                "pbs", SPECIES_PBS_FILE, TARGET_SECTION, "Color", 1
            )
            with self.assertRaisesRegex(ReconstructionError, "limitée|Category"):
                simulate_plan(technical_plan)

    def test_compiled_or_forms_change_after_plan_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_toctou_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_category_project(base)
            plan = build_v21_1_species_category_validation_plan(root, csv_path)
            compiled_root = load(root / COMPILED_SPECIES_FILE)
            compiled_root[TARGET_SECTION].ivars["@color"] = "Blue"
            (root / COMPILED_SPECIES_FILE).write_bytes(dumps(compiled_root))
            with self.assertRaisesRegex(ReconstructionError, "preuve|source"):
                simulate_plan(plan)

        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_forms_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_category_project(base)
            plan = build_v21_1_species_category_validation_plan(root, csv_path)
            forms_path = root / SPECIES_FORMS_PBS_FILE
            forms_path.write_bytes(forms_path.read_bytes() + b"# changed\r\n")
            with self.assertRaisesRegex(ReconstructionError, "preuve|source"):
                simulate_plan(plan)

    def test_bundle_failure_rolls_back_all_category_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_cat_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_category_project(base)
            target = base / "candidate"
            plan = build_v21_1_species_category_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (
                    SPECIES_PBS_FILE,
                    COMPILED_SPECIES_FILE,
                    SPECIES_MESSAGES_FILE,
                )
            }
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic category publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")
            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
