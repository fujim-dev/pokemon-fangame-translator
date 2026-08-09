from __future__ import annotations

import hashlib
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from analysis import write_analysis_reports
from adapters import (
    AdapterOperationBlocked,
    AdapterRegistry,
    GameCapability,
    PokemonEssentialsAdapter,
    PokemonFluxAdapter,
)
from adapters.pokemon_flux import SUPPORTED_RELEASES
from flux_archive import FluxArchiveEntry, FluxArchiveInventory, parse_7zip_slt
from ruby_marshal_reader import RubyObject, RubyString
from ruby_marshal_writer import dumps


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def event_command(code: int, parameters: list) -> RubyObject:
    return RubyObject("RPG::EventCommand", {"@code": code, "@parameters": parameters})


def synthetic_inventory() -> FluxArchiveInventory:
    members = {
        "Data/messages_game.dat",
        "Data/messages.dat",
        "Data/CommonEvents.rxdata",
        "Data/MapInfos.rxdata",
        "Data/System.rxdata",
        "Data/Script_index",
        "Data/Script_001.rb",
        "Data/Map001.rxdata",
    }
    return FluxArchiveInventory(
        archive_type="7z",
        physical_size=1024,
        entries=tuple(
            FluxArchiveEntry(path, 10, 10, "A", False, "Copy")
            for path in sorted(members)
        ),
    )


class FakeArchiveReader:
    def __init__(
        self,
        *,
        extractor=None,
        messages_hash: str | None = None,
        graphics_inventory: FluxArchiveInventory | None = None,
    ):
        self.inventory = synthetic_inventory()
        self.extractor = extractor
        self.messages_hash = messages_hash or SUPPORTED_RELEASES[0].messages_game_sha256
        self.graphics_inventory = graphics_inventory
        self.inspected: list[Path] = []

    def __getstate__(self):
        # La fonction d'extraction appartient au test d'analyse/extraction, pas
        # à probe(). Le worker spawn reçoit uniquement l'état de détection.
        state = dict(self.__dict__)
        state["extractor"] = None
        state["inspected"] = []
        return state

    def inspect(self, archive_path: Path) -> FluxArchiveInventory:
        self.inspected.append(archive_path)
        if archive_path.name == "Assets_0.fpk" and self.graphics_inventory is not None:
            return self.graphics_inventory
        return self.inventory

    def member_sha256(self, archive_path: Path, member_path: str, inventory=None) -> str:
        del archive_path, inventory
        if member_path != "Data/messages_game.dat":
            raise AssertionError(member_path)
        return self.messages_hash

    def extract_to(self, archive_path: Path, target_root: Path, inventory=None) -> None:
        del archive_path, inventory
        if not self.extractor:
            raise AssertionError("Extraction inattendue")
        self.extractor(target_root)


def make_flux_root(base: Path) -> Path:
    root = base / "Synthetic Flux"
    (root / "Data").mkdir(parents=True)
    (root / "Graphics").mkdir()
    (root / "Flux.exe").write_bytes(b"synthetic executable")
    (root / "Data" / "Data_0.fpk").write_bytes(b"synthetic 7z fixture marker")
    (root / "Graphics" / "Assets_0.fpk").write_bytes(b"synthetic assets")
    (root / "Flux.ini").write_text(
        "[Game]\nLibrary=RGSS104E.dll\nScripts=Data\\Scripts.rxdata\nTitle=Pokemon Flux\n",
        encoding="utf-8",
    )
    (root / "mkxp.json").write_text('{"execName":"Flux"}', encoding="utf-8")
    return root


def known_hasher(path: Path) -> str:
    release = SUPPORTED_RELEASES[0]
    if path.name == "Flux.exe":
        return release.executable_sha256
    if path.name == "Data_0.fpk":
        return release.fpk_sha256
    raise AssertionError(path)


def unknown_hasher(_path: Path) -> str:
    return "0" * 64


class FluxAdapterTests(unittest.TestCase):
    def test_known_flux_release_is_detected_but_all_write_paths_stay_locked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_detect_") as temp_dir:
            root = make_flux_root(Path(temp_dir))
            reader = FakeArchiveReader()
            adapter = PokemonFluxAdapter(reader, file_hasher=known_hasher)
            registry = AdapterRegistry((PokemonEssentialsAdapter(), adapter))

            result = registry.detect(root)

            self.assertEqual("pokemon_flux", result.adapter_id)
            self.assertEqual("2.1.0", result.recognized_version)
            self.assertEqual(100, result.confidence)
            self.assertTrue(result.adapter_recognized)
            self.assertFalse(result.write_actions_allowed)
            self.assertEqual(
                frozenset(
                    {
                        GameCapability.ANALYZE,
                        GameCapability.DEEP_ANALYZE,
                        GameCapability.EXTRACT,
                        GameCapability.TRANSLATE,
                        GameCapability.VALIDATE_IMPORT,
                    }
                ),
                result.capabilities,
            )
            self.assertIs(adapter, registry.adapter_for(result))
            self.assertFalse(result.can(GameCapability.RECONSTRUCT))

    def test_unknown_flux_release_keeps_specialized_read_only_adapter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_unknown_") as temp_dir:
            root = make_flux_root(Path(temp_dir))
            adapter = PokemonFluxAdapter(FakeArchiveReader(), file_hasher=unknown_hasher)
            registry = AdapterRegistry((PokemonEssentialsAdapter(), adapter))

            result = registry.detect(root)

            self.assertEqual("pokemon_flux", result.adapter_id)
            self.assertEqual("inconnue", result.recognized_version)
            self.assertTrue(result.adapter_recognized)
            self.assertFalse(result.write_actions_allowed)
            self.assertLess(result.confidence, 100)
            self.assertTrue(any("non homologuée" in warning for warning in result.warnings))
            with self.assertRaises(AdapterOperationBlocked):
                adapter.extract(root)

    def test_flux_name_without_structure_is_never_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_name_") as temp_dir:
            root = Path(temp_dir) / "Pokemon Flux Episode 99"
            root.mkdir()
            adapter = PokemonFluxAdapter(FakeArchiveReader(), file_hasher=known_hasher)

            result = AdapterRegistry((PokemonEssentialsAdapter(), adapter)).detect(root)

            self.assertEqual("unknown", result.adapter_id)
            self.assertFalse(result.adapter_recognized)

    def test_static_flux_analysis_never_executes_ruby(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_analysis_") as temp_dir:
            base = Path(temp_dir)
            root = make_flux_root(base)
            side_effect = base / "ruby-was-executed.txt"

            def extract_fixture(target: Path) -> None:
                data = target / "Data"
                data.mkdir()
                page = RubyObject(
                    "RPG::Event::Page",
                    {
                        "@list": [
                            event_command(101, [ruby_text("Bonjour, bienvenue dans ce village avec nous.")]),
                            event_command(355, [ruby_text(f"File.write('{side_effect}', 'bad')")]),
                        ]
                    },
                )
                event = RubyObject("RPG::Event", {"@pages": [page]})
                game_map = RubyObject("RPG::Map", {"@events": {1: event}})
                (data / "Map001.rxdata").write_bytes(dumps(game_map))
                common = RubyObject(
                    "RPG::CommonEvent",
                    {"@list": [event_command(101, [ruby_text("Merci de votre visite aujourd'hui.")])]},
                )
                (data / "CommonEvents.rxdata").write_bytes(dumps([None, common]))
                (data / "messages_game.dat").write_bytes(
                    dumps([[{ruby_text("Hello from Flux."): ruby_text("Hello from Flux.")}]]),
                )
                (data / "messages.dat").write_bytes(dumps([ruby_text("Another visible message.")]))
                (data / "MapInfos.rxdata").write_bytes(dumps({}))
                (data / "System.rxdata").write_bytes(dumps(RubyObject("RPG::System", {})))
                (data / "Script_index").write_text("Script_001.rb\n", encoding="utf-8")
                (data / "Script_001.rb").write_text(
                    f"File.write('{side_effect}', 'executed')",
                    encoding="utf-8",
                )

            reader = FakeArchiveReader(extractor=extract_fixture)
            adapter = PokemonFluxAdapter(reader, file_hasher=known_hasher)
            detection = AdapterRegistry((PokemonEssentialsAdapter(), adapter)).detect(root)

            report = adapter.analyze(root, detection)
            report_paths = write_analysis_reports(report, base / "reports", original_root=root)

            self.assertFalse(side_effect.exists())
            self.assertEqual("pokemon_flux", report.adapter_id)
            self.assertEqual(1, report.map_files_found)
            self.assertEqual(1, report.maps_analyzed)
            self.assertEqual(1, report.map_events)
            self.assertEqual(1, report.map_pages)
            self.assertEqual(1, report.common_events_found)
            self.assertEqual(1, report.common_events_analyzed)
            self.assertEqual(2, report.message_banks_analyzed)
            self.assertEqual(1, report.ruby_script_files)
            self.assertEqual(1, report.dynamic_script_commands)
            self.assertEqual(3, report.extractable_text_occurrences)
            self.assertEqual(
                {"common_events": 1, "map_events": 1, "messages_game": 1},
                report.extractable_by_source,
            )
            self.assertTrue(report.coverage.incomplete_sources)
            self.assertFalse(any(issue.blocking for issue in report.issues))
            public_report = report_paths["text"].read_text(encoding="utf-8")
            self.assertNotIn(str(root), public_report)
            self.assertNotIn("Hello from Flux", public_report)
            self.assertNotIn("Merci de votre visite", public_report)

    def test_extraction_is_deterministic_occurrence_precise_and_keeps_fpk_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_extract_") as temp_dir:
            base = Path(temp_dir)
            root = make_flux_root(base)
            original_fpk = (root / "Data" / "Data_0.fpk").read_bytes()

            def extract_fixture(target: Path) -> None:
                data = target / "Data"
                data.mkdir()
                canonical = "\\pnHello from the canonical Flux bank."
                (data / "messages_game.dat").write_bytes(
                    dumps(
                        [
                            [{ruby_text(canonical): ruby_text(canonical)}],
                            [{ruby_text(canonical): ruby_text(canonical)}],
                        ]
                    )
                )
                (data / "messages.dat").write_bytes(
                    dumps(
                        [
                            ruby_text(canonical),
                            ruby_text(canonical),
                            ruby_text("Non canonical technical text."),
                        ]
                    )
                )
                page = RubyObject(
                    "RPG::Event::Page",
                    {
                        "@list": [
                            event_command(101, [ruby_text("First dialogue line.")]),
                            event_command(401, [ruby_text("Second dialogue line!")]),
                            event_command(102, [[ruby_text("A visible choice")]]),
                        ]
                    },
                )
                event = RubyObject(
                    "RPG::Event",
                    {"@name": ruby_text("Synthetic event"), "@pages": [page]},
                )
                (data / "Map001.rxdata").write_bytes(
                    dumps(RubyObject("RPG::Map", {"@events": {1: event}}))
                )
                common = RubyObject(
                    "RPG::CommonEvent",
                    {"@list": [event_command(101, [ruby_text("Common event dialogue.")])]},
                )
                (data / "CommonEvents.rxdata").write_bytes(dumps([None, common]))
                (data / "MapInfos.rxdata").write_bytes(dumps({}))
                (data / "System.rxdata").write_bytes(dumps(RubyObject("RPG::System", {})))
                (data / "Other.dat").write_bytes(dumps([ruby_text(canonical)]))
                (data / "unsupported.dat").write_bytes(b"not a marshal payload")
                (data / "Script_index").write_text("Script_001.rb\n", encoding="utf-8")
                (data / "Script_001.rb").write_text("raise 'never executed'", encoding="utf-8")

            reader = FakeArchiveReader(extractor=extract_fixture)
            adapter = PokemonFluxAdapter(reader, file_hasher=known_hasher)

            first_rows, first_errors = adapter.extract(root)
            second_rows, second_errors = adapter.extract(root)

            self.assertEqual(original_fpk, (root / "Data" / "Data_0.fpk").read_bytes())
            self.assertEqual(first_rows, second_rows)
            self.assertEqual(first_errors, second_errors)
            self.assertEqual(len(first_rows), len({row["id_stable"] for row in first_rows}))
            self.assertTrue(all(len(row["id_stable"]) == 64 for row in first_rows))
            by_source: dict[str, int] = {}
            for row in first_rows:
                by_source[row["source_flux"]] = by_source.get(row["source_flux"], 0) + 1
                self.assertEqual(
                    row["empreinte_texte_csv"],
                    hashlib.sha256(row["texte_source"].encode("utf-8")).hexdigest(),
                )
            self.assertEqual(
                {
                    "common_events": 1,
                    "map_events": 2,
                    "messages": 2,
                    "messages_game": 2,
                    "other_data": 1,
                },
                by_source,
            )
            canonical_rows = [row for row in first_rows if row["source_flux"] == "messages_game"]
            self.assertEqual(2, len(canonical_rows))
            self.assertEqual(1, len({row["texte_source"] for row in canonical_rows}))
            self.assertEqual(2, len({row["chemin_structurel"] for row in canonical_rows}))
            self.assertTrue(all("\\pn" in row["codes_proteges"] for row in canonical_rows))
            self.assertEqual(1, len(first_errors))
            self.assertIn("unsupported.dat", first_errors[0])

    def test_extraction_canonicalizes_a_windows_temporary_path_alias_before_loading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_temp_alias_") as temp_dir:
            base = Path(temp_dir)
            root = make_flux_root(base)
            alias_parent = base / "temporary_alias"
            extracted = base / "temporary_extracted"
            alias_parent.mkdir()
            extracted.mkdir()
            aliased_path = alias_parent / ".." / extracted.name

            def extract_fixture(target: Path) -> None:
                self.assertEqual(extracted.resolve(), target)
                data = target / "Data"
                data.mkdir()
                canonical = "Canonical message through a temporary alias."
                (data / "messages_game.dat").write_bytes(
                    dumps([[{ruby_text(canonical): ruby_text(canonical)}]])
                )
                (data / "messages.dat").write_bytes(dumps([ruby_text(canonical)]))
                (data / "CommonEvents.rxdata").write_bytes(dumps([None]))

            @contextmanager
            def aliased_temporary_directory(*_args, **_kwargs):
                yield str(aliased_path)

            adapter = PokemonFluxAdapter(
                FakeArchiveReader(extractor=extract_fixture),
                file_hasher=known_hasher,
            )
            with patch(
                "flux_extractor.tempfile.TemporaryDirectory",
                side_effect=aliased_temporary_directory,
            ):
                rows, errors = adapter.extract(root)

            self.assertEqual([], errors)
            self.assertEqual(2, len(rows))
            self.assertEqual(
                {"messages", "messages_game"},
                {row["source_flux"] for row in rows},
            )

    def test_analysis_correlates_known_audio_and_graphics_references(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_resources_") as temp_dir:
            base = Path(temp_dir)
            root = make_flux_root(base)
            for relative in (
                "Audio/BGM/theme.ogg",
                "Audio/BGM/script_theme.ogg",
                "Audio/SE/click.wav",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            graphics_inventory = FluxArchiveInventory(
                archive_type="7z",
                physical_size=100,
                entries=tuple(
                    FluxArchiveEntry(path, 10, 10, "A", False, "Copy")
                    for path in (
                        "Graphics/Characters/Hero.png",
                        "Graphics/Pictures/Portrait.png",
                        "Graphics/Pictures/ScriptPortrait.png",
                    )
                ),
            )

            def extract_fixture(target: Path) -> None:
                data = target / "Data"
                data.mkdir()
                canonical = "Canonical message."
                (data / "messages_game.dat").write_bytes(
                    dumps([[{ruby_text(canonical): ruby_text(canonical)}]])
                )
                (data / "messages.dat").write_bytes(dumps([ruby_text(canonical)]))
                audio = lambda name: RubyObject(
                    "RPG::AudioFile", {"@name": ruby_text(name), "@volume": 100, "@pitch": 100}
                )
                page = RubyObject(
                    "RPG::Event::Page",
                    {
                        "@graphic": RubyObject(
                            "RPG::Event::Page::Graphic",
                            {"@character_name": ruby_text("Hero")},
                        ),
                        "@list": [
                            event_command(241, [audio("theme")]),
                            event_command(250, [audio("MissingVoice")]),
                            event_command(231, [1, ruby_text("Portrait")]),
                        ],
                    },
                )
                event = RubyObject("RPG::Event", {"@pages": [page]})
                (data / "Map001.rxdata").write_bytes(
                    dumps(RubyObject("RPG::Map", {"@events": {1: event}}))
                )
                (data / "CommonEvents.rxdata").write_bytes(dumps([None]))
                (data / "MapInfos.rxdata").write_bytes(dumps({}))
                (data / "System.rxdata").write_bytes(
                    dumps(RubyObject("RPG::System", {"@cursor_se": audio("click")}))
                )
                (data / "Script_index").write_text("Script_001.rb\n", encoding="utf-8")
                (data / "Script_001.rb").write_text(
                    '\n'.join(
                        (
                            'pbBGMPlay("script_theme")',
                            'pbSEPlay("ScriptMissing")',
                            'pbSEPlay("dynamic_#{id}")',
                            'pbResolveBitmap("Graphics/Pictures/ScriptPortrait")',
                        )
                    ),
                    encoding="utf-8",
                )

            reader = FakeArchiveReader(
                extractor=extract_fixture,
                graphics_inventory=graphics_inventory,
            )
            adapter = PokemonFluxAdapter(reader, file_hasher=known_hasher)
            detection = AdapterRegistry((PokemonEssentialsAdapter(), adapter)).detect(root)

            report = adapter.analyze(root, detection)

            self.assertEqual(5, report.static_references_checked)
            self.assertEqual(1, report.missing_static_references)
            self.assertEqual(3, report.ruby_literal_references_checked)
            self.assertEqual(1, report.ruby_literal_references_missing)
            self.assertEqual(1, report.ruby_dynamic_resource_expressions)
            self.assertEqual(2, report.extractable_text_occurrences)
            self.assertEqual(
                {"messages": 1, "messages_game": 1},
                report.extractable_by_source,
            )
            self.assertTrue(
                any(
                    issue.code == "missing_static_reference"
                    and issue.relative_path == "Audio/SE/MissingVoice"
                    for issue in report.issues
                )
            )


class FluxArchiveListingTests(unittest.TestCase):
    @staticmethod
    def _listing(member: str) -> str:
        return "\n".join(
            [
                "7-Zip synthetic output",
                "Path = fixture.fpk",
                "Type = 7z",
                "Physical Size = 42",
                "----------",
                f"Path = {member}",
                "Size = 10",
                "Packed Size = 10",
                "Attributes = A",
                "Encrypted = -",
                "Method = Copy",
                "",
            ]
        )

    def test_safe_listing_is_accepted(self) -> None:
        inventory = parse_7zip_slt(self._listing(r"Data\messages_game.dat"))
        self.assertTrue(inventory.safe)
        self.assertEqual(frozenset({"Data/messages_game.dat"}), inventory.member_paths)

    def test_blank_solid_archive_packed_size_is_accepted_as_unknown(self) -> None:
        listing = self._listing(r"Data\messages_game.dat").replace(
            "Packed Size = 10",
            "Packed Size = ",
        )

        inventory = parse_7zip_slt(listing)

        self.assertTrue(inventory.safe)
        self.assertEqual(0, inventory.file_entries[0].packed_size)

    def test_parent_traversal_is_rejected(self) -> None:
        inventory = parse_7zip_slt(self._listing(r"..\outside.txt"))
        self.assertFalse(inventory.safe)
        self.assertTrue(any("segment interdit" in issue for issue in inventory.issues))

    def test_case_insensitive_path_collision_is_rejected(self) -> None:
        first = self._listing(r"Data\messages_game.dat").rstrip()
        second_record = "\n".join(
            [
                r"Path = data\MESSAGES_GAME.dat",
                "Size = 10",
                "Packed Size = 10",
                "Attributes = A",
                "Encrypted = -",
                "Method = Copy",
            ]
        )

        inventory = parse_7zip_slt(first + "\n\n" + second_record + "\n")

        self.assertFalse(inventory.safe)
        self.assertTrue(any("Collision" in issue for issue in inventory.issues))


if __name__ == "__main__":
    unittest.main()
