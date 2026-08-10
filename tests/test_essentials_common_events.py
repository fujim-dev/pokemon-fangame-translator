from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from adapters import ESSENTIALS_V21_1_READONLY_PROFILE, PokemonEssentialsAdapter
from analysis.integrity import compare_snapshots, snapshot_tree
from reconstruction_engine import (
    V21_1_COMMON_EVENT_401_VALIDATION_SCOPE,
    V21_1_COMMON_EVENT_CHOICE_VALIDATION_SCOPE,
    V21_1_COMMON_EVENT_SINGLE_VALIDATION_SCOPE,
    V21_1_MKXP_MAX_CANDIDATE_ROOT_CHARS,
    V21_1_COMMON_EVENTS_VALIDATION_SCOPE,
    ReconstructionError,
    build_plan,
    build_v21_1_common_event_401_validation_plan,
    build_v21_1_common_event_choice_validation_plan,
    build_v21_1_common_event_single_validation_plan,
    build_v21_1_common_events_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from rpg_dialogue import DialogueSegmentationError
from ruby_marshal_reader import RubyObject, load
from ruby_marshal_writer import dumps
from structured_extractor import extract_common_events
from project_test_support import finalize_verified_essentials_project
from test_essentials_v21 import (
    event_command,
    prepare_v21_game,
    ruby_text,
    write_extracted_project_csv,
)


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


def prepare_single_dialogue_project(base: Path):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, common_event_corpus=True)
    rows = [
        dict(row)
        for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
    ]
    selected = [
        row
        for row in rows
        if row["type"] == "Événement commun — Dialogue"
        and row["evenement_id"] == 1
        and row["commande"] == 1
    ]
    if len(selected) != 1:
        raise AssertionError("La fixture doit exposer un dialogue commun simple")
    selected[0]["traduction_fr"] = (
        selected[0]["texte_source"] + " [TEST PFT COMMON SINGLE]"
    )
    selected[0]["statut"] = "Accepté"
    csv_path = project / "textes_structures.csv"
    write_extracted_project_csv(csv_path, rows)
    finalize_verified_essentials_project(
        root,
        csv_path,
        adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
        declared_version="21.1",
    )
    return root, csv_path, selected[0]


def prepare_continuation_dialogue_project(base: Path):
    """Crée une preuve synthétique bornée à une séquence 101 + une 401."""
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, common_event_corpus=True)
    common_path = root / COMMON_EVENTS_FILE
    common_events = load(common_path)
    commands = common_events[1].ivars["@list"]
    commands.pop(5)
    commands[4].ivars["@parameters"][0] = ruby_text(
        r"Continuation \n with \n internal controls"
    )
    common_path.write_bytes(dumps(common_events))

    rows = [
        dict(row)
        for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
    ]
    selected = [
        row
        for row in rows
        if row["type"] == "Événement commun — Dialogue"
        and row["evenement_id"] == 1
        and row["commande"] == 3
    ]
    if len(selected) != 1:
        raise AssertionError("La fixture doit exposer une séquence commune 101 + 401")
    selected[0]["traduction_fr"] = (
        selected[0]["texte_source"] + " [TEST PFT COMMON 401]"
    )
    selected[0]["statut"] = "Accepté"
    csv_path = project / "textes_structures.csv"
    write_extracted_project_csv(csv_path, rows)
    finalize_verified_essentials_project(
        root,
        csv_path,
        adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
        declared_version="21.1",
    )
    return root, csv_path, selected[0]


def prepare_choice_project(base: Path):
    """Crée une preuve synthétique bornée à un libellé 102 et sa branche 402."""
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, common_event_corpus=True)
    rows = [
        dict(row)
        for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
    ]
    selected = [
        row
        for row in rows
        if row["type"] == "Événement commun — Choix"
        and row["evenement_id"] == 1
        and row["commande"] == 6
        and row["sous_index"] == 0
    ]
    if len(selected) != 1:
        raise AssertionError("La fixture doit exposer un choix commun 102/402 unique")
    selected[0]["traduction_fr"] = (
        selected[0]["texte_source"] + " [TEST PFT COMMON CHOICE]"
    )
    selected[0]["statut"] = "Accepté"
    csv_path = project / "textes_structures.csv"
    write_extracted_project_csv(csv_path, rows)
    finalize_verified_essentials_project(
        root,
        csv_path,
        adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
        declared_version="21.1",
    )
    return root, csv_path, selected[0]


class EssentialsCommonEventCandidateTests(unittest.TestCase):
    def test_choice_scope_is_private_bounded_and_preserves_full_structure(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_choice_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_choice_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")
            with self.assertRaisesRegex(ReconstructionError, "dialogue"):
                build_v21_1_common_event_single_validation_plan(root, csv_path)
            plan = build_v21_1_common_event_choice_validation_plan(root, csv_path)
            self.assertEqual(
                V21_1_COMMON_EVENT_CHOICE_VALIDATION_SCOPE,
                plan.validation_scope,
            )
            self.assertEqual(1, plan.counts().get("applicable", 0))
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual([COMMON_EVENTS_FILE], result.modified_files)
            self.assertEqual(1, result.applied)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)

            original = load(root / COMMON_EVENTS_FILE)
            candidate = load(target / COMMON_EVENTS_FILE)
            self.assertEqual(len(original), len(candidate))
            for event_index, (original_event, candidate_event) in enumerate(
                zip(original, candidate)
            ):
                if event_index != 1:
                    self.assertEqual(dumps(original_event), dumps(candidate_event))
                    continue
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
                for command_index, (original_command, candidate_command) in enumerate(
                    zip(original_commands, candidate_commands)
                ):
                    if command_index not in (6, 7):
                        self.assertEqual(dumps(original_command), dumps(candidate_command))
                        continue
                    self.assertEqual(set(original_command.ivars), set(candidate_command.ivars))
                    for field in original_command.ivars:
                        if field != "@parameters":
                            self.assertEqual(
                                dumps(original_command.ivars[field]),
                                dumps(candidate_command.ivars[field]),
                            )
                original_choice = original_commands[6].ivars["@parameters"]
                candidate_choice = candidate_commands[6].ivars["@parameters"]
                self.assertEqual(dumps(original_choice[1:]), dumps(candidate_choice[1:]))
                self.assertEqual(dumps(original_choice[0][1:]), dumps(candidate_choice[0][1:]))
                self.assertEqual(original_choice[0][0].ivars, candidate_choice[0][0].ivars)
                original_branch = original_commands[7].ivars["@parameters"]
                candidate_branch = candidate_commands[7].ivars["@parameters"]
                self.assertEqual(dumps(original_branch[:1]), dumps(candidate_branch[:1]))
                self.assertEqual(original_branch[1].ivars, candidate_branch[1].ivars)

            reextracted = {
                row["id_stable"]: row
                for row in extract_common_events(
                    target / COMMON_EVENTS_FILE,
                    COMMON_EVENTS_FILE,
                    strict=True,
                )
            }
            rebuilt = reextracted[selected["id_stable"]]
            self.assertEqual(selected["traduction_fr"], rebuilt["texte_source"])
            self.assertEqual(7, rebuilt["rpg_choice_branch_command"])
            self.assertEqual(1, rebuilt["rpg_choice_branch_parameter_index"])

    def test_choice_scope_refuses_structural_changes_and_altered_proof(self) -> None:
        for mutation in (
            "missing_branch",
            "additional_branch",
            "changed_subindex",
            "changed_order",
            "changed_indent",
            "changed_non_text_parameter",
            "changed_text_whitespace",
            "altered_proof",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_common_choice_changed_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_choice_project(base)
                plan = build_v21_1_common_event_choice_validation_plan(root, csv_path)
                item = next(candidate for candidate in plan.items if candidate.decision == "applicable")
                common_path = root / COMMON_EVENTS_FILE

                if mutation == "altered_proof":
                    item.rpg_choice_branch_command = "8"
                else:
                    common_events = load(common_path)
                    commands = common_events[1].ivars["@list"]
                    if mutation == "missing_branch":
                        commands.pop(7)
                    elif mutation == "additional_branch":
                        commands.insert(
                            8,
                            event_command(
                                402,
                                [0, ruby_text("First synthetic common choice")],
                            ),
                        )
                    elif mutation == "changed_subindex":
                        commands[7].ivars["@parameters"][0] = 1
                    elif mutation == "changed_order":
                        commands.insert(9, commands.pop(7))
                    elif mutation == "changed_indent":
                        commands[7].ivars["@indent"] = 1
                    elif mutation == "changed_text_whitespace":
                        commands[6].ivars["@parameters"][0][0] = ruby_text(
                            " First synthetic common choice "
                        )
                        commands[7].ivars["@parameters"][1] = ruby_text(
                            " First synthetic common choice "
                        )
                    else:
                        commands[6].ivars["@parameters"][1] = 99
                    payload = dumps(common_events)
                    common_path.write_bytes(payload)
                    plan.source_hashes[COMMON_EVENTS_FILE] = hashlib.sha256(payload).hexdigest()
                    if mutation != "changed_non_text_parameter":
                        item.rpg_common_event_sha256 = hashlib.sha256(
                            dumps(common_events[1])
                        ).hexdigest()

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "bloquée|102|402|branche|empreinte",
                ):
                    simulate_plan(plan)

    def test_choice_scope_refuses_ambiguous_branch_and_changed_provenance(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_choice_ambiguous_"
        ) as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            prepare_v21_game(root, common_event_corpus=True)
            common_path = root / COMMON_EVENTS_FILE
            common_events = load(common_path)
            common_events[1].ivars["@list"].insert(
                8,
                event_command(
                    402,
                    [0, ruby_text("First synthetic common choice")],
                ),
            )
            common_path.write_bytes(dumps(common_events))
            rows = [
                dict(row)
                for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
            ]
            selected = next(
                row
                for row in rows
                if row["type"] == "Événement commun — Choix"
                and row["evenement_id"] == 1
                and row["sous_index"] == 0
            )
            self.assertEqual("", selected["rpg_choice_branch_command"])
            self.assertEqual("", selected["rpg_choice_branch_parameter_index"])
            selected["traduction_fr"] = selected["texte_source"] + " [TEST]"
            selected["statut"] = "Accepté"
            csv_path = project / "textes_structures.csv"
            write_extracted_project_csv(csv_path, rows)
            finalize_verified_essentials_project(
                root,
                csv_path,
                adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                declared_version="21.1",
            )
            with self.assertRaisesRegex(ReconstructionError, "invalide|102/402"):
                build_v21_1_common_event_choice_validation_plan(root, csv_path)

        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_choice_provenance_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_choice_project(base)
            common_path = root / COMMON_EVENTS_FILE
            common_events = load(common_path)
            common_events[1].ivars["@trigger"] = 2
            common_path.write_bytes(dumps(common_events))
            with self.assertRaisesRegex(ReconstructionError, "sources Essentials"):
                build_v21_1_common_event_choice_validation_plan(root, csv_path)

    def test_401_scope_is_private_bounded_and_preserves_full_structure(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_401_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_continuation_dialogue_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")
            with self.assertRaisesRegex(ReconstructionError, "preuve|structurelle"):
                build_v21_1_common_event_single_validation_plan(root, csv_path)

            plan = build_v21_1_common_event_401_validation_plan(root, csv_path)
            self.assertEqual(V21_1_COMMON_EVENT_401_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(1, plan.counts().get("applicable", 0))
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual([COMMON_EVENTS_FILE], result.modified_files)
            self.assertEqual(1, result.applied)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)

            original = load(root / COMMON_EVENTS_FILE)
            candidate = load(target / COMMON_EVENTS_FILE)
            self.assertEqual(len(original), len(candidate))
            for event_index, (original_event, candidate_event) in enumerate(
                zip(original, candidate)
            ):
                if event_index != 1:
                    self.assertEqual(dumps(original_event), dumps(candidate_event))
                    continue
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
                    if command_index not in (3, 4):
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

            reextracted = {
                row["id_stable"]: row["texte_source"]
                for row in extract_common_events(
                    target / COMMON_EVENTS_FILE,
                    COMMON_EVENTS_FILE,
                    strict=True,
                )
            }
            self.assertEqual(selected["traduction_fr"], reextracted[selected["id_stable"]])

    def test_401_scope_refuses_changed_command_stream(self) -> None:
        for mutation in (
            "missing_401",
            "extra_401",
            "misindented_401",
            "reordered",
            "unexpected_parameter",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_common_401_changed_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_continuation_dialogue_project(base)
                plan = build_v21_1_common_event_401_validation_plan(root, csv_path)
                common_path = root / COMMON_EVENTS_FILE
                common_events = load(common_path)
                commands = common_events[1].ivars["@list"]
                if mutation == "missing_401":
                    commands.pop(4)
                elif mutation == "extra_401":
                    commands.insert(
                        5,
                        event_command(
                            401,
                            [ruby_text("Unexpected continuation")],
                            indent=1,
                        ),
                    )
                elif mutation == "misindented_401":
                    commands[4].ivars["@indent"] = 0
                elif mutation == "reordered":
                    commands[3], commands[4] = commands[4], commands[3]
                else:
                    commands[4].ivars["@parameters"].append(99)
                common_path.write_bytes(dumps(common_events))

                with self.assertRaisesRegex(ReconstructionError, "bloquée"):
                    simulate_plan(plan)
                self.assertTrue(any(
                    "simulation" in item.reason.casefold()
                    for item in plan.items
                    if item.status == "Accepté"
                ))

    def test_401_scope_refuses_altered_segmentation_proof(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_401_proof_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_continuation_dialogue_project(base)
            plan = build_v21_1_common_event_401_validation_plan(root, csv_path)
            item = next(item for item in plan.items if item.decision == "applicable")
            metadata = json.loads(item.rpg_dialogue_segments)
            metadata["segments"][1]["command_index"] += 1
            item.rpg_dialogue_segments = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

            with self.assertRaisesRegex(ReconstructionError, "101/401|preuve"):
                simulate_plan(plan)

    def test_401_scope_refuses_changed_source_provenance(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_401_provenance_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_continuation_dialogue_project(base)
            common_path = root / COMMON_EVENTS_FILE
            common_events = load(common_path)
            common_events[1].ivars["@trigger"] = 2
            common_path.write_bytes(dumps(common_events))

            with self.assertRaisesRegex(ReconstructionError, "sources Essentials"):
                build_v21_1_common_event_401_validation_plan(root, csv_path)

    def test_single_common_event_scope_is_private_bounded_and_reextractable(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_single_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_single_dialogue_project(base)
            target = base / "candidate"
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")
            with self.assertRaisesRegex(ReconstructionError, "trois"):
                build_v21_1_common_events_validation_plan(root, csv_path)

            plan = build_v21_1_common_event_single_validation_plan(root, csv_path)
            self.assertEqual(
                V21_1_COMMON_EVENT_SINGLE_VALIDATION_SCOPE,
                plan.validation_scope,
            )
            self.assertEqual(1, plan.counts().get("applicable", 0))
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual([COMMON_EVENTS_FILE], result.modified_files)
            self.assertEqual(1, result.applied)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            reextracted = {
                row["id_stable"]: row["texte_source"]
                for row in extract_common_events(
                    target / COMMON_EVENTS_FILE,
                    COMMON_EVENTS_FILE,
                    strict=True,
                )
            }
            self.assertEqual(
                selected["traduction_fr"],
                reextracted[selected["id_stable"]],
            )

    def test_single_common_event_scope_refuses_source_changed_after_plan(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_single_changed_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_single_dialogue_project(base)
            plan = build_v21_1_common_event_single_validation_plan(root, csv_path)
            common_path = root / COMMON_EVENTS_FILE
            common_events = load(common_path)
            common_events[1].ivars["@trigger"] = 2
            common_path.write_bytes(dumps(common_events))

            with self.assertRaisesRegex(ReconstructionError, "bloquée"):
                simulate_plan(plan)
            self.assertTrue(any(
                "simulation" in item.reason.casefold()
                for item in plan.items
                if item.status == "Accepté"
            ))

    def test_single_common_event_scope_refuses_unlaunchable_long_mkxp_path(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="pft_test_v21_common_long_target_"
        ) as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_single_dialogue_project(base)
            plan = simulate_plan(
                build_v21_1_common_event_single_validation_plan(root, csv_path)
            )
            missing = V21_1_MKXP_MAX_CANDIDATE_ROOT_CHARS + 1 - len(str(base.resolve()))
            target = base / ("x" * max(1, missing))
            self.assertGreater(
                len(str(target.resolve())),
                V21_1_MKXP_MAX_CANDIDATE_ROOT_CHARS,
            )

            with self.assertRaisesRegex(ReconstructionError, "trop long.*mkxp-z"):
                reconstruct_copy(plan, target, base / "reports")

            self.assertFalse(target.exists())

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
