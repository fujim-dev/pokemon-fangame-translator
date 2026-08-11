from __future__ import annotations

import csv
import hashlib
import io
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
from essentials_town_map import (
    COMPILED_POINT_PROOF_FORMAT,
    extract_compiled_point_text,
    graph_sha256,
    load_town_map_bytes,
)
from analysis.integrity import compare_snapshots, snapshot_tree
from project_identity import (
    PROJECT_METADATA_NAME,
    ProjectIdentityError,
    build_project_identity_bytes,
    read_project_identity,
)
from reconstruction_engine import (
    V21_1_BANK_CORPUS_VALIDATION_SCOPE,
    V21_1_MAP_VALIDATION_SCOPE,
    V21_1_POINT_VALIDATION_SCOPE,
    V21_1_VALIDATION_SCOPE,
    PlanItem,
    ReconstructionError,
    _apply_pbs_items,
    build_plan,
    build_v21_1_bank_corpus_validation_plan,
    build_v21_1_map_validation_plan,
    build_v21_1_point_validation_plan,
    build_v21_1_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import RubyObject, RubyString, load
from ruby_marshal_writer import dumps
from structured_extractor import (
    ExtractionIntegrityError,
    PBS_POINT_STRUCTURE_FORMAT,
    extract_map,
    extract_message_bank,
    extract_pbs,
)
from translation_project import TranslationProjectError, TranslationProjectSession
from project_test_support import finalize_verified_essentials_project


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def compressed_script(value: str) -> RubyString:
    return RubyString(zlib.compress(value.encode("utf-8")))


def event_command(code: int, parameters: list, *, indent: int = 0) -> RubyObject:
    return RubyObject(
        "RPG::EventCommand",
        {"@code": code, "@indent": indent, "@parameters": parameters},
    )


def validation_map(
    *,
    ambiguous_choice_branch: bool = False,
    internal_line_control: bool = False,
) -> RubyObject:
    first_choice = ruby_text("First synthetic choice")
    second_choice = ruby_text("Second synthetic choice")
    first_branch_text = ruby_text("First synthetic choice")
    commands = [
        event_command(
            101,
            [
                ruby_text(
                    "Synthetic\\ninternal map dialogue."
                    if internal_line_control
                    else "Synthetic map dialogue."
                )
            ],
        ),
        event_command(401, [ruby_text("Second synthetic line.")]),
        event_command(102, [[first_choice, second_choice], 0]),
        event_command(402, [0, first_branch_text]),
        event_command(101, [ruby_text("First branch body")], indent=1),
        event_command(402, [1, ruby_text("Second synthetic choice")]),
        event_command(101, [ruby_text("Second branch body")], indent=1),
    ]
    if ambiguous_choice_branch:
        commands.append(event_command(402, [0, ruby_text("First synthetic choice")]))
    commands.extend([event_command(404, []), event_command(0, [])])
    page = RubyObject(
        "RPG::Event::Page",
        {"@trigger": 3, "@list": commands},
    )
    event = RubyObject(
        "RPG::Event",
        {
            "@id": 1,
            "@name": ruby_text("Synthetic intro event"),
            "@x": 9,
            "@y": 7,
            "@pages": [page],
        },
    )
    return RubyObject("RPG::Map", {"@events": {1: event}})


def validation_common_events() -> list:
    first_choice = ruby_text("First synthetic common choice")
    second_choice = ruby_text("Second synthetic common choice")
    first_event = RubyObject(
        "RPG::CommonEvent",
        {
            "@id": 1,
            "@name": ruby_text("Synthetic common event one"),
            "@trigger": 1,
            "@switch_id": 7,
            "@synthetic_metadata": ruby_text("preserve first event metadata"),
            "@list": [
                event_command(108, [ruby_text("Neighbor before first dialogue")]),
                event_command(101, [ruby_text("Simple common dialogue")]),
                event_command(111, [12, 0]),
                event_command(101, [ruby_text(r"Internal \n common control")], indent=1),
                event_command(401, [ruby_text("Second common line")], indent=1),
                event_command(401, [ruby_text("Third common line")], indent=1),
                event_command(102, [[first_choice, second_choice], 0]),
                event_command(402, [0, ruby_text("First synthetic common choice")]),
                event_command(108, [ruby_text("First branch body")], indent=1),
                event_command(402, [1, ruby_text("Second synthetic common choice")]),
                event_command(108, [ruby_text("Second branch body")], indent=1),
                event_command(404, []),
                event_command(0, []),
            ],
        },
    )
    second_event = RubyObject(
        "RPG::CommonEvent",
        {
            "@id": 2,
            "@name": ruby_text("Synthetic common event two"),
            "@trigger": 2,
            "@switch_id": 9,
            "@synthetic_metadata": ruby_text("preserve second event metadata"),
            "@list": [
                event_command(121, [4, 4, 0]),
                event_command(101, [ruby_text("Second event dialogue")]),
                event_command(201, [0, 3, 4, 5, 2, 0]),
                event_command(0, []),
            ],
        },
    )
    return [None, first_event, second_event]


def prepare_v21_game(
    root: Path,
    *,
    script_version: str = "21.1",
    ini_version: str | None = None,
    mkxp_version: str | None = None,
    empty_plugin_bank: bool = False,
    nested_message_bank: bool = False,
    bank_corpus: bool = False,
    map_validation: bool = False,
    ambiguous_choice_branch: bool = False,
    internal_line_control: bool = False,
    common_event_corpus: bool = False,
    point_validation: bool = False,
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
    game_map = (
        validation_map(
            ambiguous_choice_branch=ambiguous_choice_branch,
            internal_line_control=internal_line_control,
        )
        if map_validation
        else RubyObject("RPG::Map", {"@events": {}})
    )
    (data / "Map001.rxdata").write_bytes(dumps(game_map))
    map_infos = {
        1: RubyObject("RPG::MapInfo", {"@name": ruby_text("Synthetic intro")})
    }
    (data / "MapInfos.rxdata").write_bytes(dumps(map_infos))
    if common_event_corpus:
        (data / "CommonEvents.rxdata").write_bytes(dumps(validation_common_events()))
    core_bank = {}
    if bank_corpus:
        bank = [
            [
                {
                    ruby_text("Nested synthetic game bank text"): ruby_text(
                        "Nested synthetic game bank text"
                    ),
                    ruby_text("Untouched nested game bank text"): ruby_text(
                        "Untouched nested game bank text"
                    ),
                }
            ],
            {
                ruby_text("Direct synthetic game bank text"): ruby_text(
                    "Direct synthetic game bank text"
                ),
                ruby_text("Untouched direct game bank text"): ruby_text(
                    "Untouched direct game bank text"
                ),
            },
        ]
        core_bank = [
            {},
            {
                ruby_text("Direct synthetic core bank text"): ruby_text(
                    "Direct synthetic core bank text"
                ),
                ruby_text("Untouched direct core bank text"): ruby_text(
                    "Untouched direct core bank text"
                ),
            },
        ]
    elif nested_message_bank:
        bank = [
            {
                ruby_text("Synthetic bank text for validation"): ruby_text(
                    "Synthetic bank text for validation"
                ),
            },
            {
                ruby_text("Second untouched synthetic bank text"): RubyString(
                    b"Second untouched synthetic bank text",
                    {"E": True, "synthetic_metadata": ruby_text("preserved")},
                ),
            },
        ]
    else:
        bank = {ruby_text("Synthetic bank text"): ruby_text("Synthetic bank text")}
    (data / "messages_game.dat").write_bytes(dumps(bank))
    (data / "messages_core.dat").write_bytes(dumps(core_bank))
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
    if point_validation:
        (pbs.parent / "town_map.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic point fixture\r\n"
            b"[0]\r\n"
            b"Name = Synthetic Region\r\n"
            b"Filename = synthetic.png\r\n"
            b"Point = 4,7,Synthetic Route\r\n"
            b"# keep this comment exactly here\r\n"
            b"Point = 5,8,Untouched Town,Untouched Description,1,2,3\r\n"
        )
        town_map = {
            0: RubyObject(
                "GameData::TownMap",
                {
                    "@id": 0,
                    "@real_name": ruby_text("Synthetic Region"),
                    "@filename": ruby_text("synthetic.png"),
                    "@point": [
                        [
                            4,
                            7,
                            ruby_text("Synthetic Route"),
                            None,
                            None,
                            None,
                            None,
                            None,
                        ],
                        [
                            5,
                            8,
                            ruby_text("Untouched Town"),
                            ruby_text("Untouched Description"),
                            1,
                            2,
                            3,
                            None,
                        ],
                    ],
                    "@flags": [],
                    "@pbs_file_suffix": ruby_text(""),
                },
            )
        }
        (data / "town_map.dat").write_bytes(dumps(town_map))


def write_extracted_project_csv(path: Path, rows: list[dict]) -> None:
    fields = list(rows[0])
    for field in (
        "niveau_relecture",
        "alertes_relecture",
        "groupe_doublon",
        "origine_traduction",
    ):
        if field not in fields:
            fields.append(field)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, delimiter=";")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    path.parent.mkdir(parents=True)
    path.write_bytes(output.getvalue().encode("utf-8-sig"))


def prepare_point_project(base: Path):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(root, point_validation=True)
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["type"] == "PBS v21.1 — Point.Name"
        and row["fichier"] == "PBS/town_map.txt"
        and row["pbs_field_count"] == 3
    )
    selected["traduction_fr"] = selected["texte_source"] + " [TEST PFT v21.1 POINT]"
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_synthetique_v21_1_point"
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

    def test_point_extraction_records_an_exact_text_free_structure_proof(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_extract_") as temporary:
            root = Path(temporary)
            path = root / "town_map.txt"
            payload = (
                b"\xef\xbb\xbf# synthetic comment\r\n"
                b"[0]\r\n"
                b"Point = 4,7,Synthetic Route\r\n"
            )
            path.write_bytes(payload)

            row = next(
                row
                for row in extract_pbs(path, "PBS/town_map.txt")
                if row["commande"] == "Point"
            )
            proof = json.loads(row["pbs_point_structure"])

        self.assertEqual(PBS_POINT_STRUCTURE_FORMAT, proof["format"])
        self.assertEqual(3, proof["line_number"])
        self.assertEqual(3, proof["field_count"])
        self.assertEqual(2, proof["field_index"])
        self.assertEqual([1, 3], proof["separator_offsets"])
        self.assertEqual("CRLF", proof["newline"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), proof["file_sha256"])
        self.assertNotIn("Synthetic Route", row["pbs_point_structure"])
        self.assertEqual(3, row["pbs_line_number"])
        self.assertEqual(3, row["pbs_field_count"])
        self.assertEqual("utf-8-sig", row["pbs_encoding"])
        self.assertEqual("utf-8", row["pbs_bom"])
        self.assertEqual("CRLF", row["pbs_newline"])

    def test_point_extraction_binds_every_row_to_the_compiled_town_map(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_link_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, point_validation=True)
            extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
            point_rows = [
                row
                for row in extraction.rows
                if row["type"].startswith("PBS v21.1 — Point.")
            ]

        self.assertEqual(3, len(point_rows))
        self.assertTrue(
            any(source.relative_path == "Data/town_map.dat" for source in extraction.sources)
        )
        for row in point_rows:
            proof = json.loads(row["pbs_compiled_structure"])
            self.assertEqual(COMPILED_POINT_PROOF_FORMAT, proof["format"])
            self.assertEqual("Data/town_map.dat", row["pbs_compiled_file"])
            self.assertEqual(proof["file_sha256"], row["pbs_compiled_sha256"])
            self.assertEqual(proof["compiled_path"], json.loads(row["pbs_compiled_path"]))
            self.assertNotIn(row["texte_source"], row["pbs_compiled_structure"])

    def test_point_extraction_refuses_a_pbs_compiled_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_mismatch_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, point_validation=True)
            compiled_path = root / "Data" / "town_map.dat"
            compiled = load(compiled_path)
            compiled[0].ivars["@point"][0][0] = 9
            compiled_path.write_bytes(dumps(compiled))

            with self.assertRaisesRegex(
                ExtractionIntegrityError,
                "correspondance|ambiguë",
            ):
                PokemonEssentialsAdapter().extract_with_provenance(root)

    def test_point_extraction_refuses_global_town_map_section_mismatches(self) -> None:
        mutations = {
            "section name": lambda root: setattr(
                root[0].ivars["@real_name"], "data", b"Other Region"
            ),
            "section filename": lambda root: setattr(
                root[0].ivars["@filename"], "data", b"other.png"
            ),
            "extra compiled point": lambda root: root[0].ivars["@point"].append(
                [9, 9, ruby_text("Extra"), None, None, None, None, None]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_section_"
            ) as temporary:
                root = Path(temporary) / "game"
                prepare_v21_game(root, point_validation=True)
                compiled_path = root / "Data" / "town_map.dat"
                compiled = load(compiled_path)
                mutate(compiled)
                compiled_path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ExtractionIntegrityError,
                    "sections PBS et compilées|TownMap",
                ):
                    PokemonEssentialsAdapter().extract_with_provenance(root)

    def test_point_extraction_refuses_a_missing_compiled_town_map(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_missing_" ) as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, point_validation=True)
            (root / "Data" / "town_map.dat").unlink()

            with self.assertRaisesRegex(
                ExtractionIntegrityError,
                "liés|town_map",
            ):
                PokemonEssentialsAdapter().extract_with_provenance(root)

    def test_point_structure_proof_is_immutable_during_studio_save(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_studio_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_point_project(base)
            original_payload = csv_path.read_bytes()
            with io.StringIO(original_payload.decode("utf-8-sig"), newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";", strict=True)
                fields = list(reader.fieldnames or [])
                rows = list(reader)
            target = next(row for row in rows if row["id_stable"] == selected["id_stable"])
            target["pbs_compiled_structure"] = "{}"
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
            altered_payload = output.getvalue().encode("utf-8-sig")

            with TranslationProjectSession(
                csv_path,
                game_root=root,
                expected_adapter_id="pokemon_essentials",
            ) as session:
                self.assertTrue(session.writable, session.read_only_reason)
                with self.assertRaisesRegex(
                    TranslationProjectError,
                    "occurrence|donnée source",
                ):
                    session.save(altered_payload)

            self.assertEqual(original_payload, csv_path.read_bytes())

    def test_v21_private_point_roundtrip_changes_only_the_target_subfield(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_point_project(base)
            target = base / "candidate"
            reports = base / "reports"
            point_path = root / "PBS" / "town_map.txt"
            compiled_path = root / "Data" / "town_map.dat"
            original_payload = point_path.read_bytes()
            original_compiled = compiled_path.read_bytes()
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")

            plan = build_v21_1_point_validation_plan(root, csv_path)
            self.assertEqual(V21_1_POINT_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(1, plan.counts().get("applicable", 0))
            item = next(item for item in plan.items if item.decision == "applicable")
            self.assertEqual("PBS v21.1 — Point.Name", item.type)
            self.assertEqual("3", str(item.pbs_field_count))
            self.assertEqual("2", str(item.pbs_field_index))
            compiled_proof = json.loads(item.pbs_compiled_structure)
            self.assertEqual(COMPILED_POINT_PROOF_FORMAT, compiled_proof["format"])
            self.assertEqual([0, "@point", 0, 2], compiled_proof["compiled_path"])

            simulate_plan(plan)
            result = reconstruct_copy(plan, target, reports)

            self.assertEqual(
                ["Data/town_map.dat", "PBS/town_map.txt"],
                result.modified_files,
            )
            self.assertEqual(1, result.applied)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            expected_payload = original_payload.replace(
                selected["texte_source"].encode("utf-8"),
                selected["traduction_fr"].encode("utf-8"),
                1,
            )
            self.assertEqual(expected_payload, (target / "PBS" / "town_map.txt").read_bytes())

            rebuilt = {
                row["id_stable"]: row
                for row in extract_pbs(
                    target / "PBS" / "town_map.txt",
                    "PBS/town_map.txt",
                )
            }[selected["id_stable"]]
            self.assertEqual(selected["traduction_fr"], rebuilt["texte_source"])
            self.assertEqual(selected["sous_index"], rebuilt["sous_index"])
            self.assertEqual(3, rebuilt["pbs_field_count"])
            self.assertEqual(2, rebuilt["pbs_field_index"])

            original_root = load_town_map_bytes(original_compiled)
            candidate_raw = (target / "Data" / "town_map.dat").read_bytes()
            candidate_root = load_town_map_bytes(candidate_raw)
            original_target = original_root[0].ivars["@point"][0][2]
            candidate_target = candidate_root[0].ivars["@point"][0][2]
            self.assertEqual(selected["texte_source"], original_target.text())
            self.assertEqual(selected["traduction_fr"], candidate_target.text())
            self.assertEqual(
                graph_sha256(original_root, masked_string=original_target),
                graph_sha256(candidate_root, masked_string=candidate_target),
            )
            self.assertEqual(
                dumps(original_root[0].ivars["@point"][1]),
                dumps(candidate_root[0].ivars["@point"][1]),
            )
            self.assertEqual(
                selected["traduction_fr"],
                extract_compiled_point_text(
                    candidate_raw,
                    section="0",
                    occurrence=1,
                    field_index=2,
                    pbs_fields=["4", "7", selected["traduction_fr"]],
                ),
            )

            comparison = compare_snapshots(
                source_before,
                snapshot_tree(target),
                allowed_changed={"Data/town_map.dat", "PBS/town_map.txt"},
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)
            self.assertEqual(
                {
                    "LANCER_VERSION_FR.bat",
                    "LIRE_AVANT_DE_JOUER.txt",
                    "PFT_RECONSTRUCTION_V1.0.txt",
                },
                set(comparison.unexpected_files),
            )

    def test_v21_point_refuses_every_structural_or_format_change(self) -> None:
        for mutation in (
            "missing_subfield",
            "additional_subfield",
            "changed_order",
            "wrong_separator",
            "changed_non_text_value",
            "moved_comment",
            "changed_space",
            "changed_bom",
            "changed_crlf",
            "changed_source",
            "altered_proof",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(base)
                plan = build_v21_1_point_validation_plan(root, csv_path)
                point_path = root / "PBS" / "town_map.txt"
                payload = point_path.read_bytes()

                if mutation == "altered_proof":
                    item = next(
                        item for item in plan.items if item.decision == "applicable"
                    )
                    proof = json.loads(item.pbs_point_structure)
                    proof["separator_offsets"] = [1, 4]
                    item.pbs_point_structure = json.dumps(
                        proof,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                elif mutation == "missing_subfield":
                    payload = payload.replace(
                        b"Point = 4,7,Synthetic Route",
                        b"Point = 4,7",
                    )
                elif mutation == "additional_subfield":
                    payload = payload.replace(
                        b"Point = 4,7,Synthetic Route",
                        b"Point = 4,7,Synthetic Route,Unexpected",
                    )
                elif mutation == "changed_order":
                    payload = payload.replace(b"Point = 4,7,", b"Point = 7,4,")
                elif mutation == "wrong_separator":
                    payload = payload.replace(b"Point = 4,7,", b"Point = 4;7,")
                elif mutation == "changed_non_text_value":
                    payload = payload.replace(b"Point = 4,7,", b"Point = 5,7,")
                elif mutation == "moved_comment":
                    payload = payload.replace(
                        b"Point = 4,7,Synthetic Route\r\n"
                        b"# keep this comment exactly here\r\n",
                        b"# keep this comment exactly here\r\n"
                        b"Point = 4,7,Synthetic Route\r\n",
                    )
                elif mutation == "changed_space":
                    payload = payload.replace(b"Point = 4,7,", b"Point  = 4,7,")
                elif mutation == "changed_bom":
                    payload = payload[3:]
                elif mutation == "changed_crlf":
                    payload = payload.replace(b"\r\n", b"\n")
                else:
                    payload = payload.replace(b"Synthetic Route", b"Changed Route", 1)

                if mutation != "altered_proof":
                    point_path.write_bytes(payload)
                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|occurrence bloquée|preuve|format",
                ):
                    simulate_plan(plan)

    def test_v21_point_refuses_compiled_index_type_and_structure_changes(self) -> None:
        for mutation in (
            "wrong_index",
            "wrong_section",
            "wrong_path",
            "changed_non_text_value",
            "changed_target_type",
            "changed_marshal_structure",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_compiled_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(base)
                plan = build_v21_1_point_validation_plan(root, csv_path)
                item = next(
                    item for item in plan.items if item.decision == "applicable"
                )
                if mutation in {"wrong_index", "wrong_section"}:
                    proof = json.loads(item.pbs_compiled_structure)
                    if mutation == "wrong_index":
                        proof["point_index"] = 1
                    else:
                        proof["section"] = 1
                    item.pbs_compiled_structure = json.dumps(
                        proof,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                elif mutation == "wrong_path":
                    item.pbs_compiled_path = '[0,"@point",1,2]'
                else:
                    compiled_path = root / "Data" / "town_map.dat"
                    compiled = load(compiled_path)
                    if mutation == "changed_non_text_value":
                        compiled[0].ivars["@point"][0][0] = 9
                    elif mutation == "changed_target_type":
                        compiled[0].ivars["@point"][0][2] = 123
                    else:
                        del compiled[0].ivars["@flags"]
                    compiled_path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|compilée|town_map|sources",
                ):
                    simulate_plan(plan)

    def test_v21_point_refuses_changed_provenance_and_source_after_plan(self) -> None:
        for mutation in ("provenance", "source_after_plan", "compiled_after_plan"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_guard_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(base)
                plan = build_v21_1_point_validation_plan(root, csv_path)
                simulate_plan(plan)
                target = base / "candidate"
                if mutation == "provenance":
                    plan.project_provenance_token = "altered-provenance"
                elif mutation == "source_after_plan":
                    path = root / "PBS" / "town_map.txt"
                    path.write_bytes(
                        path.read_bytes().replace(
                            b"Synthetic Route",
                            b"Changed after planning",
                            1,
                        )
                    )
                else:
                    path = root / "Data" / "town_map.dat"
                    compiled = load(path)
                    compiled[0].ivars["@point"][0][0] = 9
                    path.write_bytes(dumps(compiled))
                with self.assertRaisesRegex(
                    ReconstructionError,
                    "provenance|sources|source|inventaire|changé",
                ):
                    reconstruct_copy(plan, target, base / "reports")
                self.assertFalse(target.exists())

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

    def test_v21_map_extraction_records_exact_102_402_relationship(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_map_metadata_") as temporary:
            root = Path(temporary) / "game"
            prepare_v21_game(root, map_validation=True)

            rows = extract_map(
                root / "Data" / "Map001.rxdata",
                "Data/Map001.rxdata",
                "Synthetic intro",
                strict=True,
            )

        dialogue = next(row for row in rows if row["type"] == "Dialogue")
        dialogue_segments = json.loads(dialogue["rpg_dialogue_segments"])
        first_choice = next(
            row
            for row in rows
            if row["type"] == "Choix" and row["sous_index"] == 0
        )
        second_choice = next(
            row
            for row in rows
            if row["type"] == "Choix" and row["sous_index"] == 1
        )
        self.assertEqual(1, dialogue["rpg_continuation_end"])
        self.assertEqual("pft_rpg_dialogue_segments_v1", dialogue_segments["format"])
        self.assertEqual([101, 401], [
            segment["command_code"] for segment in dialogue_segments["segments"]
        ])
        self.assertTrue(all(
            segment["command_sha256"] for segment in dialogue_segments["segments"]
        ))
        self.assertEqual((3, 1), (
            first_choice["rpg_choice_branch_command"],
            first_choice["rpg_choice_branch_parameter_index"],
        ))
        self.assertEqual((5, 1), (
            second_choice["rpg_choice_branch_command"],
            second_choice["rpg_choice_branch_parameter_index"],
        ))

    def test_v21_bank_corpus_roundtrip_covers_real_observed_shapes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_bank_corpus_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            target = base / "candidate"
            prepare_v21_game(root, bank_corpus=True)
            source_before = snapshot_tree(root)

            extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [dict(row) for row in extraction.rows]
            selected_sources = {
                "Nested synthetic game bank text",
                "Direct synthetic game bank text",
                "Direct synthetic core bank text",
            }
            selected = [
                row
                for row in rows
                if row["type"] == "Banque de messages"
                and row["texte_source"] in selected_sources
            ]
            self.assertEqual(3, len(selected))
            for index, row in enumerate(selected, start=1):
                row["traduction_fr"] = row["texte_source"] + f" [BANK {index}]"
                row["statut"] = "Accepté"
            csv_path = project / "textes_structures.csv"
            write_extracted_project_csv(csv_path, rows)
            finalize_verified_essentials_project(
                root,
                csv_path,
                adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                declared_version="21.1",
            )

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")
            plan = build_v21_1_bank_corpus_validation_plan(root, csv_path)
            self.assertEqual(V21_1_BANK_CORPUS_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(3, plan.counts().get("applicable", 0))
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(
                ["Data/messages_core.dat", "Data/messages_game.dat"],
                result.modified_files,
            )
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            before_rows = {}
            after_rows = {}
            for relative in ("Data/messages_core.dat", "Data/messages_game.dat"):
                before_rows.update({
                    row["id_stable"]: row["traduction_fr"]
                    for row in extract_message_bank(root / relative, relative)
                })
                after_rows.update({
                    row["id_stable"]: row["traduction_fr"]
                    for row in extract_message_bank(target / relative, relative)
                })
            selected_ids = {row["id_stable"] for row in selected}
            for row in selected:
                self.assertEqual(row["traduction_fr"], after_rows[row["id_stable"]])
            self.assertEqual(
                {key: value for key, value in before_rows.items() if key not in selected_ids},
                {key: value for key, value in after_rows.items() if key not in selected_ids},
            )

    def test_v21_map_dialogue_choice_roundtrip_updates_matching_402_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_map_candidate_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            target = base / "candidate"
            prepare_v21_game(root, map_validation=True)
            source_before = snapshot_tree(root)

            extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [dict(row) for row in extraction.rows]
            dialogue = next(
                row
                for row in rows
                if row["type"] == "Dialogue"
                and row["fichier"] == "Data/Map001.rxdata"
                and row["commande"] == 0
            )
            choice = next(
                row
                for row in rows
                if row["type"] == "Choix"
                and row["fichier"] == "Data/Map001.rxdata"
                and row["sous_index"] == 0
            )
            dialogue["traduction_fr"] = dialogue["texte_source"] + " [TEST MAP]"
            choice["traduction_fr"] = choice["texte_source"] + " [TEST CHOICE]"
            for row in (dialogue, choice):
                row["statut"] = "Accepté"
            csv_path = project / "textes_structures.csv"
            write_extracted_project_csv(csv_path, rows)
            finalize_verified_essentials_project(
                root,
                csv_path,
                adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                declared_version="21.1",
            )

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")
            plan = build_v21_1_map_validation_plan(root, csv_path)
            self.assertEqual(V21_1_MAP_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(2, plan.counts().get("applicable", 0))
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(["Data/Map001.rxdata"], result.modified_files)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            original_map = load(root / "Data" / "Map001.rxdata")
            candidate_map = load(target / "Data" / "Map001.rxdata")
            original_commands = original_map.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
            candidate_commands = candidate_map.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
            self.assertEqual(
                [(cmd.ivars["@code"], cmd.ivars["@indent"]) for cmd in original_commands],
                [(cmd.ivars["@code"], cmd.ivars["@indent"]) for cmd in candidate_commands],
            )
            self.assertEqual(
                dialogue["traduction_fr"].split("\\n")[1],
                candidate_commands[1].ivars["@parameters"][0].text(),
            )
            self.assertEqual(
                choice["traduction_fr"],
                candidate_commands[2].ivars["@parameters"][0][0].text(),
            )
            self.assertEqual(
                choice["traduction_fr"],
                candidate_commands[3].ivars["@parameters"][1].text(),
            )
            self.assertEqual(
                dumps(original_commands[2].ivars["@parameters"][0][1]),
                dumps(candidate_commands[2].ivars["@parameters"][0][1]),
            )
            self.assertEqual(
                dumps(original_commands[5]),
                dumps(candidate_commands[5]),
            )
            reextracted = {
                row["id_stable"]: row["texte_source"]
                for row in extract_map(
                    target / "Data" / "Map001.rxdata",
                    "Data/Map001.rxdata",
                    "Synthetic intro",
                    strict=True,
                )
            }
            self.assertEqual(dialogue["traduction_fr"], reextracted[dialogue["id_stable"]])
            self.assertEqual(choice["traduction_fr"], reextracted[choice["id_stable"]])

    def test_v21_map_candidate_refuses_ambiguous_402_branch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_map_ambiguous_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            prepare_v21_game(
                root,
                map_validation=True,
                ambiguous_choice_branch=True,
            )
            rows = [
                dict(row)
                for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
            ]
            dialogue = next(row for row in rows if row["type"] == "Dialogue")
            choice = next(row for row in rows if row["type"] == "Choix")
            self.assertEqual("", choice["rpg_choice_branch_command"])
            dialogue["traduction_fr"] = dialogue["texte_source"] + " [TEST]"
            choice["traduction_fr"] = choice["texte_source"] + " [TEST]"
            for row in (dialogue, choice):
                row["statut"] = "Accepté"
            csv_path = project / "textes_structures.csv"
            write_extracted_project_csv(csv_path, rows)
            finalize_verified_essentials_project(
                root,
                csv_path,
                adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                declared_version="21.1",
            )

            with self.assertRaisesRegex(ReconstructionError, "402|Branche"):
                build_v21_1_map_validation_plan(root, csv_path)

    def test_v21_map_candidate_reconstructs_explicit_internal_line_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_map_lines_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            target = base / "candidate"
            prepare_v21_game(
                root,
                map_validation=True,
                internal_line_control=True,
            )
            source_before = snapshot_tree(root)
            rows = [
                dict(row)
                for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
            ]
            dialogue = next(row for row in rows if row["type"] == "Dialogue")
            choice = next(row for row in rows if row["type"] == "Choix")
            dialogue["traduction_fr"] = (
                r"Translated \n internal control"
                r"\nTranslated continuation [TEST]"
            )
            choice["traduction_fr"] = choice["texte_source"] + " [TEST]"
            for row in (dialogue, choice):
                row["statut"] = "Accepté"
            csv_path = project / "textes_structures.csv"
            write_extracted_project_csv(csv_path, rows)
            finalize_verified_essentials_project(
                root,
                csv_path,
                adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                declared_version="21.1",
            )

            plan = build_v21_1_map_validation_plan(root, csv_path)
            simulate_plan(plan)
            result = reconstruct_copy(plan, target, base / "reports")

            self.assertEqual(["Data/Map001.rxdata"], result.modified_files)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            original_map = load(root / "Data" / "Map001.rxdata")
            candidate_map = load(target / "Data" / "Map001.rxdata")
            original_commands = original_map.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
            commands = candidate_map.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
            for command_index in (0, 1):
                original_command = original_commands[command_index]
                candidate_command = commands[command_index]
                self.assertEqual(
                    set(original_command.ivars),
                    set(candidate_command.ivars),
                )
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
            self.assertEqual(
                r"Translated \n internal control",
                commands[0].ivars["@parameters"][0].text(),
            )
            self.assertEqual(
                "Translated continuation [TEST]",
                commands[1].ivars["@parameters"][0].text(),
            )
            reextracted = {
                row["id_stable"]: row["texte_source"]
                for row in extract_map(
                    target / "Data" / "Map001.rxdata",
                    "Data/Map001.rxdata",
                    "Synthetic intro",
                    strict=True,
                )
            }
            self.assertEqual(dialogue["traduction_fr"], reextracted[dialogue["id_stable"]])

    def test_v21_map_candidate_refuses_missing_or_tampered_segmentation_proof(self) -> None:
        for metadata in ("", "{}"):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_map_segment_proof_"
            ) as temporary:
                base = Path(temporary)
                root = base / "game"
                project = base / "project"
                prepare_v21_game(
                    root,
                    map_validation=True,
                    internal_line_control=True,
                )
                rows = [
                    dict(row)
                    for row in PokemonEssentialsAdapter().extract_with_provenance(root).rows
                ]
                dialogue = next(row for row in rows if row["type"] == "Dialogue")
                choice = next(row for row in rows if row["type"] == "Choix")
                dialogue["rpg_dialogue_segments"] = metadata
                dialogue["traduction_fr"] = dialogue["texte_source"] + " [TEST]"
                choice["traduction_fr"] = choice["texte_source"] + " [TEST]"
                for row in (dialogue, choice):
                    row["statut"] = "Accepté"
                csv_path = project / "textes_structures.csv"
                write_extracted_project_csv(csv_path, rows)
                finalize_verified_essentials_project(
                    root,
                    csv_path,
                    adapter_profile=ESSENTIALS_V21_1_READONLY_PROFILE,
                    declared_version="21.1",
                )

                if not metadata:
                    with self.assertRaisesRegex(ReconstructionError, "101/401"):
                        build_v21_1_map_validation_plan(root, csv_path)
                else:
                    plan = build_v21_1_map_validation_plan(root, csv_path)
                    with self.assertRaisesRegex(ReconstructionError, "occurrence bloquée"):
                        simulate_plan(plan)
                    dialogue_item = next(
                        item for item in plan.items if item.type == "Dialogue"
                    )
                    self.assertIn("segmentation", dialogue_item.reason.casefold())

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

    def test_v21_private_validation_roundtrip_is_limited_to_one_message_bank_row(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_v21_candidate_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            target = base / "candidate"
            reports = base / "reports"
            prepare_v21_game(root, nested_message_bank=True)
            source_before = snapshot_tree(root)

            extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [dict(row) for row in extraction.rows]
            selected = next(
                row
                for row in rows
                if row["type"] == "Banque de messages"
                and row["fichier"] == "Data/messages_game.dat"
                and not row["traduction_fr"]
            )
            selected["traduction_fr"] = selected["texte_source"] + " [TEST PFT v21.1]"
            selected["statut"] = "Accepté"
            selected["origine_traduction"] = "validation_synthetique_v21_1"
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

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")

            plan = build_v21_1_validation_plan(root, csv_path)
            self.assertEqual(V21_1_VALIDATION_SCOPE, plan.validation_scope)
            self.assertEqual(ESSENTIALS_V21_1_READONLY_PROFILE, plan.adapter_profile)
            self.assertEqual(1, plan.counts().get("applicable", 0))
            self.assertEqual("Banque de messages", next(
                item.type for item in plan.items if item.decision == "applicable"
            ))

            simulate_plan(plan)
            self.assertEqual(1, plan.counts().get("applicable", 0))
            result = reconstruct_copy(plan, target, reports)

            self.assertEqual(["Data/messages_game.dat"], result.modified_files)
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            source_after = snapshot_tree(root)
            self.assertTrue(compare_snapshots(source_before, source_after).passed)

            before_rows = {
                row["id_stable"]: row["traduction_fr"]
                for row in extract_message_bank(
                    root / "Data" / "messages_game.dat",
                    "Data/messages_game.dat",
                )
            }
            after_rows = {
                row["id_stable"]: row["traduction_fr"]
                for row in extract_message_bank(
                    target / "Data" / "messages_game.dat",
                    "Data/messages_game.dat",
                )
            }
            self.assertEqual(
                selected["traduction_fr"],
                after_rows[selected["id_stable"]],
            )
            self.assertEqual(
                {key: value for key, value in before_rows.items() if key != selected["id_stable"]},
                {key: value for key, value in after_rows.items() if key != selected["id_stable"]},
            )
            original_bank = load(root / "Data" / "messages_game.dat")
            candidate_bank = load(target / "Data" / "messages_game.dat")
            self.assertEqual(2, len(original_bank))
            self.assertEqual(2, len(candidate_bank))
            original_untouched = next(iter(original_bank[1].values()))
            candidate_untouched = next(iter(candidate_bank[1].values()))
            self.assertEqual(dumps(original_untouched), dumps(candidate_untouched))

            candidate = snapshot_tree(target)
            comparison = compare_snapshots(
                source_before,
                candidate,
                allowed_changed={"Data/messages_game.dat"},
            )
            self.assertFalse(comparison.missing_files)
            self.assertFalse(comparison.changed_files)
            self.assertFalse(comparison.emptied_files)
            self.assertEqual(
                {
                    "LANCER_VERSION_FR.bat",
                    "LIRE_AVANT_DE_JOUER.txt",
                    "PFT_RECONSTRUCTION_V1.0.txt",
                },
                set(comparison.unexpected_files),
            )

    def test_v21_private_validation_rejects_point_common_event_and_multiple_rows(self) -> None:
        refused_types = (
            "PBS v21.1 — Point.Name",
            "Événement commun — Dialogue",
        )
        for refused_type in refused_types:
            with self.subTest(refused_type=refused_type), tempfile.TemporaryDirectory(
                prefix="pft_test_v21_scope_"
            ) as temporary:
                base = Path(temporary)
                root = base / "game"
                project = base / "project"
                prepare_v21_game(root, nested_message_bank=True)
                extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
                rows = [dict(row) for row in extraction.rows]
                selected = next(row for row in rows if row["type"] == "Banque de messages")
                selected["type"] = refused_type
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

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "validation v21.1|banque de messages",
                ):
                    build_v21_1_validation_plan(root, csv_path)

        with tempfile.TemporaryDirectory(prefix="pft_test_v21_scope_multi_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            prepare_v21_game(root, nested_message_bank=True)
            extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [dict(row) for row in extraction.rows]
            bank_rows = [row for row in rows if row["type"] == "Banque de messages"]
            self.assertGreaterEqual(len(bank_rows), 2)
            for row in bank_rows[:2]:
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

            with self.assertRaisesRegex(ReconstructionError, "une seule occurrence"):
                build_v21_1_validation_plan(root, csv_path)

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
