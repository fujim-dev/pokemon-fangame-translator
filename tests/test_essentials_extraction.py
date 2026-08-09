from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import structured_extractor
from adapters import PokemonEssentialsAdapter
from ruby_marshal_reader import RubyObject, RubyString
from ruby_marshal_writer import dumps
from structured_extractor import ExtractionIntegrityError, extract_structured_verified


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def command(code: int, parameters: list) -> RubyObject:
    return RubyObject(
        "RPG::EventCommand",
        {"@code": code, "@indent": 0, "@parameters": parameters},
    )


def prepare_essentials_game(root: Path) -> None:
    markers = {
        "Game.exe": b"synthetic executable",
        "Game.ini": b"[Game]\nLibrary=RGSS104E.dll\n",
        "Data/System.rxdata": b"synthetic system",
    }
    for relative, payload in markers.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    page = RubyObject(
        "RPG::Event::Page",
        {"@list": [command(101, [ruby_text("Hello from the synthetic map.")])]},
    )
    event = RubyObject(
        "RPG::Event",
        {"@name": ruby_text("Synthetic event"), "@pages": [page]},
    )
    game_map = RubyObject("RPG::Map", {"@events": {1: event}})
    (root / "Data" / "Map001.rxdata").write_bytes(dumps(game_map))
    map_infos = {1: RubyObject("RPG::MapInfo", {"@name": ruby_text("Test map")})}
    (root / "Data" / "MapInfos.rxdata").write_bytes(dumps(map_infos))
    message_bank = {ruby_text("Hello from the bank."): ruby_text("Hello from the bank.")}
    (root / "Data" / "messages_game.dat").write_bytes(dumps(message_bank))

    pbs = root / "PBS" / "pokemon.txt"
    pbs.parent.mkdir(parents=True)
    pbs.write_text(
        "# Pokemon Essentials v21.1\n[TEST]\nName = Testmon\n"
        "Description = Hello from the synthetic PBS file.\n",
        encoding="utf-8",
    )


def file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class EssentialsExtractionIntegrityTests(unittest.TestCase):
    def test_common_events_are_extracted_with_precise_stable_identifiers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_common_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            common_event = RubyObject(
                "RPG::CommonEvent",
                {
                    "@id": 7,
                    "@name": ruby_text("Synthetic common event"),
                    "@list": [
                        command(101, [ruby_text(r"Hello \PN")]),
                        command(401, [ruby_text("Second line")]),
                        command(102, [[ruby_text("First choice"), ruby_text("Second choice")]]),
                    ],
                },
            )
            (root / "Data" / "CommonEvents.rxdata").write_bytes(
                dumps([None, common_event])
            )

            first = PokemonEssentialsAdapter().extract_with_provenance(root)
            second = PokemonEssentialsAdapter().extract_with_provenance(root)

            common_rows = [
                row for row in first.rows if row["fichier"] == "Data/CommonEvents.rxdata"
            ]
            second_common_rows = [
                row for row in second.rows if row["fichier"] == "Data/CommonEvents.rxdata"
            ]
            self.assertEqual(3, len(common_rows))
            self.assertEqual(
                [row["id_stable"] for row in common_rows],
                [row["id_stable"] for row in second_common_rows],
            )
            self.assertEqual(3, len({row["id_stable"] for row in common_rows}))
            self.assertEqual({7}, {row["evenement_id"] for row in common_rows})
            self.assertEqual({0, 2}, {row["commande"] for row in common_rows})
            self.assertEqual({101, 102}, {row["rpg_command_code"] for row in common_rows})
            self.assertEqual({0}, {row["rpg_parameter_index"] for row in common_rows})
            dialogue = next(
                row for row in common_rows if row["type"] == "Événement commun — Dialogue"
            )
            self.assertEqual(r"Hello \PN\nSecond line", dialogue["texte_source"])
            self.assertEqual(r"\PN | \n", dialogue["codes_proteges"])

    def test_common_event_dialogue_records_internal_controls_and_multiple_continuations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_common_segments_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            common_event = RubyObject(
                "RPG::CommonEvent",
                {
                    "@id": 9,
                    "@name": ruby_text("Synthetic segmented common event"),
                    "@list": [
                        command(108, [ruby_text("Neighbor before")]),
                        command(101, [ruby_text(r"First \n internal")]),
                        command(401, [ruby_text("Second segment")]),
                        command(401, [ruby_text(r"Third \n internal")]),
                        command(102, [[ruby_text("Neighbor choice")]]),
                    ],
                },
            )
            (root / "Data" / "CommonEvents.rxdata").write_bytes(
                dumps([None, common_event])
            )

            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            row = next(
                row
                for row in result.rows
                if row["type"] == "Événement commun — Dialogue"
            )
            metadata = json.loads(row["rpg_dialogue_segments"])

            self.assertEqual(1, row["commande"])
            self.assertEqual(3, row["rpg_continuation_end"])
            self.assertEqual([1, 2, 3], [
                segment["command_index"] for segment in metadata["segments"]
            ])
            self.assertEqual([101, 401, 401], [
                segment["command_code"] for segment in metadata["segments"]
            ])
            self.assertEqual([1, 0, 1], [
                segment["internal_line_control_count"]
                for segment in metadata["segments"]
            ])
            choice = next(
                row
                for row in result.rows
                if row["type"] == "Événement commun — Choix"
            )
            self.assertEqual(4, choice["commande"])

    def test_strict_extraction_refuses_orphan_or_misindented_continuations(self) -> None:
        invalid_commands = (
            [command(401, [ruby_text("Orphan")])],
            [
                command(101, [ruby_text("First")]),
                RubyObject(
                    "RPG::EventCommand",
                    {"@code": 401, "@indent": 1, "@parameters": [ruby_text("Second")]},
                ),
            ],
        )
        for commands in invalid_commands:
            with self.subTest(commands=commands), tempfile.TemporaryDirectory(
                prefix="pft_test_invalid_dialogue_stream_"
            ) as temp_dir:
                root = Path(temp_dir) / "game"
                prepare_essentials_game(root)
                page = RubyObject("RPG::Event::Page", {"@list": commands})
                event = RubyObject(
                    "RPG::Event",
                    {"@name": ruby_text("Invalid synthetic event"), "@pages": [page]},
                )
                (root / "Data" / "Map001.rxdata").write_bytes(
                    dumps(RubyObject("RPG::Map", {"@events": {1: event}}))
                )

                with self.assertRaisesRegex(
                    ExtractionIntegrityError,
                    "Map001.rxdata",
                ):
                    PokemonEssentialsAdapter().extract_with_provenance(root)

    def test_verified_extraction_is_deterministic_and_keeps_original_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_extract_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            before = file_hashes(root)

            first = PokemonEssentialsAdapter().extract_with_provenance(root)
            second = PokemonEssentialsAdapter().extract_with_provenance(root)

            self.assertEqual(before, file_hashes(root))
            self.assertEqual(first.source_manifest_sha256, second.source_manifest_sha256)
            self.assertEqual(first.rows, second.rows)
            self.assertEqual([], first.errors)
            self.assertEqual(7, len(first.sources))
            self.assertTrue(first.rows)
            self.assertEqual(
                {first.source_manifest_sha256},
                {row["source_manifest_sha256"] for row in first.rows},
            )
            self.assertTrue(all(row["source_sha256"] for row in first.rows))

    def test_redirected_source_is_refused_before_snapshot_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_redirect_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            redirected = (root / "PBS" / "pokemon.txt").resolve()

            def marks_pbs_file_as_redirected(path: Path) -> bool:
                return path.resolve() == redirected

            with patch(
                "structured_extractor._is_link_or_junction",
                side_effect=marks_pbs_file_as_redirected,
            ):
                with self.assertRaisesRegex(ExtractionIntegrityError, "redirig"):
                    extract_structured_verified(root)

    def test_source_replaced_between_inventory_and_snapshot_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_replace_") as temp_dir:
            actual_root = Path(temp_dir) / "game"
            prepare_essentials_game(actual_root)
            alias_parent = Path(temp_dir) / "path_alias"
            alias_parent.mkdir()
            root = alias_parent / ".." / actual_root.name
            pbs_alias = root / "PBS" / "pokemon.txt"
            pbs = actual_root.resolve() / "PBS" / "pokemon.txt"
            original = pbs.read_bytes()
            original_sha256 = hashlib.sha256(original).hexdigest()
            real_copy = structured_extractor.atomic_copy_file
            changed = False
            expected_sha256 = ""
            replacement_sha256 = ""

            self.assertNotEqual(pbs_alias, pbs)
            self.assertEqual(pbs_alias.resolve(), pbs)

            def replace_before_copy(source: Path, destination: Path, **kwargs) -> None:
                nonlocal changed, expected_sha256, replacement_sha256
                if Path(source).resolve() == pbs and not changed:
                    changed = True
                    expected_sha256 = str(kwargs.get("expected_sha256") or "")
                    replacement = pbs.with_name("pokemon.replacement")
                    replacement.write_bytes(original + b"# changed before copy\n")
                    replacement.replace(pbs)
                    replacement_sha256 = hashlib.sha256(pbs.read_bytes()).hexdigest()
                real_copy(source, destination, **kwargs)

            with patch(
                "structured_extractor.atomic_copy_file",
                side_effect=replace_before_copy,
            ):
                with self.assertRaisesRegex(ExtractionIntegrityError, "chang|empreinte"):
                    extract_structured_verified(root)

            self.assertTrue(changed, "Le remplacement synthétique doit réellement avoir lieu.")
            self.assertEqual(original_sha256, expected_sha256)
            self.assertNotEqual(original_sha256, replacement_sha256)

    def test_new_source_appearing_during_parsing_invalidates_whole_extraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_appeared_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            real_extract_pbs = structured_extractor.extract_pbs
            added = False

            def extract_then_add_source(path: Path, relative: str, **kwargs):
                nonlocal added
                rows = real_extract_pbs(path, relative, **kwargs)
                if not added:
                    added = True
                    (root / "PBS" / "items.txt").write_text(
                        "[ITEM]\nName = Appeared during extraction\n",
                        encoding="utf-8",
                    )
                return rows

            with patch(
                "structured_extractor.extract_pbs",
                side_effect=extract_then_add_source,
            ):
                with self.assertRaisesRegex(ExtractionIntegrityError, "modifi"):
                    extract_structured_verified(root)

    def test_source_disappearing_during_parsing_invalidates_whole_extraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_disappeared_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            original_pbs = root / "PBS" / "pokemon.txt"
            real_extract_pbs = structured_extractor.extract_pbs
            removed = False

            def extract_then_remove_source(path: Path, relative: str, **kwargs):
                nonlocal removed
                rows = real_extract_pbs(path, relative, **kwargs)
                if not removed:
                    removed = True
                    original_pbs.unlink()
                return rows

            with patch(
                "structured_extractor.extract_pbs",
                side_effect=extract_then_remove_source,
            ):
                with self.assertRaisesRegex(ExtractionIntegrityError, "modifi"):
                    extract_structured_verified(root)

    def test_invalid_supported_source_never_returns_partial_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_partial_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            (root / "Data" / "Map001.rxdata").write_bytes(b"invalid marshal source")

            with self.assertRaisesRegex(ExtractionIntegrityError, "Map001.rxdata"):
                extract_structured_verified(root)

    def test_unknown_common_event_structure_never_returns_partial_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_essentials_common_unknown_") as temp_dir:
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            (root / "Data" / "CommonEvents.rxdata").write_bytes(
                dumps([None, RubyObject("Synthetic::UnknownCommonEvent", {})])
            )

            with self.assertRaisesRegex(ExtractionIntegrityError, "CommonEvents.rxdata"):
                extract_structured_verified(root)


if __name__ == "__main__":
    unittest.main()
