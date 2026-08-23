from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import safe_io
from adapters import (
    ESSENTIALS_V21_1_READONLY_PROFILE,
    GameCapability,
    PokemonEssentialsAdapter,
)
from analysis.integrity import compare_snapshots, snapshot_tree
from essentials_ability import (
    ABILITY_MESSAGES_FILE,
    ABILITY_NAME_PBS_PROOF_FORMAT,
    ABILITY_NAME_RUNTIME_PROOF_FORMAT,
    ABILITY_PBS_FILE,
    ABILITY_PBS_PROOF_FORMAT,
    ABILITY_RUNTIME_PROOF_FORMAT,
    COMPILED_ABILITY_FILE,
    COMPILED_ABILITY_NAME_PROOF_FORMAT,
    COMPILED_ABILITY_PROOF_FORMAT,
    AbilityIntegrityError,
    build_ability_description_proofs,
    build_ability_name_proofs,
    build_ability_text_proofs,
    extract_ability_description_texts,
    extract_ability_name_texts,
    rebuild_ability_description_payloads,
    rebuild_ability_name_payloads,
)
from project_test_support import finalize_verified_essentials_project
from project_identity import PROJECT_METADATA_NAME
from reconstruction_engine import (
    V21_1_ABILITY_DESCRIPTION_VALIDATION_SCOPE,
    V21_1_ABILITY_NAME_VALIDATION_SCOPE,
    PlanItem,
    ReconstructionError,
    _apply_pbs_items,
    build_plan,
    build_v21_1_ability_description_validation_plan,
    build_v21_1_ability_name_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from structured_extractor import extract_pbs, extract_structured_verified, stable_id
from translation_project import TranslationProjectError, TranslationProjectSession
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


TARGET_SECTION = "OVERGROW"
SHARED_SECTION = "BLAZE"
NAME_TRANSLATION = "[PFT v21.1 NAME]"


def prepare_ability_project(base: Path, *, shared: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, ability_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == ABILITY_PBS_FILE
        and row["type"] == "PBS — Description"
        and row["evenement_id"] == (SHARED_SECTION if shared else TARGET_SECTION)
    )
    selected["traduction_fr"] = (
        selected["texte_source"] + " [TEST PFT v21.1 ABILITY DESCRIPTION]"
    )
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_ability"
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


def prepare_ability_name_project(base: Path, *, shared: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, ability_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == ABILITY_PBS_FILE
        and row["type"] == "PBS — Name"
        and row["evenement_id"] == (SHARED_SECTION if shared else TARGET_SECTION)
    )
    selected["traduction_fr"] = NAME_TRANSLATION
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_ability_name"
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


class EssentialsAbilityDescriptionTests(unittest.TestCase):
    def test_extraction_binds_pbs_compiled_and_runtime_structures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, ability_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [
                row
                for row in result.rows
                if row["fichier"] == ABILITY_PBS_FILE
                and row["type"] == "PBS — Description"
            ]

        self.assertEqual(3, len(rows))
        usage_counts = []
        for row in rows:
            pbs = json.loads(row["pbs_structure"])
            compiled = json.loads(row["pbs_compiled_structure"])
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(ABILITY_PBS_PROOF_FORMAT, pbs["format"])
            self.assertEqual(COMPILED_ABILITY_PROOF_FORMAT, compiled["format"])
            self.assertEqual(ABILITY_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(COMPILED_ABILITY_FILE, row["pbs_compiled_file"])
            self.assertEqual(ABILITY_MESSAGES_FILE, row["pbs_runtime_file"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])
            usage_counts.append(runtime["source_usage_count"])
        self.assertEqual([1, 2, 2], usage_counts)

    def test_ability_correlation_is_never_applied_to_a_legacy_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_legacy_ability_guard_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, ability_validation=True)
            result = extract_structured_verified(
                root,
                essentials_profile="essentials_legacy_rxmp",
            )
            rows = [
                row
                for row in result.rows
                if row["fichier"] == ABILITY_PBS_FILE
                and row["type"] == "PBS — Description"
            ]

        self.assertEqual(3, len(rows))
        self.assertTrue(all(not row["pbs_compiled_structure"] for row in rows))
        self.assertTrue(all(not row["pbs_runtime_structure"] for row in rows))

    def test_private_roundtrip_changes_exactly_three_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_ability_project(base)
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

            plan = build_v21_1_ability_description_validation_plan(root, csv_path)
            self.assertEqual(V21_1_ABILITY_DESCRIPTION_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(
                {ABILITY_PBS_FILE, COMPILED_ABILITY_FILE, ABILITY_MESSAGES_FILE},
                set(plan.source_hashes),
            )
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(
                sorted([ABILITY_PBS_FILE, COMPILED_ABILITY_FILE, ABILITY_MESSAGES_FILE]),
                result.modified_files,
            )
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            translated = extract_ability_description_texts(
                (target / ABILITY_PBS_FILE).read_bytes(),
                (target / COMPILED_ABILITY_FILE).read_bytes(),
                (target / ABILITY_MESSAGES_FILE).read_bytes(),
                section=selected["evenement_id"],
            )
            self.assertEqual((selected["traduction_fr"],) * 3, translated)
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed={
                    ABILITY_PBS_FILE,
                    COMPILED_ABILITY_FILE,
                    ABILITY_MESSAGES_FILE,
                },
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_proofs_refuse_identity_bank_and_format_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, ability_validation=True)
            pbs = (root / ABILITY_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_ABILITY_FILE).read_bytes()
            runtime = (root / ABILITY_MESSAGES_FILE).read_bytes()

            compiled_root = load(root / COMPILED_ABILITY_FILE)
            list(compiled_root.values())[0].ivars["@id"] = "CHANGED"
            with self.assertRaisesRegex(AbilityIntegrityError, "métadonnées|clé"):
                build_ability_description_proofs(pbs, dumps(compiled_root), runtime)

            runtime_root = load(root / ABILITY_MESSAGES_FILE)
            runtime_root[11].pop(next(iter(runtime_root[11])))
            with self.assertRaisesRegex(AbilityIntegrityError, "couvre"):
                build_ability_description_proofs(pbs, compiled, dumps(runtime_root))

            for bad_pbs in (
                pbs[3:],
                pbs.replace(b"\r\n", b"\n"),
                pbs.replace(b"Description = Synthetic unique", b"Name = Synthetic unique"),
            ):
                with self.subTest(prefix=bad_pbs[:12]), self.assertRaises(AbilityIntegrityError):
                    build_ability_description_proofs(bad_pbs, compiled, runtime)

    def test_shared_description_is_extractable_but_reconstruction_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_shared_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_ability_project(base, shared=True)
            with self.assertRaisesRegex(ReconstructionError, "partagée|correspond"):
                build_v21_1_ability_description_validation_plan(root, csv_path)

            proofs = build_ability_description_proofs(
                (root / ABILITY_PBS_FILE).read_bytes(),
                (root / COMPILED_ABILITY_FILE).read_bytes(),
                (root / ABILITY_MESSAGES_FILE).read_bytes(),
            )
            proof = proofs[(selected["evenement_id"], "Description", 1)]
            with self.assertRaisesRegex(AbilityIntegrityError, "partagée"):
                rebuild_ability_description_payloads(
                    (root / ABILITY_PBS_FILE).read_bytes(),
                    (root / COMPILED_ABILITY_FILE).read_bytes(),
                    (root / ABILITY_MESSAGES_FILE).read_bytes(),
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
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_guard_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_ability_project(base)
            plan = build_v21_1_ability_description_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            item.pbs_runtime_structure = "{}"
            with self.assertRaisesRegex(ReconstructionError, "preuve|correspond"):
                simulate_plan(plan)

            previous = csv_path.read_bytes()
            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).__setitem__("pbs_runtime_path", "[11,\"entry\",999]"),
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

    def test_plan_refuses_compiled_source_changed_after_planning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_toctou_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_ability_project(base)
            plan = build_v21_1_ability_description_validation_plan(root, csv_path)
            compiled_root = load(root / COMPILED_ABILITY_FILE)
            compiled_root[TARGET_SECTION].ivars["@real_name"].data = b"Changed name"
            (root / COMPILED_ABILITY_FILE).write_bytes(dumps(compiled_root))

            with self.assertRaisesRegex(
                ReconstructionError,
                "preuve|source|métadonnées",
            ):
                simulate_plan(plan)

    def test_bundle_failure_rolls_back_all_three_game_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_ability_project(base)
            target = base / "candidate"
            plan = build_v21_1_ability_description_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (
                    ABILITY_PBS_FILE,
                    COMPILED_ABILITY_FILE,
                    ABILITY_MESSAGES_FILE,
                )
            }
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic ability publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")

            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


class EssentialsAbilityNameTests(unittest.TestCase):
    def test_name_extraction_has_own_bank_proofs_and_excludes_flags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_name_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, ability_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [
                row for row in result.rows if row["fichier"] == ABILITY_PBS_FILE
            ]

        self.assertEqual(6, len(rows))
        self.assertEqual({"Name", "Description"}, {row["commande"] for row in rows})
        self.assertNotIn("Flags", {row["commande"] for row in rows})
        names = [row for row in rows if row["commande"] == "Name"]
        self.assertEqual([1, 2, 2], [
            json.loads(row["pbs_runtime_structure"])["source_usage_count"]
            for row in names
        ])
        for row in names:
            self.assertEqual(
                ABILITY_NAME_PBS_PROOF_FORMAT,
                json.loads(row["pbs_structure"])["format"],
            )
            self.assertEqual(
                COMPILED_ABILITY_NAME_PROOF_FORMAT,
                json.loads(row["pbs_compiled_structure"])["format"],
            )
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(ABILITY_NAME_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(10, runtime["message_type_index"])

    def test_private_name_roundtrip_changes_only_name_in_three_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_name_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_ability_name_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)
            compiled_before = load(root / COMPILED_ABILITY_FILE)
            messages_before = load(root / ABILITY_MESSAGES_FILE)

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

            detection = PokemonEssentialsAdapter().probe(root)
            self.assertFalse(detection.can(GameCapability.RECONSTRUCT))
            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")

            plan = build_v21_1_ability_name_validation_plan(root, csv_path)
            self.assertEqual(V21_1_ABILITY_NAME_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(
                {ABILITY_PBS_FILE, COMPILED_ABILITY_FILE, ABILITY_MESSAGES_FILE},
                set(plan.source_hashes),
            )
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(
                sorted([ABILITY_PBS_FILE, COMPILED_ABILITY_FILE, ABILITY_MESSAGES_FILE]),
                result.modified_files,
            )
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            self.assertEqual(
                (selected["traduction_fr"],) * 3,
                extract_ability_name_texts(
                    (target / ABILITY_PBS_FILE).read_bytes(),
                    (target / COMPILED_ABILITY_FILE).read_bytes(),
                    (target / ABILITY_MESSAGES_FILE).read_bytes(),
                    section=TARGET_SECTION,
                ),
            )
            compiled_after = load(target / COMPILED_ABILITY_FILE)
            for ivar in compiled_before[TARGET_SECTION].ivars:
                if ivar == "@real_name":
                    continue
                self.assertEqual(
                    dumps(compiled_before[TARGET_SECTION].ivars[ivar]),
                    dumps(compiled_after[TARGET_SECTION].ivars[ivar]),
                    ivar,
                )
            messages_after = load(target / ABILITY_MESSAGES_FILE)
            self.assertEqual(dumps(messages_before[11]), dumps(messages_after[11]))
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed={
                    ABILITY_PBS_FILE,
                    COMPILED_ABILITY_FILE,
                    ABILITY_MESSAGES_FILE,
                },
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_shared_name_is_extractable_but_private_rebuild_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_name_shared_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_ability_name_project(base, shared=True)
            with self.assertRaisesRegex(ReconstructionError, "partagée|correspond"):
                build_v21_1_ability_name_validation_plan(root, csv_path)

            proofs = build_ability_name_proofs(
                (root / ABILITY_PBS_FILE).read_bytes(),
                (root / COMPILED_ABILITY_FILE).read_bytes(),
                (root / ABILITY_MESSAGES_FILE).read_bytes(),
            )
            proof = proofs[(selected["evenement_id"], "Name", 1)]
            with self.assertRaisesRegex(AbilityIntegrityError, "partagée"):
                rebuild_ability_name_payloads(
                    (root / ABILITY_PBS_FILE).read_bytes(),
                    (root / COMPILED_ABILITY_FILE).read_bytes(),
                    (root / ABILITY_MESSAGES_FILE).read_bytes(),
                    section=selected["evenement_id"],
                    source=selected["texte_source"],
                    translation=selected["traduction_fr"],
                    pbs_structure=proof.pbs_structure,
                    compiled_path=proof.compiled_path,
                    compiled_structure=proof.compiled_structure,
                    runtime_path=proof.runtime_path,
                    runtime_structure=proof.runtime_structure,
                )

    def test_forged_technical_ability_field_is_refused_without_writing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_technical_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_ability_name_project(base)
            pbs_path = root / ABILITY_PBS_FILE
            original = pbs_path.read_bytes()
            forged_item = PlanItem(
                id_stable=stable_id("pbs", ABILITY_PBS_FILE, "BLAZE", "Flags", 1),
                type="PBS — Flags",
                fichier=ABILITY_PBS_FILE,
                source="FasterEggHatching",
                translation="TechnicalLookingText",
                status="Accepté",
            )
            with self.assertRaisesRegex(ReconstructionError, "introuvable"):
                _apply_pbs_items(pbs_path, ABILITY_PBS_FILE, [forged_item])
            self.assertEqual(original, pbs_path.read_bytes())

            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).update(
                    {
                        "id_stable": forged_item.id_stable,
                        "type": forged_item.type,
                        "evenement_id": "BLAZE",
                        "evenement_nom": "BLAZE",
                        "commande": "Flags",
                        "texte_source": "FasterEggHatching",
                        "traduction_fr": "TechnicalLookingText",
                    }
                ),
            )
            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                with self.assertRaisesRegex(
                    TranslationProjectError, "occurrence|donnée source"
                ):
                    session.save(altered)
            self.assertEqual(original, pbs_path.read_bytes())

    def test_wrong_identity_proof_or_second_ability_field_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_name_guard_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_ability_name_project(base)
            plan = build_v21_1_ability_name_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            item.event_id = "WRONG"
            with self.assertRaisesRegex(ReconstructionError, "preuve|Ability|correspond"):
                simulate_plan(plan)

            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row
                    for row in rows
                    if row["fichier"] == ABILITY_PBS_FILE
                    and row["commande"] == "Description"
                    and row["evenement_id"] == TARGET_SECTION
                ).update(
                    {
                        "traduction_fr": "A second forbidden translation.",
                        "statut": "Accepté",
                    }
                ),
            )
            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                session.save(altered)
            with self.assertRaisesRegex(ReconstructionError, "exactement un texte"):
                build_v21_1_ability_name_validation_plan(root, csv_path)
            self.assertEqual(NAME_TRANSLATION, selected["traduction_fr"])

    def test_name_plan_refuses_description_or_provenance_changed_after_planning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_name_toctou_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_ability_name_project(base)
            plan = build_v21_1_ability_name_validation_plan(root, csv_path)
            compiled_root = load(root / COMPILED_ABILITY_FILE)
            compiled_root[TARGET_SECTION].ivars["@real_description"].data = b"Changed"
            (root / COMPILED_ABILITY_FILE).write_bytes(dumps(compiled_root))
            with self.assertRaisesRegex(
                ReconstructionError, "preuve|source|métadonnées|Ability"
            ):
                simulate_plan(plan)

    def test_name_plan_refuses_incoherent_project_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_name_provenance_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_ability_name_project(base)
            identity_path = csv_path.parent / PROJECT_METADATA_NAME
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["source_manifest_sha256"] = "0" * 64
            identity_path.write_text(
                json.dumps(identity, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReconstructionError, "provenance|identité|manifeste|projet"
            ):
                build_v21_1_ability_name_validation_plan(root, csv_path)

    def test_moves_category_protection_remains_context_specific(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_move_category_") as temporary:
            path = Path(temporary) / "moves.txt"
            original = (
                "[TACKLE]\r\nName = Tackle\r\nCategory = Physical\r\n"
                "Description = A move.\r\n"
            ).encode("utf-8")
            path.write_bytes(original)
            rows = extract_pbs(path, "PBS/moves.txt")
            self.assertEqual({"Name", "Description"}, {row["commande"] for row in rows})
            forged = PlanItem(
                id_stable=stable_id("pbs", "PBS/moves.txt", "TACKLE", "Category", 1),
                type="PBS — Category",
                fichier="PBS/moves.txt",
                source="Physical",
                translation="Translated technical value",
                status="Accepté",
            )
            with self.assertRaisesRegex(ReconstructionError, "introuvable"):
                _apply_pbs_items(path, "PBS/moves.txt", [forged])
            self.assertEqual(original, path.read_bytes())

    def test_name_bundle_failure_rolls_back_all_three_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_ability_name_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_ability_name_project(base)
            target = base / "candidate"
            plan = build_v21_1_ability_name_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (
                    ABILITY_PBS_FILE,
                    COMPILED_ABILITY_FILE,
                    ABILITY_MESSAGES_FILE,
                )
            }
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic ability name publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")

            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
