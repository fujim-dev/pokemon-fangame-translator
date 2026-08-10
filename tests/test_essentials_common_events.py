from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters import ESSENTIALS_V21_1_READONLY_PROFILE, PokemonEssentialsAdapter
from analysis.integrity import compare_snapshots, snapshot_tree
from reconstruction_engine import (
    V21_1_COMMON_EVENTS_VALIDATION_SCOPE,
    ReconstructionError,
    build_plan,
    build_v21_1_common_events_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from rpg_dialogue import DialogueSegmentationError
from ruby_marshal_reader import RubyObject, load
from ruby_marshal_writer import dumps
from structured_extractor import extract_common_events
from project_test_support import finalize_verified_essentials_project
from test_essentials_v21 import prepare_v21_game, ruby_text, write_extracted_project_csv


COMMON_EVENTS_FILE = "Data/CommonEvents.rxdata"


def prepare_project(base: Path, *, explicit_non_utf8_encoding: bool = False):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, common_event_corpus=True)
    if explicit_non_utf8_encoding:
        common_path = root / COMMON_EVENTS_FILE
        common_events = load(common_path)
        common_events[1].ivars["@list"][1].ivars["@parameters"][0].ivars[
            "encoding"
        ] = ruby_text("Windows-1252")
        common_path.write_bytes(dumps(common_events))
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    dialogues = [
        row
        for row in rows
        if row["type"] == "Événement commun — Dialogue"
    ]
    if len(dialogues) != 3:
        raise AssertionError("La fixture doit exposer exactement trois dialogues communs")
    translations = {
        (1, 1): "Translated simple common dialogue [TEST]",
        (1, 3): (
            r"Translated \n internal common control"
            r"\nTranslated second common line"
            r"\nTranslated third common line"
        ),
        (2, 1): "Translated second event dialogue [TEST]",
    }
    for row in dialogues:
        row["traduction_fr"] = translations[(row["evenement_id"], row["commande"])]
        row["statut"] = "Accepté"
    csv_path = project / "textes_structures.csv"
    write_extracted_project_csv(csv_path, rows)
    finalize_verified_essentials_project(
        root,
        csv_path,
        adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
        declared_version="21.1",
    )
    return root, csv_path, rows, dialogues


class EssentialsCommonEventCandidateTests(unittest.TestCase):
    def test_common_event_dialogue_corpus_roundtrip_preserves_full_structure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_common_roundtrip_") as temporary:
            base = Path(temporary)
            root, csv_path, _rows, dialogues = prepare_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")
            plan = build_v21_1_common_events_validation_plan(root, csv_path)
            self.assertEqual(V21_1_COMMON_EVENTS_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(3, plan.counts().get("applicable", 0))
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual([COMMON_EVENTS_FILE], result.modified_files)
            self.assertEqual(3, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)

            original = load(root / COMMON_EVENTS_FILE)
            candidate = load(target / COMMON_EVENTS_FILE)
            self.assertEqual(len(original), len(candidate))
            target_commands: dict[int, set[int]] = {1: set(), 2: set()}
            for row in dialogues:
                start = int(row["commande"])
                end = int(row["rpg_continuation_end"])
                target_commands[int(row["rpg_common_event_array_index"])].update(
                    range(start, end + 1)
                )
            for event_index in (1, 2):
                original_event = original[event_index]
                candidate_event = candidate[event_index]
                self.assertIsInstance(original_event, RubyObject)
                self.assertIsInstance(candidate_event, RubyObject)
                self.assertEqual(set(original_event.ivars), set(candidate_event.ivars))
                for field in original_event.ivars:
                    if field != "@list":
                        self.assertEqual(
                            dumps(original_event.ivars[field]),
                            dumps(candidate_event.ivars[field]),
                        )
                original_commands = original_event.ivars["@list"]
                candidate_commands = candidate_event.ivars["@list"]
                self.assertEqual(len(original_commands), len(candidate_commands))
                self.assertEqual(
                    [
                        (command.ivars["@code"], command.ivars["@indent"])
                        for command in original_commands
                    ],
                    [
                        (command.ivars["@code"], command.ivars["@indent"])
                        for command in candidate_commands
                    ],
                )
                for command_index, (original_command, candidate_command) in enumerate(
                    zip(original_commands, candidate_commands)
                ):
                    if command_index not in target_commands[event_index]:
                        self.assertEqual(dumps(original_command), dumps(candidate_command))
                        continue
                    self.assertEqual(set(original_command.ivars), set(candidate_command.ivars))
                    for field in original_command.ivars:
                        if field != "@parameters":
                            self.assertEqual(
                                dumps(original_command.ivars[field]),
                                dumps(candidate_command.ivars[field]),
                            )
                    original_parameters = original_command.ivars["@parameters"]
                    candidate_parameters = candidate_command.ivars["@parameters"]
                    self.assertEqual(len(original_parameters), len(candidate_parameters))
                    self.assertEqual(
                        dumps(original_parameters[1:]),
                        dumps(candidate_parameters[1:]),
                    )
                    self.assertEqual(
                        original_parameters[0].ivars,
                        candidate_parameters[0].ivars,
                    )

            reextracted_rows = extract_common_events(
                target / COMMON_EVENTS_FILE,
                COMMON_EVENTS_FILE,
                strict=True,
            )
            reextracted = {
                row["id_stable"]: row["texte_source"] for row in reextracted_rows
            }
            for row in dialogues:
                self.assertEqual(row["traduction_fr"], reextracted[row["id_stable"]])
            original_choices = {
                row["id_stable"]: row["texte_source"]
                for row in extract_common_events(
                    root / COMMON_EVENTS_FILE,
                    COMMON_EVENTS_FILE,
                    strict=True,
                )
                if row["type"] == "Événement commun — Choix"
            }
            candidate_choices = {
                row["id_stable"]: row["texte_source"]
                for row in reextracted_rows
                if row["type"] == "Événement commun — Choix"
            }
            self.assertEqual(original_choices, candidate_choices)

    def test_scope_refuses_incomplete_or_mixed_occurrence_sets(self) -> None:
        for mutation in ("missing_dialogue", "accepted_choice"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_common_scope_"
            ) as temporary:
                base = Path(temporary)
                root = base / "game"
                project = base / "project"
                prepare_v21_game(root, common_event_corpus=True)
                rows = [
                    dict(row)
                    for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
                ]
                dialogues = [
                    row for row in rows if row["type"] == "Événement commun — Dialogue"
                ]
                selected = dialogues[:2] if mutation == "missing_dialogue" else dialogues
                if mutation == "accepted_choice":
                    selected.append(next(
                        row for row in rows if row["type"] == "Événement commun — Choix"
                    ))
                for row in selected:
                    row["traduction_fr"] = row["texte_source"] + " [TEST]"
                    row["statut"] = "Accepté"
                csv_path = project / "textes_structures.csv"
                write_extracted_project_csv(csv_path, rows)
                finalize_verified_essentials_project(
                    root,
                    csv_path,
                    adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                    declared_version="21.1",
                )

                with self.assertRaisesRegex(ReconstructionError, "trois|dialogue"):
                    build_v21_1_common_events_validation_plan(root, csv_path)

    def test_missing_replaced_or_structurally_changed_event_is_refused(self) -> None:
        for mutation in (
            "missing",
            "replaced",
            "invalid_array_sentinel",
            "changed_id",
            "misindented_401",
            "unexpected_parameter",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_common_changed_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _rows, _dialogues = prepare_project(base)
                plan = build_v21_1_common_events_validation_plan(root, csv_path)
                common_path = root / COMMON_EVENTS_FILE
                common_events = load(common_path)
                if mutation == "missing":
                    common_events[2] = None
                elif mutation == "replaced":
                    common_events[2] = common_events[1]
                elif mutation == "invalid_array_sentinel":
                    common_events[0] = common_events[2]
                elif mutation == "changed_id":
                    common_events[1].ivars["@id"] = 99
                elif mutation == "misindented_401":
                    common_events[1].ivars["@list"][4].ivars["@indent"] = 0
                else:
                    common_events[1].ivars["@list"][1].ivars["@parameters"].append(99)
                common_path.write_bytes(dumps(common_events))

                with self.assertRaisesRegex(ReconstructionError, "bloquée"):
                    simulate_plan(plan)
                self.assertTrue(any(
                    "simulation" in item.reason.casefold()
                    for item in plan.items
                    if item.status == "Accepté"
                ))

    def test_strict_extraction_refuses_orphan_and_misindented_401(self) -> None:
        for mutation in ("orphan_401", "misindented_401"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_common_invalid_stream_"
            ) as temporary:
                root = Path(temporary) / "game"
                prepare_v21_game(root, common_event_corpus=True)
                common_path = root / COMMON_EVENTS_FILE
                common_events = load(common_path)
                commands = common_events[1].ivars["@list"]
                if mutation == "orphan_401":
                    commands[8].ivars["@code"] = 401
                else:
                    commands[4].ivars["@indent"] = 0
                common_path.write_bytes(dumps(common_events))

                with self.assertRaisesRegex(
                    DialogueSegmentationError,
                    "401|Indentation",
                ):
                    extract_common_events(
                        common_path,
                        COMMON_EVENTS_FILE,
                        strict=True,
                    )

    def test_scope_refuses_incompatible_event_proofs_and_malformed_metadata(self) -> None:
        for mutation in ("incompatible_event_hash", "malformed_segmentation"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_common_proof_"
            ) as temporary:
                base = Path(temporary)
                root = base / "game"
                project = base / "project"
                prepare_v21_game(root, common_event_corpus=True)
                rows = [
                    dict(row)
                    for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
                ]
                dialogues = [
                    row
                    for row in rows
                    if row["type"] == "Événement commun — Dialogue"
                ]
                for row in dialogues:
                    row["traduction_fr"] = row["texte_source"] + " [TEST]"
                    row["statut"] = "Accepté"
                if mutation == "incompatible_event_hash":
                    dialogues[0]["rpg_common_event_sha256"] = "0" * 64
                    expected = "preuves incompatibles"
                else:
                    metadata = dialogues[0]["rpg_dialogue_segments"]
                    dialogues[0]["rpg_dialogue_segments"] = metadata.replace(
                        '"internal_line_control_count":0',
                        '"internal_line_control_count":"invalide"',
                        1,
                    )
                    expected = "contrôles internes|segmentation"
                csv_path = project / "textes_structures.csv"
                write_extracted_project_csv(csv_path, rows)
                finalize_verified_essentials_project(
                    root,
                    csv_path,
                    adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                    declared_version="21.1",
                )

                with self.assertRaisesRegex(ReconstructionError, expected):
                    build_v21_1_common_events_validation_plan(root, csv_path)

    def test_candidate_refuses_to_rewrite_ruby_string_encoding_metadata(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_encoding_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, _rows, _dialogues = prepare_project(
                base,
                explicit_non_utf8_encoding=True,
            )
            plan = build_v21_1_common_events_validation_plan(root, csv_path)

            with self.assertRaisesRegex(ReconstructionError, "bloquée"):
                simulate_plan(plan)
            self.assertTrue(any(
                "métadonnées d'encodage" in item.reason
                for item in plan.items
                if item.status == "Accepté"
            ))

    def test_changed_source_provenance_is_refused_before_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_common_provenance_") as temporary:
            base = Path(temporary)
            root, csv_path, _rows, _dialogues = prepare_project(base)
            common_path = root / COMMON_EVENTS_FILE
            common_events = load(common_path)
            common_events[1].ivars["@trigger"] = 2
            common_path.write_bytes(dumps(common_events))

            with self.assertRaisesRegex(ReconstructionError, "sources Essentials"):
                build_v21_1_common_events_validation_plan(root, csv_path)


if __name__ == "__main__":
    unittest.main()
