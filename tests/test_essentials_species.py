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
from essentials_species import (
    COMPILED_SPECIES_FILE,
    COMPILED_SPECIES_PROOF_FORMAT,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SPECIES_PBS_PROOF_FORMAT,
    SPECIES_RUNTIME_PROOF_FORMAT,
    SpeciesIntegrityError,
    build_species_pokedex_proofs,
    extract_species_pokedex_texts,
    rebuild_species_pokedex_payloads,
)
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    V21_1_SPECIES_POKEDEX_VALIDATION_SCOPE,
    ReconstructionError,
    build_plan,
    build_v21_1_species_pokedex_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import RubyString, load
from ruby_marshal_writer import dumps
from structured_extractor import extract_structured_verified
from translation_project import TranslationProjectError, TranslationProjectSession
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


TARGET_SECTION = "BULBASAUR"
SHARED_SECTION = "CUBONE"


def prepare_species_project(base: Path, *, shared: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, species_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == SPECIES_PBS_FILE
        and row["type"] == "PBS — Pokedex"
        and row["evenement_id"] == (SHARED_SECTION if shared else TARGET_SECTION)
    )
    selected["traduction_fr"] = (
        "[TEST PFT v21.1 POKEDEX] " + selected["texte_source"]
    )
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_species_pokedex"
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


class EssentialsSpeciesPokedexTests(unittest.TestCase):
    def test_extraction_binds_two_pbs_compiled_and_runtime_structures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [
                row
                for row in result.rows
                if row["fichier"] == SPECIES_PBS_FILE
                and row["type"] == "PBS — Pokedex"
            ]

        self.assertEqual(3, len(rows))
        usage_counts = []
        for row in rows:
            pbs = json.loads(row["pbs_structure"])
            compiled = json.loads(row["pbs_compiled_structure"])
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(SPECIES_PBS_PROOF_FORMAT, pbs["format"])
            self.assertEqual(COMPILED_SPECIES_PROOF_FORMAT, compiled["format"])
            self.assertEqual(SPECIES_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(3, pbs["base_section_count"])
            self.assertEqual(2, pbs["form_section_count"])
            self.assertEqual(1, pbs["explicit_form_pokedex_count"])
            self.assertEqual(1, pbs["inherited_form_pokedex_count"])
            self.assertEqual(COMPILED_SPECIES_FILE, row["pbs_compiled_file"])
            self.assertEqual(SPECIES_MESSAGES_FILE, row["pbs_runtime_file"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])
            usage_counts.append(runtime["source_usage_count"])
        self.assertEqual([1, 2, 1], usage_counts)

    def test_species_correlation_is_never_applied_to_a_legacy_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_legacy_species_guard_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            result = extract_structured_verified(
                root,
                essentials_profile="essentials_legacy_rxmp",
            )
            rows = [
                row
                for row in result.rows
                if row["fichier"] == SPECIES_PBS_FILE
                and row["type"] == "PBS — Pokedex"
            ]

        self.assertEqual(3, len(rows))
        self.assertTrue(all(not row["pbs_compiled_structure"] for row in rows))
        self.assertTrue(all(not row["pbs_runtime_structure"] for row in rows))

    def test_private_roundtrip_changes_three_files_and_watches_both_pbs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_species_project(base)
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

            plan = build_v21_1_species_pokedex_validation_plan(root, csv_path)
            self.assertEqual(V21_1_SPECIES_POKEDEX_VALIDATION_SCOPE, plan.validation_scope)
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
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            translated = extract_species_pokedex_texts(
                (target / SPECIES_PBS_FILE).read_bytes(),
                (target / SPECIES_FORMS_PBS_FILE).read_bytes(),
                (target / COMPILED_SPECIES_FILE).read_bytes(),
                (target / SPECIES_MESSAGES_FILE).read_bytes(),
                section=selected["evenement_id"],
            )
            self.assertEqual((selected["traduction_fr"],) * 3, translated)
            self.assertEqual(
                (root / SPECIES_FORMS_PBS_FILE).read_bytes(),
                (target / SPECIES_FORMS_PBS_FILE).read_bytes(),
            )
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed=set(expected_changed),
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_proofs_refuse_identity_order_inheritance_bank_and_format_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            pbs = (root / SPECIES_PBS_FILE).read_bytes()
            forms = (root / SPECIES_FORMS_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_SPECIES_FILE).read_bytes()
            runtime = (root / SPECIES_MESSAGES_FILE).read_bytes()

            compiled_root = load(root / COMPILED_SPECIES_FILE)
            compiled_root["BULBASAUR"].ivars["@form"] = 1
            with self.assertRaisesRegex(SpeciesIntegrityError, "identité|texte"):
                build_species_pokedex_proofs(pbs, forms, dumps(compiled_root), runtime)

            compiled_root = load(root / COMPILED_SPECIES_FILE)
            reordered = {key: compiled_root[key] for key in reversed(compiled_root)}
            with self.assertRaisesRegex(SpeciesIntegrityError, "racine"):
                build_species_pokedex_proofs(pbs, forms, dumps(reordered), runtime)

            compiled_root = load(root / COMPILED_SPECIES_FILE)
            inherited = compiled_root["CUBONE_1"].ivars["@real_pokedex_entry"]
            compiled_root["CUBONE_1"].ivars["@real_pokedex_entry"] = RubyString(
                inherited.data, dict(inherited.ivars)
            )
            with self.assertRaisesRegex(SpeciesIntegrityError, "héritée"):
                build_species_pokedex_proofs(pbs, forms, dumps(compiled_root), runtime)

            runtime_root = load(root / SPECIES_MESSAGES_FILE)
            runtime_root[3].pop(next(iter(runtime_root[3])))
            with self.assertRaisesRegex(SpeciesIntegrityError, "couvre"):
                build_species_pokedex_proofs(pbs, forms, compiled, dumps(runtime_root))

            for bad_pbs, bad_forms in (
                (pbs[3:], forms),
                (pbs.replace(b"\r\n", b"\n"), forms),
                (pbs, forms[3:]),
                (pbs, forms.replace(b"\r\n", b"\n")),
            ):
                with self.subTest(prefix=bad_pbs[:8]), self.assertRaises(
                    SpeciesIntegrityError
                ):
                    build_species_pokedex_proofs(
                        bad_pbs, bad_forms, compiled, runtime
                    )

    def test_inherited_entry_is_extractable_but_reconstruction_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_shared_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_species_project(base, shared=True)
            with self.assertRaisesRegex(ReconstructionError, "partagée|correspond"):
                build_v21_1_species_pokedex_validation_plan(root, csv_path)

            proofs = build_species_pokedex_proofs(
                (root / SPECIES_PBS_FILE).read_bytes(),
                (root / SPECIES_FORMS_PBS_FILE).read_bytes(),
                (root / COMPILED_SPECIES_FILE).read_bytes(),
                (root / SPECIES_MESSAGES_FILE).read_bytes(),
            )
            proof = proofs[(selected["evenement_id"], "Pokedex", 1)]
            with self.assertRaisesRegex(SpeciesIntegrityError, "partagée|héritée"):
                rebuild_species_pokedex_payloads(
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

    def test_plan_and_studio_refuse_tampered_proof_or_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_guard_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_species_project(base)
            plan = build_v21_1_species_pokedex_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            item.pbs_compiled_structure = "{}"
            with self.assertRaisesRegex(ReconstructionError, "preuve|correspond"):
                simulate_plan(plan)

            previous = csv_path.read_bytes()
            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).__setitem__("pbs_runtime_path", "[3,\"entry\",999]"),
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

    def test_plan_refuses_forms_or_compiled_source_changed_after_planning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_toctou_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_project(base)
            plan = build_v21_1_species_pokedex_validation_plan(root, csv_path)
            forms_path = root / SPECIES_FORMS_PBS_FILE
            forms_path.write_bytes(forms_path.read_bytes() + b"# changed after plan\r\n")
            with self.assertRaisesRegex(ReconstructionError, "source|preuve"):
                simulate_plan(plan)

        with tempfile.TemporaryDirectory(prefix="pft_v21_species_compiled_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_project(base)
            plan = build_v21_1_species_pokedex_validation_plan(root, csv_path)
            compiled_root = load(root / COMPILED_SPECIES_FILE)
            compiled_root[TARGET_SECTION].ivars["@real_name"].data = b"Changed name"
            (root / COMPILED_SPECIES_FILE).write_bytes(dumps(compiled_root))
            with self.assertRaisesRegex(
                ReconstructionError,
                "preuve|source|nom",
            ):
                simulate_plan(plan)

    def test_bundle_failure_rolls_back_all_three_changed_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_project(base)
            target = base / "candidate"
            plan = build_v21_1_species_pokedex_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (
                    SPECIES_PBS_FILE,
                    COMPILED_SPECIES_FILE,
                    SPECIES_MESSAGES_FILE,
                )
            }
            forms_original = (root / SPECIES_FORMS_PBS_FILE).read_bytes()
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic species publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")

            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertEqual(
                forms_original,
                (target / SPECIES_FORMS_PBS_FILE).read_bytes(),
            )
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
