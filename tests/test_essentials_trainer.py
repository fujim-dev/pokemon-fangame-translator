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
from essentials_trainer import (
    COMPILED_TRAINER_FILE,
    COMPILED_TRAINER_PROOF_FORMAT,
    TRAINER_MESSAGES_FILE,
    TRAINER_PBS_FILE,
    TRAINER_PBS_PROOF_FORMAT,
    TRAINER_RUNTIME_PROOF_FORMAT,
    TrainerIntegrityError,
    build_trainer_entry_proofs,
    extract_trainer_target_texts,
    rebuild_trainer_payloads,
)
from reconstruction_engine import (
    V21_1_TRAINER_LOSE_VALIDATION_SCOPE,
    ReconstructionError,
    build_plan,
    build_v21_1_trainer_lose_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from structured_extractor import extract_pbs, extract_structured_verified
from translation_project import TranslationProjectError, TranslationProjectSession
from project_test_support import finalize_verified_essentials_project
from test_essentials_v21 import prepare_v21_game, write_extracted_project_csv


TARGET_SECTION = "YOUNGSTER,Synthetic Battler"
SHARED_SECTION = "CAMPER,Synthetic Shared One"


def prepare_trainer_project(base: Path, *, shared: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, trainer_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["fichier"] == TRAINER_PBS_FILE
        and row["type"] == "PBS — LoseText"
        and row["evenement_id"] == (SHARED_SECTION if shared else TARGET_SECTION)
    )
    selected["traduction_fr"] = (
        selected["texte_source"] + " [TEST PFT v21.1 TRAINER LOSE]"
    )
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_trainer_lose"
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


class EssentialsTrainerLoseTests(unittest.TestCase):
    def test_extraction_binds_pbs_compiled_and_runtime_structures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_trainer_extract_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, trainer_validation=True)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [row for row in result.rows if row["fichier"] == TRAINER_PBS_FILE]

        self.assertEqual(3, len(rows))
        usage_counts = []
        for row in rows:
            pbs = json.loads(row["pbs_structure"])
            compiled = json.loads(row["pbs_compiled_structure"])
            runtime = json.loads(row["pbs_runtime_structure"])
            self.assertEqual(TRAINER_PBS_PROOF_FORMAT, pbs["format"])
            self.assertEqual(COMPILED_TRAINER_PROOF_FORMAT, compiled["format"])
            self.assertEqual(TRAINER_RUNTIME_PROOF_FORMAT, runtime["format"])
            self.assertEqual(COMPILED_TRAINER_FILE, row["pbs_compiled_file"])
            self.assertEqual(TRAINER_MESSAGES_FILE, row["pbs_runtime_file"])
            self.assertNotIn(row["texte_source"], row["pbs_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])
            self.assertNotIn(row["texte_source"], row["pbs_runtime_structure"])
            usage_counts.append(runtime["source_usage_count"])
        self.assertEqual([1, 2, 2], usage_counts)

    def test_mega_message_numeric_selector_is_not_extractable_text(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_mega_selector_") as temporary:
            path = Path(temporary) / "pokemon_forms.txt"
            path.write_bytes(
                b"\xef\xbb\xbf[TEST,1]\r\n"
                b"FormName = Synthetic Mega\r\n"
                b"MegaMessage = 1\r\n"
            )
            rows = extract_pbs(path, "PBS/pokemon_forms.txt")

        self.assertEqual(["FormName"], [row["commande"] for row in rows])

    def test_trainer_correlation_is_never_applied_to_a_legacy_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_legacy_trainer_guard_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, trainer_validation=True)
            result = extract_structured_verified(
                root,
                essentials_profile="essentials_legacy_rxmp",
            )
            rows = [row for row in result.rows if row["fichier"] == TRAINER_PBS_FILE]

        self.assertEqual(3, len(rows))
        self.assertTrue(all(not row["pbs_compiled_structure"] for row in rows))
        self.assertTrue(all(not row["pbs_runtime_structure"] for row in rows))

    def test_private_roundtrip_changes_exactly_three_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_trainer_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_trainer_project(base)
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

            plan = build_v21_1_trainer_lose_validation_plan(root, csv_path)
            self.assertEqual(V21_1_TRAINER_LOSE_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(
                {TRAINER_PBS_FILE, COMPILED_TRAINER_FILE, TRAINER_MESSAGES_FILE},
                set(plan.source_hashes),
            )
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(
                sorted([TRAINER_PBS_FILE, COMPILED_TRAINER_FILE, TRAINER_MESSAGES_FILE]),
                result.modified_files,
            )
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            translated = extract_trainer_target_texts(
                (target / TRAINER_PBS_FILE).read_bytes(),
                (target / COMPILED_TRAINER_FILE).read_bytes(),
                (target / TRAINER_MESSAGES_FILE).read_bytes(),
                section=selected["evenement_id"],
            )
            self.assertEqual((selected["traduction_fr"],) * 3, translated)
            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed={
                    TRAINER_PBS_FILE,
                    COMPILED_TRAINER_FILE,
                    TRAINER_MESSAGES_FILE,
                },
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)

    def test_proofs_refuse_identity_bank_and_format_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_trainer_negative_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, trainer_validation=True)
            pbs = (root / TRAINER_PBS_FILE).read_bytes()
            compiled = (root / COMPILED_TRAINER_FILE).read_bytes()
            runtime = (root / TRAINER_MESSAGES_FILE).read_bytes()

            compiled_root = load(root / COMPILED_TRAINER_FILE)
            list(compiled_root.values())[0].ivars["@version"] = 1
            with self.assertRaisesRegex(TrainerIntegrityError, "métadonnées|identité"):
                build_trainer_entry_proofs(pbs, dumps(compiled_root), runtime)

            runtime_root = load(root / TRAINER_MESSAGES_FILE)
            runtime_root[23].pop(next(iter(runtime_root[23])))
            with self.assertRaisesRegex(TrainerIntegrityError, "couvre"):
                build_trainer_entry_proofs(pbs, compiled, dumps(runtime_root))

            for bad_pbs in (
                pbs[3:],
                pbs.replace(b"\r\n", b"\n"),
                pbs.replace(b"LoseText = Synthetic unique", b"Name = Synthetic unique"),
            ):
                with self.subTest(prefix=bad_pbs[:12]), self.assertRaises(TrainerIntegrityError):
                    build_trainer_entry_proofs(bad_pbs, compiled, runtime)

    def test_shared_lose_text_is_extractable_but_reconstruction_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_trainer_shared_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_trainer_project(base, shared=True)
            with self.assertRaisesRegex(ReconstructionError, "partagé|correspond"):
                build_v21_1_trainer_lose_validation_plan(root, csv_path)

            proofs = build_trainer_entry_proofs(
                (root / TRAINER_PBS_FILE).read_bytes(),
                (root / COMPILED_TRAINER_FILE).read_bytes(),
                (root / TRAINER_MESSAGES_FILE).read_bytes(),
            )
            proof = proofs[(selected["evenement_id"], "LoseText", 1)]
            with self.assertRaisesRegex(TrainerIntegrityError, "partagé"):
                rebuild_trainer_payloads(
                    (root / TRAINER_PBS_FILE).read_bytes(),
                    (root / COMPILED_TRAINER_FILE).read_bytes(),
                    (root / TRAINER_MESSAGES_FILE).read_bytes(),
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
        with tempfile.TemporaryDirectory(prefix="pft_v21_trainer_guard_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_trainer_project(base)
            plan = build_v21_1_trainer_lose_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            original_proof = item.pbs_runtime_structure
            item.pbs_runtime_structure = "{}"
            with self.assertRaisesRegex(ReconstructionError, "preuve|correspond"):
                simulate_plan(plan)

            item.pbs_runtime_structure = original_proof
            (root / COMPILED_TRAINER_FILE).write_bytes(
                (root / COMPILED_TRAINER_FILE).read_bytes() + b"x"
            )
            with self.assertRaisesRegex(ReconstructionError, "preuve|Marshal|source"):
                simulate_plan(plan)

            previous = csv_path.read_bytes()
            altered = rewrite_csv(
                csv_path,
                lambda rows: next(
                    row for row in rows if row["id_stable"] == selected["id_stable"]
                ).__setitem__("pbs_runtime_path", "[23,\"entry\",999]"),
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

    def test_bundle_failure_rolls_back_all_three_game_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_trainer_rollback_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_trainer_project(base)
            target = base / "candidate"
            plan = build_v21_1_trainer_lose_validation_plan(root, csv_path)
            simulate_plan(plan)
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (
                    TRAINER_PBS_FILE,
                    COMPILED_TRAINER_FILE,
                    TRAINER_MESSAGES_FILE,
                )
            }
            real_replace = safe_io._replace_file
            calls = 0

            def fail_second_publish(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic trainer publication failure")
                real_replace(source, destination)

            with patch("safe_io._replace_file", side_effect=fail_second_publish):
                with self.assertRaisesRegex(ReconstructionError, "transactionnelle"):
                    reconstruct_copy(plan, target, base / "reports")

            for relative, payload in originals.items():
                self.assertEqual(payload, (target / relative).read_bytes())
            self.assertTrue((target / "RECONSTRUCTION_INCOMPLETE.txt").is_file())


if __name__ == "__main__":
    unittest.main()
