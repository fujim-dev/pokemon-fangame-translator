from __future__ import annotations

import hashlib
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
        {"@code": code, "@parameters": parameters},
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
            root = Path(temp_dir) / "game"
            prepare_essentials_game(root)
            pbs = root / "PBS" / "pokemon.txt"
            original = pbs.read_bytes()
            real_copy = structured_extractor.atomic_copy_file
            changed = False

            def replace_before_copy(source: Path, destination: Path, **kwargs) -> None:
                nonlocal changed
                if Path(source) == pbs and not changed:
                    changed = True
                    pbs.write_bytes(original + b"# changed before copy\n")
                real_copy(source, destination, **kwargs)

            with patch(
                "structured_extractor.atomic_copy_file",
                side_effect=replace_before_copy,
            ):
                with self.assertRaisesRegex(ExtractionIntegrityError, "chang|empreinte"):
                    extract_structured_verified(root)

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


if __name__ == "__main__":
    unittest.main()
