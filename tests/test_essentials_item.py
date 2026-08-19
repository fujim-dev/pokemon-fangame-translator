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
from essentials_item import (
    COMPILED_ITEM_FILE,
    COMPILED_ITEM_PROOF_FORMAT,
    ITEM_MESSAGES_FILE,
    ITEM_PBS_FILE,
    ITEM_PBS_PROOF_FORMAT,
    ITEM_RUNTIME_PROOF_FORMAT,
    ItemIntegrityError,
    build_item_text_proofs,
    extract_item_description_texts,
    rebuild_item_description_payloads,
)
from project_test_support import finalize_verified_essentials_project
from reconstruction_engine import (
    V21_1_ITEM_DESCRIPTION_VALIDATION_SCOPE,
    PlanItem,
    ReconstructionError,
    _apply_pbs_items,
    build_plan,
    build_v21_1_item_description_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import RubyString, load
from ruby_marshal_writer import dumps
from structured_extractor import extract_pbs, extract_structured_verified, stable_id
from translation_project import TranslationProjectError, TranslationProjectSession
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


TARGET_SECTION = "POTION"
SHARED_SECTION = "ORANBERRY"
TEST_TRANSLATION = "[TEST PFT v21.1 ITEM DESCRIPTION]"


def prepare_item_project(base: Path, *, shared: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, item_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == ITEM_PBS_FILE
        and row["type"] == "PBS — Description"
        and row["evenement_id"] == (SHARED_SECTION if shared else TARGET_SECTION)
    )
    selected["traduction_fr"] = TEST_TRANSLATION
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_item"
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


class EssentialsItemDescriptionTests(unittest.TestCase):
    def test_extraction_links_only_five_text_fields_and_excludes_technical_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, item_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [row for row in result.rows if row["fichier"] == ITEM_PBS_FILE]

        self.assertEqual(16, len(rows))
        self.assertEqual(
            {"Name", "NamePlural", "PortionName", "PortionNamePlural", "Description"},
            {row["commande"] for row in rows},
        )
        for technical in {
            "Pocket", "Price", "SellPrice", "BPPrice", "FieldUse", "BattleUse",
            "Flags", "Consumable", "ShowQuantity", "Move",
        }:
            self.assertNotIn(technical, {row["commande"] for row in rows})
        descriptions = [row for row in rows if row["commande"] == "Description"]
        self.assertEqual(
            [1, 2, 2, 1],
            [
                json.loads(row["pbs_runtime_structure"])["source_usage_count"]
                for row in descriptions
            ],
        )
        for row in rows:
            pbs = json.loads(row["pbs_structure"])
            compiled = json.loads(row["pbs_compiled_structure"])
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(ITEM_PBS_PROOF_FORMAT, pbs["format"])
            self.assertEqual(COMPILED_ITEM_PROOF_FORMAT, compiled["format"])
            self.assertEqual(ITEM_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(COMPILED_ITEM_FILE, row["pbs_compiled_file"])
            self.assertEqual(ITEM_MESSAGES_FILE, row["pbs_runtime_file"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])

    def test_string_looking_item_technical_fields_are_never_translatable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_technical_") as temporary:
            path = Path(temporary) / "items.txt"
            path.write_text(
                "[TM001]\nName = TM01\nNamePlural = TM01s\nPocket = 4\n"
                "Price = 3000\nFieldUse = TM\nFlags = KeyItem,Fling_30\n"
                "Move = TACKLE\nDescription = A visible description.\n",
                encoding="utf-8",
            )
            rows = extract_pbs(path, ITEM_PBS_FILE)

        self.assertEqual(
            {"Name", "NamePlural", "Description"},
            {row["commande"] for row in rows},
        )
        self.assertFalse(
            {"TM", "KeyItem,Fling_30", "TACKLE"}
            & {row["texte_source"] for row in rows}
        )

    def test_generic_pbs_reinjection_can_never_write_item_move_or_flags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_write_") as temporary:
            path = Path(temporary) / "items.txt"
            original = (
                "[TM001]\r\nName = TM01\r\nNamePlural = TM01s\r\n"
                "Pocket = 4\r\nPrice = 3000\r\nFlags = KeyItem\r\n"
                "Move = TACKLE\r\nDescription = A machine.\r\n"
            ).encode("utf-8")
            path.write_bytes(original)
            for field, source, translation in (
                ("Move", "TACKLE", "GROWL"),
                ("Flags", "KeyItem", "Mail"),
            ):
                item = PlanItem(
                    id_stable=stable_id("pbs", ITEM_PBS_FILE, "TM001", field, 1),
                    type=f"PBS — {field}",
                    fichier=ITEM_PBS_FILE,
                    source=source,
                    translation=translation,
                    status="Accepté",
                )
                with self.subTest(field=field), self.assertRaisesRegex(
                    ReconstructionError, "introuvable"
                ):
                    _apply_pbs_items(path, ITEM_PBS_FILE, [item])
                self.assertEqual(original, path.read_bytes())

    def test_item_correlation_is_never_applied_to_a_legacy_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_legacy_item_guard_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, item_validation=True)
            result = extract_structured_verified(
                root,
                essentials_profile="essentials_legacy_rxmp",
            )
            rows = [row for row in result.rows if row["fichier"] == ITEM_PBS_FILE]

        self.assertEqual(16, len(rows))
        self.assertTrue(all(not row["pbs_compiled_structure"] for row in rows))
        self.assertTrue(all(not row["pbs_runtime_structure"] for row in rows))

    def test_private_roundtrip_changes_exactly_three_files_and_only_description(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_item_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)
            compiled_before = load(root / COMPILED_ITEM_FILE)

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

            plan = build_v21_1_item_description_validation_plan(root, csv_path)
            self.assertEqual(V21_1_ITEM_DESCRIPTION_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(
                {ITEM_PBS_FILE, COMPILED_ITEM_FILE, ITEM_MESSAGES_FILE},
                set(plan.source_hashes),
            )
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(
                sorted([ITEM_PBS_FILE, COMPILED_ITEM_FILE, ITEM_MESSAGES_FILE]),
                result.modified_files,
            )
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            translated = extract_item_description_texts(
                (target / ITEM_PBS_FILE).read_bytes(),
                (target / COMPILED_ITEM_FILE).read_bytes(),
                (target / ITEM_MESSAGES_FILE).read_bytes(),
                section=selected["evenement_id"],
            )
            self.assertEqual((selected["traduction_fr"],) * 3, translated)
            compiled_after = load(target / COMPILED_ITEM_FILE)
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
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed={ITEM_PBS_FILE, COMPILED_ITEM_FILE, ITEM_MESSAGES_FILE},
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_proofs_refuse_technical_format_and_bank_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, item_validation=True)
            pbs = (root / ITEM_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_ITEM_FILE).read_bytes()
            runtime = (root / ITEM_MESSAGES_FILE).read_bytes()

            compiled_root = load(root / COMPILED_ITEM_FILE)
            compiled_root[TARGET_SECTION].ivars["@move"] = "TACKLE"
            with self.assertRaisesRegex(ItemIntegrityError, "techniques|métadonnées|Move"):
                build_item_text_proofs(pbs, dumps(compiled_root), runtime)

            with self.assertRaisesRegex(ItemIntegrityError, "techniques|métadonnées"):
                build_item_text_proofs(
                    pbs.replace(b"Flags = Fling_30", b"Flags = KeyItem", 1),
                    compiled,
                    runtime,
                )

            for bad_pbs in (pbs[3:], pbs.replace(b"\r\n", b"\n")):
                with self.subTest(prefix=bad_pbs[:12]), self.assertRaises(ItemIntegrityError):
                    build_item_text_proofs(bad_pbs, compiled, runtime)

            runtime_root = load(root / ITEM_MESSAGES_FILE)
            potion = next(
                key for key in runtime_root[9]
                if isinstance(key, RubyString)
                and key.text() == "Synthetic unique item description."
            )
            value = runtime_root[9].pop(potion)
            runtime_root[9][
                RubyString(b"Obsolete description key", dict(potion.ivars))
            ] = value
            proofs = build_item_text_proofs(pbs, compiled, dumps(runtime_root))
            self.assertNotIn((TARGET_SECTION, "Description", 1), proofs)

    def test_shared_description_is_extractable_but_reconstruction_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_shared_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_item_project(base, shared=True)
            with self.assertRaisesRegex(ReconstructionError, "partagée|correspond"):
                build_v21_1_item_description_validation_plan(root, csv_path)

            proofs = build_item_text_proofs(
                (root / ITEM_PBS_FILE).read_bytes(),
                (root / COMPILED_ITEM_FILE).read_bytes(),
                (root / ITEM_MESSAGES_FILE).read_bytes(),
            )
            proof = proofs[(selected["evenement_id"], "Description", 1)]
            with self.assertRaisesRegex(ItemIntegrityError, "partagée"):
                rebuild_item_description_payloads(
                    (root / ITEM_PBS_FILE).read_bytes(),
                    (root / COMPILED_ITEM_FILE).read_bytes(),
                    (root / ITEM_MESSAGES_FILE).read_bytes(),
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
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_guard_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_item_project(base)
            plan = build_v21_1_item_description_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            item.pbs_compiled_structure = "{}"
            with self.assertRaisesRegex(ReconstructionError, "preuve|correspond"):
                simulate_plan(plan)

            previous = csv_path.read_bytes()
            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).__setitem__("pbs_runtime_path", "[9,\"entry\",999]"),
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
            self.assertEqual(previous, csv_path.read_bytes())

    def test_plan_refuses_technical_source_changed_after_planning(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_toctou_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_item_project(base)
            plan = build_v21_1_item_description_validation_plan(root, csv_path)
            compiled_root = load(root / COMPILED_ITEM_FILE)
            compiled_root[TARGET_SECTION].ivars["@flags"] = ["KeyItem"]
            (root / COMPILED_ITEM_FILE).write_bytes(dumps(compiled_root))

            with self.assertRaisesRegex(ReconstructionError, "preuve|source|métadonnées"):
                simulate_plan(plan)

    def test_bundle_failure_rolls_back_all_three_game_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_item_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_item_project(base)
            target = base / "candidate"
            plan = build_v21_1_item_description_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (ITEM_PBS_FILE, COMPILED_ITEM_FILE, ITEM_MESSAGES_FILE)
            }
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic item publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")

            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
