from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zlib
from pathlib import Path

from adapters import (
    AdapterOperationBlocked,
    ESSENTIALS_LEGACY_PROFILE,
    ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE,
    ESSENTIALS_V21_1_READONLY_PROFILE,
    GameCapability,
    PokemonEssentialsAdapter,
    authorize_adapter_operation,
    create_default_registry,
)
from extraction_project import EXTRACTION_MANIFEST_NAME, build_extraction_manifest_bytes
from project_identity import (
    PROJECT_METADATA_NAME,
    ProjectIdentityError,
    build_project_identity_bytes,
    read_project_identity,
)
from reconstruction_engine import PlanItem, _apply_pbs_items
from ruby_marshal_reader import RubyObject, RubyString
from ruby_marshal_writer import dumps
from structured_extractor import extract_message_bank, extract_pbs


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def compressed_script(value: str) -> RubyString:
    return RubyString(zlib.compress(value.encode("utf-8")))


def event_command(code: int, parameters: list) -> RubyObject:
    return RubyObject("RPG::EventCommand", {"@code": code, "@parameters": parameters})


def prepare_v21_game(
    root: Path,
    *,
    script_version: str = "21.1",
    ini_version: str | None = None,
    mkxp_version: str | None = None,
    empty_plugin_bank: bool = False,
    dangerous_marker: Path | None = None,
) -> None:
    ini_version = ini_version or script_version
    mkxp_version = mkxp_version or script_version
    data = root / "Data"
    data.mkdir(parents=True)
    (root / "Graphics" / "Pokemon").mkdir(parents=True)
    (root / "Game.exe").write_bytes(b"synthetic executable")
    (root / "Game.ini").write_text(
        "[Game]\r\n"
        f"Title=Pokemon Essentials v{ini_version}\r\n"
        "Scripts=Data\\Scripts.rxdata\r\n"
        "Library=RGSS102E.dll\r\n",
        encoding="utf-8",
        newline="",
    )
    (root / "mkxp.json").write_text(
        json.dumps({"windowTitle": f"Pokemon Essentials v{mkxp_version}"}),
        encoding="utf-8",
    )
    (data / "System.rxdata").write_bytes(b"synthetic system marker")
    (data / "Map001.rxdata").write_bytes(dumps(RubyObject("RPG::Map", {"@events": {}})))
    (data / "MapInfos.rxdata").write_bytes(dumps({}))
    (data / "messages_game.dat").write_bytes(
        dumps({ruby_text("Synthetic bank text"): ruby_text("Synthetic bank text")})
    )
    (data / "messages_core.dat").write_bytes(dumps({}))
    dangerous = (
        f"File.write({str(dangerous_marker)!r}, 'executed')\n"
        if dangerous_marker is not None
        else ""
    )
    scripts = [
        [0, ruby_text("Settings"), compressed_script(
            "module Essentials\n"
            f"  VERSION = \"{script_version}\"\n"
            "end\n"
        )],
        [1, ruby_text("GameData"), compressed_script("module GameData\nend\n")],
        [2, ruby_text("PluginManager"), compressed_script("module PluginManager\nend\n")],
        [3, ruby_text("MessageTypes"), compressed_script("module MessageTypes\nend\n")],
        [4, ruby_text("Never execute"), compressed_script(dangerous)],
    ]
    (data / "Scripts.rxdata").write_bytes(dumps(scripts))
    if empty_plugin_bank:
        (data / "PluginScripts.rxdata").write_bytes(dumps([]))
    pbs = root / "PBS" / "pokemon.txt"
    pbs.parent.mkdir()
    pbs.write_text("[TEST]\nName = Syntheticmon\n", encoding="utf-8")


class EssentialsV21ProfileTests(unittest.TestCase):
    def test_compressed_v21_constant_confirms_readonly_game_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_profile_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root)

            result = PokemonEssentialsAdapter().probe(root)
            with self.assertRaises(AdapterOperationBlocked):
                authorize_adapter_operation(
                    root,
                    expected_adapter_id="pokemon_essentials",
                    capability=GameCapability.RECONSTRUCT,
                    require_write_authorization=True,
                )

        self.assertTrue(result.adapter_recognized)
        self.assertEqual("pokemon_essentials", result.engine_family)
        self.assertEqual("21.1", result.declared_version)
        self.assertEqual(ESSENTIALS_V21_1_READONLY_PROFILE, result.structural_profile)
        self.assertIn("Scripts.rxdata", result.version_detection_method)
        self.assertIn("Game.ini", result.version_detection_method)
        self.assertIn("mkxp.json", result.version_detection_method)
        self.assertTrue(result.analysis_compatible)
        self.assertTrue(result.extraction_compatible)
        self.assertTrue(result.translation_compatible)
        self.assertFalse(result.game_write_compatible)
        self.assertFalse(result.reconstruction_validated)
        self.assertTrue(result.can(GameCapability.EXTRACT))
        self.assertTrue(result.can(GameCapability.TRANSLATE))
        self.assertFalse(result.can(GameCapability.RECONSTRUCT))
        self.assertFalse(result.write_actions_allowed)

    def test_static_scripts_inspection_never_executes_ruby(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_no_exec_") as temporary:
            base = Path(temporary)
            marker = base / "ruby-side-effect.txt"
            root = base / "game"
            prepare_v21_game(root, dangerous_marker=marker)

            result = PokemonEssentialsAdapter().probe(root)

            self.assertEqual(ESSENTIALS_V21_1_READONLY_PROFILE, result.structural_profile)
            self.assertFalse(marker.exists())

    def test_contradictory_markers_force_modified_unknown_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_conflict_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, script_version="21.1", ini_version="20", mkxp_version="21.1")

            result = PokemonEssentialsAdapter().probe(root)

        self.assertTrue(result.adapter_recognized)
        self.assertEqual(ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE, result.structural_profile)
        self.assertIn("20", result.declared_version)
        self.assertIn("21.1", result.declared_version)
        self.assertEqual(
            frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            result.capabilities,
        )
        self.assertTrue(any("contredis" in warning.casefold() for warning in result.warnings))

    def test_empty_plugin_scripts_is_not_plugin_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_empty_plugins_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, empty_plugin_bank=True)

            result = PokemonEssentialsAdapter().probe(root)

        evidence_ids = {evidence.evidence_id for evidence in result.evidence}
        self.assertIn("plugin_scripts_empty", evidence_ids)
        self.assertNotIn("plugin_scripts", evidence_ids)
        empty_evidence = next(
            evidence for evidence in result.evidence if evidence.evidence_id == "plugin_scripts_empty"
        )
        self.assertEqual(0, empty_evidence.weight)

    def test_fake_rmxp_project_with_copied_pbs_stays_unknown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_fake_essentials_") as temporary:
            root = Path(temporary) / "game"
            (root / "Data").mkdir(parents=True)
            (root / "Graphics" / "Pokemon").mkdir(parents=True)
            (root / "PBS").mkdir()
            (root / "Game.exe").write_bytes(b"synthetic executable")
            (root / "Game.ini").write_text("[Game]\nLibrary=RGSS102E.dll\n", encoding="utf-8")
            (root / "Data" / "System.rxdata").write_bytes(b"synthetic system")
            (root / "Data" / "Map001.rxdata").write_bytes(b"synthetic map")
            (root / "Data" / "PluginScripts.rxdata").write_bytes(dumps([]))
            (root / "PBS" / "pokemon.txt").write_text("[TEST]\nName = Copied\n", encoding="utf-8")
            (root / "PBS" / "moves.txt").write_text("[MOVE]\nName = Copied\n", encoding="utf-8")

            result = create_default_registry().detect(root)

        self.assertEqual("unknown", result.adapter_id)
        self.assertEqual(ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE, result.structural_profile)
        self.assertFalse(result.extraction_compatible)
        self.assertFalse(result.can(GameCapability.EXTRACT))

    def test_v20_and_future_versions_are_readonly_modified_profiles(self) -> None:
        for version in ("20", "22.0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory(
                prefix="pft_test_essentials_other_version_"
            ) as temporary:
                root = Path(temporary) / "game"
                prepare_v21_game(root, script_version=version)

                result = PokemonEssentialsAdapter().probe(root)

                self.assertEqual(version, result.declared_version)
                self.assertEqual(
                    ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE,
                    result.structural_profile,
                )
                self.assertTrue(result.analysis_compatible)
                self.assertFalse(result.extraction_compatible)
                self.assertFalse(result.translation_compatible)
                self.assertFalse(result.can(GameCapability.RECONSTRUCT))

    def test_legacy_profile_is_distinct_from_declared_modern_versions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_legacy_profile_") as temporary:
            root = Path(temporary) / "game"
            (root / "Data").mkdir(parents=True)
            (root / "PBS").mkdir()
            (root / "Graphics" / "Pokemon").mkdir(parents=True)
            (root / "Game.exe").write_bytes(b"synthetic executable")
            (root / "Game.ini").write_text("[Game]\nLibrary=RGSS102E.dll\n", encoding="utf-8")
            (root / "Data" / "System.rxdata").write_bytes(b"synthetic system")
            (root / "Data" / "Map001.rxdata").write_bytes(b"synthetic map")
            (root / "Data" / "messages_game.dat").write_bytes(b"synthetic bank")
            (root / "PBS" / "pokemon.txt").write_text("[TEST]\nName=Legacy\n", encoding="utf-8")

            result = PokemonEssentialsAdapter().probe(root)

        self.assertEqual(ESSENTIALS_LEGACY_PROFILE, result.structural_profile)
        self.assertEqual("", result.declared_version)
        self.assertTrue(result.game_write_compatible)
        self.assertTrue(result.reconstruction_validated)

    def test_modern_pbs_schemas_preserve_source_format_and_subfields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_pbs_") as temporary:
            root = Path(temporary)
            facilities = root / "battle_facilities.txt"
            facilities_payload = (
                b"\xef\xbb\xbf# synthetic comment\r\n[FACILITY]\r\n"
                b"BeginSpeech = Welcome, \\PN!\r\n"
                b"EndSpeechWin = You kept \\c[2]the exact code.\r\n"
                b"EndSpeechLose = Try again.\r\n"
            )
            facilities.write_bytes(facilities_payload)
            phone = root / "phone.txt"
            phone.write_bytes(
                b"[CONTACT]\r\nIntroMorning = Good morning.\r\n"
                b"Body1 = First body.\r\nBattleRequest = Battle me.\r\n"
                b"MegaMessage = Mega ready.\r\n"
            )
            town_map = root / "town_map.txt"
            town_map.write_bytes(
                b"[REGION]\r\nPoint = 4,7,Synthetic Town,A safe synthetic description,1,2\r\n"
            )

            facility_rows = extract_pbs(facilities, "PBS/battle_facilities.txt")
            phone_rows = extract_pbs(phone, "PBS/phone.txt")
            point_rows = extract_pbs(town_map, "PBS/town_map.txt")

            self.assertEqual(facilities_payload, facilities.read_bytes())
            self.assertEqual(
                {"BeginSpeech", "EndSpeechWin", "EndSpeechLose"},
                {row["commande"] for row in facility_rows},
            )
            self.assertEqual(
                {"IntroMorning", "Body1", "BattleRequest", "MegaMessage"},
                {row["commande"] for row in phone_rows},
            )
            self.assertEqual(
                {"Synthetic Town", "A safe synthetic description"},
                {row["texte_source"] for row in point_rows},
            )
            self.assertTrue(all(row["type"].startswith("PBS v21.1 — Point.") for row in point_rows))
            self.assertTrue(all(row["pbs_newline"] == "CRLF" for row in facility_rows))
            self.assertTrue(all(row["pbs_bom"] == "utf-8" for row in facility_rows))
            win = next(row for row in facility_rows if row["commande"] == "EndSpeechWin")
            self.assertEqual(r"\c[2]", win["codes_proteges"])

    def test_nested_v21_message_banks_keep_distinct_locations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_bank_") as temporary:
            path = Path(temporary) / "messages_game.dat"
            source = ruby_text("Same synthetic source")
            nested = [
                {source: ruby_text("Same synthetic source")},
                {ruby_text("Same synthetic source"): ruby_text("Traduction synthétique")},
            ]
            path.write_bytes(dumps(nested))

            rows = extract_message_bank(path, "Data/messages_game.dat")

        self.assertEqual(2, len(rows))
        self.assertEqual(2, len({row["id_stable"] for row in rows}))
        self.assertEqual(2, len({row["evenement_nom"] for row in rows}))
        translated = next(row for row in rows if row["traduction_fr"])
        self.assertEqual("Traduction synthétique", translated["traduction_fr"])

    def test_supported_modern_pbs_field_rewrite_preserves_bom_crlf_comments_and_order(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_pbs_format_") as temporary:
            path = Path(temporary) / "battle_facilities.txt"
            original = (
                b"\xef\xbb\xbf# first comment\r\n[FACILITY]\r\n"
                b"BeginSpeech = Welcome, \\PN!\r\n"
                b"# second comment\r\nEndSpeechWin = Original ending.\r\n"
            )
            path.write_bytes(original)
            row = next(
                row
                for row in extract_pbs(path, "PBS/battle_facilities.txt")
                if row["commande"] == "EndSpeechWin"
            )
            item = PlanItem(
                id_stable=row["id_stable"],
                type=row["type"],
                fichier=row["fichier"],
                source=row["texte_source"],
                translation="Translated ending.",
                status="Accepté",
            )

            _apply_pbs_items(path, "PBS/battle_facilities.txt", [item])

            self.assertEqual(
                original.replace(b"Original ending.", b"Translated ending."),
                path.read_bytes(),
            )

    def test_v21_extraction_provenance_is_bound_to_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_provenance_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            project.mkdir()
            prepare_v21_game(root)

            adapter = PokemonEssentialsAdapter()
            detection = adapter.probe(root)
            result = adapter.extract_with_provenance(root)
            analysis = adapter.analyze(root, detection)
            self.assertEqual(ESSENTIALS_V21_1_READONLY_PROFILE, result.essentials_profile)
            self.assertEqual(len(result.rows), analysis.extractable_text_occurrences)
            self.assertEqual(
                {"message_banks": 1, "pbs": 1},
                analysis.extractable_by_source,
            )
            self.assertEqual(
                {ESSENTIALS_V21_1_READONLY_PROFILE},
                {row["profil_essentials"] for row in result.rows},
            )
            csv_payload = b"synthetic private-free csv fixture"
            csv_sha256 = hashlib.sha256(csv_payload).hexdigest()
            manifest_raw = build_extraction_manifest_bytes(
                result,
                game_root=root,
                adapter_version="21.1",
                csv_sha256=csv_sha256,
                report_sha256="a" * 64,
                row_count=len(result.rows),
            )
            manifest = json.loads(manifest_raw.decode("utf-8"))
            self.assertEqual(
                ESSENTIALS_V21_1_READONLY_PROFILE,
                manifest["essentials_profile"],
            )
            (project / EXTRACTION_MANIFEST_NAME).write_bytes(manifest_raw)
            csv_path = project / "textes_structures.csv"
            csv_path.write_bytes(csv_payload)
            (project / PROJECT_METADATA_NAME).write_bytes(
                build_project_identity_bytes(
                    root,
                    adapter_id="pokemon_essentials",
                    adapter_version="21.1",
                    adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                    source_manifest_sha256=result.source_manifest_sha256,
                    extraction_manifest_name=EXTRACTION_MANIFEST_NAME,
                    extraction_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                    extraction_id=manifest["extraction_id"],
                    extracted_csv_sha256=csv_sha256,
                )
            )

            identity = read_project_identity(
                csv_path,
                root,
                expected_adapter_id="pokemon_essentials",
                require_extraction_provenance=True,
            )
            self.assertEqual(ESSENTIALS_V21_1_READONLY_PROFILE, identity.adapter_profile)

            identity_payload = json.loads((project / PROJECT_METADATA_NAME).read_text(encoding="utf-8"))
            identity_payload["adapter_profile"] = ESSENTIALS_LEGACY_PROFILE
            (project / PROJECT_METADATA_NAME).write_text(
                json.dumps(identity_payload),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ProjectIdentityError, "manifeste.*incohérent"):
                read_project_identity(
                    csv_path,
                    root,
                    expected_adapter_id="pokemon_essentials",
                    require_extraction_provenance=True,
                )


if __name__ == "__main__":
    unittest.main()
