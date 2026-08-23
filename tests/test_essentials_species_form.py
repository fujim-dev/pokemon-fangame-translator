import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import safe_io
from adapters import ESSENTIALS_V21_1_READONLY_PROFILE, PokemonEssentialsAdapter
from adapters.base import GameCapability
from analysis.integrity import compare_snapshots, snapshot_tree
from essentials_phone import graph_sha256
from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SpeciesIntegrityError,
)
from essentials_species_form import (
    COMPILED_SPECIES_FORM_NAME_PROOF_FORMAT,
    SPECIES_FORM_NAME_PBS_PROOF_FORMAT,
    SPECIES_FORM_NAME_RUNTIME_PROOF_FORMAT,
    build_species_form_name_proofs,
    extract_species_form_name_texts,
    rebuild_species_form_name_payloads,
)
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    V21_1_SPECIES_FORM_NAME_VALIDATION_SCOPE,
    ReconstructionError,
    build_plan,
    build_v21_1_species_form_name_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import RubyString, load
from ruby_marshal_writer import dumps
from structured_extractor import stable_id
from translation_project import TranslationProjectSession
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


TARGET_SECTION = "CHARMANDER,1"
TARGET_COMPILED_KEY = "CHARMANDER_1"
OTHER_SECTION = "CUBONE,1"
OTHER_COMPILED_KEY = "CUBONE_1"
TEST_TRANSLATION = "[TEST PFT v21.1 SPECIES FORM NAME]"


def prepare_species_form_project(base: Path):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, species_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == SPECIES_FORMS_PBS_FILE
        and row["type"] == "PBS — FormName"
        and row["evenement_id"] == TARGET_SECTION
    )
    selected["traduction_fr"] = TEST_TRANSLATION
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = (
        "validation_synthetique_v21_1_species_form_name"
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


def source_payloads(root: Path) -> tuple[bytes, bytes, bytes, bytes]:
    return (
        (root / SPECIES_PBS_FILE).read_bytes(),
        (root / SPECIES_FORMS_PBS_FILE).read_bytes(),
        (root / COMPILED_SPECIES_FILE).read_bytes(),
        (root / SPECIES_MESSAGES_FILE).read_bytes(),
    )


class EssentialsSpeciesFormNameTests(unittest.TestCase):
    def test_extraction_binds_only_explicit_form_names_to_form_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_form_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [
                row
                for row in result.rows
                if row["fichier"] == SPECIES_FORMS_PBS_FILE
                and row["type"] == "PBS — FormName"
            ]

        self.assertEqual(2, len(rows))
        self.assertEqual({TARGET_SECTION, OTHER_SECTION}, {row["evenement_id"] for row in rows})
        for row in rows:
            pbs = json.loads(row["pbs_structure"])
            compiled = json.loads(row["pbs_compiled_structure"])
            runtime = json.loads(row["pbs_runtime_structure"])
            species, form = row["evenement_id"].split(",")
            self.assertEqual(SPECIES_FORM_NAME_PBS_PROOF_FORMAT, pbs["format"])
            self.assertEqual(COMPILED_SPECIES_FORM_NAME_PROOF_FORMAT, compiled["format"])
            self.assertEqual(SPECIES_FORM_NAME_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertTrue(pbs["target_is_explicit"])
            self.assertFalse(pbs["form_name_is_inherited"])
            self.assertEqual(species, compiled["species"])
            self.assertEqual(int(form), compiled["form"])
            self.assertEqual("@real_form_name", compiled["field"])
            self.assertEqual(4, runtime["message_type_index"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])

    def test_private_roundtrip_changes_only_form_pbs_compiled_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_form_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_species_form_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)
            base_pbs_before = (root / SPECIES_PBS_FILE).read_bytes()
            compiled_before = load(root / COMPILED_SPECIES_FILE)
            other_before = dumps(compiled_before[OTHER_COMPILED_KEY])
            target_outside_before = graph_sha256(
                compiled_before[TARGET_COMPILED_KEY],
                masked=(compiled_before[TARGET_COMPILED_KEY].ivars["@real_form_name"],),
            )
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
            plan = build_v21_1_species_form_name_validation_plan(root, csv_path)
            self.assertEqual(V21_1_SPECIES_FORM_NAME_VALIDATION_SCOPE, plan.validation_scope)
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
                [SPECIES_FORMS_PBS_FILE, COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE]
            )
            self.assertEqual(expected_changed, result.modified_files)
            self.assertEqual(base_pbs_before, (target / SPECIES_PBS_FILE).read_bytes())
            self.assertEqual(
                (selected["traduction_fr"],) * 3,
                extract_species_form_name_texts(
                    *(path.read_bytes() for path in (
                        target / SPECIES_PBS_FILE,
                        target / SPECIES_FORMS_PBS_FILE,
                        target / COMPILED_SPECIES_FILE,
                        target / SPECIES_MESSAGES_FILE,
                    )),
                    section=selected["evenement_id"],
                ),
            )
            compiled_after = load(target / COMPILED_SPECIES_FILE)
            self.assertEqual(other_before, dumps(compiled_after[OTHER_COMPILED_KEY]))
            self.assertEqual(
                target_outside_before,
                graph_sha256(
                    compiled_after[TARGET_COMPILED_KEY],
                    masked=(compiled_after[TARGET_COMPILED_KEY].ivars["@real_form_name"],),
                ),
            )
            self.assertEqual(
                compiled_before[TARGET_COMPILED_KEY].ivars["@real_pokedex_entry"].data,
                compiled_after[TARGET_COMPILED_KEY].ivars["@real_pokedex_entry"].data,
            )
            self.assertEqual(
                compiled_before[TARGET_COMPILED_KEY].ivars["@real_category"].data,
                compiled_after[TARGET_COMPILED_KEY].ivars["@real_category"].data,
            )
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed=set(expected_changed),
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_absent_form_name_is_nil_and_cannot_be_reconstructed_as_inherited(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_form_missing_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            base_pbs, forms_pbs, compiled, runtime = source_payloads(root)
            forms_pbs = forms_pbs.replace(
                b"FormName = Synthetic inherited form\r\n", b""
            )
            compiled_root = load(root / COMPILED_SPECIES_FILE)
            compiled_root[OTHER_COMPILED_KEY].ivars["@real_form_name"] = None
            runtime_root = load(root / SPECIES_MESSAGES_FILE)
            runtime_root[4].pop(next(iter(runtime_root[4])))
            compiled = dumps(compiled_root)
            runtime = dumps(runtime_root)
            proofs = build_species_form_name_proofs(
                base_pbs, forms_pbs, compiled, runtime
            )
            self.assertNotIn((OTHER_SECTION, "FormName", 1), proofs)
            with self.assertRaisesRegex(SpeciesIntegrityError, "introuvable|absente"):
                extract_species_form_name_texts(
                    base_pbs,
                    forms_pbs,
                    compiled,
                    runtime,
                    section=OTHER_SECTION,
                )

    def test_shared_form_name_is_extractable_but_private_rebuild_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_form_shared_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            base_pbs, forms_pbs, _compiled, _runtime = source_payloads(root)
            forms_pbs = forms_pbs.replace(
                b"Synthetic inherited form", b"Synthetic explicit form"
            )
            compiled_root = load(root / COMPILED_SPECIES_FILE)
            shared = compiled_root[TARGET_COMPILED_KEY].ivars["@real_form_name"]
            compiled_root[OTHER_COMPILED_KEY].ivars["@real_form_name"] = shared
            runtime_root = load(root / SPECIES_MESSAGES_FILE)
            runtime_root[4].pop(next(iter(runtime_root[4])))
            compiled = dumps(compiled_root)
            runtime = dumps(runtime_root)
            proofs = build_species_form_name_proofs(
                base_pbs, forms_pbs, compiled, runtime
            )
            proof = proofs[(TARGET_SECTION, "FormName", 1)]
            with self.assertRaisesRegex(SpeciesIntegrityError, "partagé|ambigu"):
                rebuild_species_form_name_payloads(
                    base_pbs,
                    forms_pbs,
                    compiled,
                    runtime,
                    section=TARGET_SECTION,
                    source=proof.source,
                    translation=TEST_TRANSLATION,
                    pbs_structure=proof.pbs_structure,
                    compiled_path=proof.compiled_path,
                    compiled_structure=proof.compiled_structure,
                    runtime_path=proof.runtime_path,
                    runtime_structure=proof.runtime_structure,
                )

    def test_scope_refuses_base_other_form_and_non_form_name_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_form_scope_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_form_project(base)
            for event_id, field, file_name in (
                ("CHARMANDER", "FormName", SPECIES_PBS_FILE),
                (OTHER_SECTION, "FormName", SPECIES_FORMS_PBS_FILE),
                (TARGET_SECTION, "Pokedex", SPECIES_FORMS_PBS_FILE),
            ):
                plan = build_v21_1_species_form_name_validation_plan(root, csv_path)
                item = next(item for item in plan.items if item.decision == "applicable")
                item.event_id = event_id
                item.event_name = event_id
                item.command = field
                item.type = f"PBS — {field}"
                item.fichier = file_name
                item.id_stable = stable_id("pbs", file_name, event_id, field, 1)
                with self.assertRaisesRegex(
                    ReconstructionError, "limitée|FormName|preuve"
                ):
                    simulate_plan(plan)

    def test_proofs_refuse_identity_type_bank_order_and_pbs_format_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_form_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, species_validation=True)
            base_pbs, forms_pbs, compiled, runtime = source_payloads(root)

            compiled_root = load(root / COMPILED_SPECIES_FILE)
            compiled_root[TARGET_COMPILED_KEY].ivars["@real_form_name"] = "not RubyString"
            with self.assertRaisesRegex(SpeciesIntegrityError, "RubyString"):
                build_species_form_name_proofs(
                    base_pbs, forms_pbs, dumps(compiled_root), runtime
                )

            compiled_root = load(root / COMPILED_SPECIES_FILE)
            compiled_root[TARGET_COMPILED_KEY].ivars["@form"] = 2
            with self.assertRaisesRegex(SpeciesIntegrityError, "identité"):
                build_species_form_name_proofs(
                    base_pbs, forms_pbs, dumps(compiled_root), runtime
                )

            runtime_root = load(root / SPECIES_MESSAGES_FILE)
            runtime_root[4].pop(next(iter(runtime_root[4])))
            with self.assertRaisesRegex(SpeciesIntegrityError, "couvre"):
                build_species_form_name_proofs(
                    base_pbs, forms_pbs, compiled, dumps(runtime_root)
                )

            for bad_base, bad_forms in (
                (base_pbs[3:], forms_pbs),
                (base_pbs.replace(b"\r\n", b"\n"), forms_pbs),
                (base_pbs, forms_pbs[3:]),
                (base_pbs, forms_pbs.replace(b"\r\n", b"\n")),
            ):
                with self.assertRaises(SpeciesIntegrityError):
                    build_species_form_name_proofs(
                        bad_base, bad_forms, compiled, runtime
                    )

    def test_source_changed_after_plan_is_refused_for_each_watched_file(self) -> None:
        mutators = {
            SPECIES_PBS_FILE: lambda path: path.write_bytes(
                path.read_bytes() + b"# base changed\r\n"
            ),
            SPECIES_FORMS_PBS_FILE: lambda path: path.write_bytes(
                path.read_bytes() + b"# forms changed\r\n"
            ),
            COMPILED_SPECIES_FILE: lambda path: path.write_bytes(
                path.read_bytes() + b"changed"
            ),
            SPECIES_MESSAGES_FILE: lambda path: path.write_bytes(
                path.read_bytes() + b"changed"
            ),
        }
        for relative, mutate in mutators.items():
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(
                prefix="pft_v21_species_form_toctou_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_species_form_project(base)
                plan = build_v21_1_species_form_name_validation_plan(root, csv_path)
                mutate(root / relative)
                with self.assertRaisesRegex(ReconstructionError, "source|preuve"):
                    simulate_plan(plan)

    def test_bundle_failure_rolls_back_all_form_name_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_species_form_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_species_form_project(base)
            target = base / "candidate"
            plan = build_v21_1_species_form_name_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (
                    SPECIES_FORMS_PBS_FILE,
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
                    raise OSError("synthetic Species.FormName publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")
            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
