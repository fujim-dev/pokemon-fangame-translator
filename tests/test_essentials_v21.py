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
    V21_1_POINT_DESCRIPTION_SEVEN_FIELDS_VALIDATION_SCOPE,
    V21_1_POINT_DESCRIPTION_VALIDATION_SCOPE,
    V21_1_POINT_NAME_EIGHT_FIELDS_VALIDATION_SCOPE,
    V21_1_POINT_VALIDATION_SCOPE,
    V21_1_VALIDATION_SCOPE,
    PlanItem,
    ReconstructionError,
    _apply_pbs_items,
    build_plan,
    build_v21_1_bank_corpus_validation_plan,
    build_v21_1_map_validation_plan,
    build_v21_1_point_description_seven_fields_validation_plan,
    build_v21_1_point_description_validation_plan,
    build_v21_1_point_name_eight_fields_validation_plan,
    build_v21_1_point_validation_plan,
    build_v21_1_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import RubyHashKey, RubyObject, RubyString, load
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
    phone_validation: bool = False,
    trainer_validation: bool = False,
    ability_validation: bool = False,
    move_validation: bool = False,
    item_validation: bool = False,
    species_validation: bool = False,
    map_metadata_validation: bool = False,
    point_eight_switch: int = 51,
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
    if phone_validation:
        default_messages = RubyObject(
            "GameData::PhoneMessage",
            {
                "@id": ruby_text("default"),
                "@trainer_type": ruby_text("default"),
                "@real_name": None,
                "@version": 0,
                "@intro": [ruby_text("Synthetic default intro one"), ruby_text("Synthetic default intro two")],
                "@intro_morning": None,
                "@intro_afternoon": None,
                "@intro_evening": None,
                "@body": [ruby_text("Synthetic default body")],
                "@body1": None,
                "@body2": None,
                "@battle_request": None,
                "@battle_remind": None,
                "@end": [ruby_text("Synthetic default end")],
                "@pbs_file_suffix": ruby_text(""),
            },
        )
        trainer_messages = RubyObject(
            "GameData::PhoneMessage",
            {
                "@id": ["YOUNGSTER", ruby_text("Synthetic Contact"), 0],
                "@trainer_type": "YOUNGSTER",
                "@real_name": ruby_text("Synthetic Contact"),
                "@version": 0,
                "@intro": [ruby_text("Synthetic trainer intro")],
                "@intro_morning": None,
                "@intro_afternoon": None,
                "@intro_evening": None,
                "@body": None,
                "@body1": None,
                "@body2": None,
                "@battle_request": None,
                "@battle_remind": None,
                "@end": [ruby_text("Synthetic trainer end")],
                "@pbs_file_suffix": ruby_text(""),
            },
        )
        phone_root = {
            ruby_text("default"): default_messages,
            RubyHashKey(["YOUNGSTER", ruby_text("Synthetic Contact"), 0]): trainer_messages,
        }
        (data / "phone.dat").write_bytes(dumps(phone_root))
        runtime_phone_messages = [
            "Synthetic default intro one",
            "Synthetic default intro two",
            "Synthetic default body",
            "Synthetic default end",
            "Synthetic trainer intro",
            "Synthetic trainer end",
        ]
        bank = [{} for _index in range(31)]
        bank[22] = {
            ruby_text(message): ruby_text(message)
            for message in runtime_phone_messages
        }
    elif trainer_validation:
        def trainer_object(trainer_type: str, name: str, lose_text: str) -> RubyObject:
            return RubyObject(
                "GameData::Trainer",
                {
                    "@id": [trainer_type, ruby_text(name), 0],
                    "@trainer_type": trainer_type,
                    "@real_name": ruby_text(name),
                    "@version": 0,
                    "@items": [],
                    "@real_lose_text": ruby_text(lose_text),
                    "@pokemon": [
                        {
                            "species": "BULBASAUR",
                            "level": 5,
                            "synthetic_metadata": ruby_text("preserve trainer metadata"),
                        }
                    ],
                    "@pbs_file_suffix": ruby_text(""),
                },
            )

        target_lose = "Synthetic unique trainer defeat"
        shared_lose = "Synthetic shared trainer defeat"
        trainer_root = {
            RubyHashKey(
                ["YOUNGSTER", ruby_text("Synthetic Battler"), 0]
            ): trainer_object("YOUNGSTER", "Synthetic Battler", target_lose),
            RubyHashKey(
                ["CAMPER", ruby_text("Synthetic Shared One"), 0]
            ): trainer_object("CAMPER", "Synthetic Shared One", shared_lose),
            RubyHashKey(
                ["CAMPER", ruby_text("Synthetic Shared Two"), 0]
            ): trainer_object("CAMPER", "Synthetic Shared Two", shared_lose),
            RubyHashKey(
                ["RIVAL", ruby_text("Synthetic Placeholder"), 0]
            ): trainer_object("RIVAL", "Synthetic Placeholder", "..."),
        }
        (data / "trainers.dat").write_bytes(dumps(trainer_root))
        bank = [{} for _index in range(31)]
        bank[23] = {
            ruby_text(target_lose): ruby_text(target_lose),
            ruby_text(shared_lose): ruby_text(shared_lose),
            ruby_text("..."): ruby_text("..."),
        }
    if ability_validation:
        def ability_object(
            identifier: str,
            name: str,
            description: str,
            flags: tuple[str, ...] = (),
        ) -> RubyObject:
            return RubyObject(
                "GameData::Ability",
                {
                    "@id": identifier,
                    "@real_name": ruby_text(name),
                    "@real_description": ruby_text(description),
                    "@flags": [ruby_text(flag) for flag in flags],
                    "@pbs_file_suffix": ruby_text(""),
                },
            )

        target_description = "Synthetic unique ability description."
        shared_description = "Synthetic shared ability description."
        ability_root = {
            "OVERGROW": ability_object(
                "OVERGROW", "Synthetic Overgrow", target_description
            ),
            "BLAZE": ability_object(
                "BLAZE",
                "Synthetic Blaze",
                shared_description,
                ("FasterEggHatching",),
            ),
            "TORRENT": ability_object(
                "TORRENT", "Synthetic Torrent", shared_description
            ),
        }
        (data / "abilities.dat").write_bytes(dumps(ability_root))
        core_bank = [{} for _index in range(31)]
        core_bank[11] = {
            ruby_text(target_description): ruby_text(target_description),
            ruby_text(shared_description): ruby_text(shared_description),
        }
    if move_validation:
        from essentials_move import MOVE_IVARS

        def move_object(
            identifier: str,
            name: str,
            description: str,
            *,
            move_type: str,
            category: int,
            power: int,
            accuracy: int,
            total_pp: int,
            target: str,
            priority: int = 0,
            function_code: str = "None",
            flags: tuple[str, ...] = (),
            effect_chance: int = 0,
        ) -> RubyObject:
            values = {
                "@id": identifier,
                "@real_name": ruby_text(name),
                "@type": move_type,
                "@category": category,
                "@power": power,
                "@accuracy": accuracy,
                "@total_pp": total_pp,
                "@target": target,
                "@priority": priority,
                "@function_code": ruby_text(function_code),
                "@flags": [ruby_text(flag) for flag in flags],
                "@effect_chance": effect_chance,
                "@real_description": ruby_text(description),
                "@pbs_file_suffix": ruby_text(""),
            }
            return RubyObject(
                "GameData::Move",
                {ivar: values[ivar] for ivar in MOVE_IVARS},
            )

        target_move_description = "Synthetic unique move description."
        shared_move_description = "Synthetic shared move description."
        move_root = {
            "TACKLE": move_object(
                "TACKLE",
                "Synthetic Tackle",
                target_move_description,
                move_type="NORMAL",
                category=0,
                power=40,
                accuracy=100,
                total_pp=35,
                target="NearOther",
                flags=("Contact", "CanProtect"),
            ),
            "EMBER": move_object(
                "EMBER",
                "Synthetic Ember",
                shared_move_description,
                move_type="FIRE",
                category=1,
                power=40,
                accuracy=100,
                total_pp=25,
                target="NearOther",
                flags=("CanProtect",),
                effect_chance=10,
            ),
            "GROWL": move_object(
                "GROWL",
                "Synthetic Growl",
                shared_move_description,
                move_type="NORMAL",
                category=2,
                power=0,
                accuracy=100,
                total_pp=40,
                target="AllNearFoes",
                priority=-1,
            ),
        }
        (data / "moves.dat").write_bytes(dumps(move_root))
        core_bank = [{} for _index in range(31)]
        core_bank[5] = {
            ruby_text("Synthetic Tackle"): ruby_text("Synthetic Tackle"),
            ruby_text("Synthetic obsolete move alias"): ruby_text(
                "Synthetic obsolete move alias"
            ),
            ruby_text("Synthetic Ember"): ruby_text("Synthetic Ember"),
            ruby_text("Synthetic Growl"): ruby_text("Synthetic Growl"),
        }
        core_bank[6] = {
            ruby_text(target_move_description): ruby_text(target_move_description),
            ruby_text(shared_move_description): ruby_text(shared_move_description),
        }
    if item_validation:
        from essentials_item import ITEM_IVARS

        def item_object(
            identifier: str,
            name: str,
            name_plural: str,
            description: str,
            *,
            pocket: int,
            price: int,
            sell_price: int,
            bp_price: int = 1,
            field_use: int = 0,
            battle_use: int = 0,
            flags: tuple[str, ...] = (),
            consumable: bool = True,
            move: str | None = None,
            portion_name: str | None = None,
            portion_name_plural: str | None = None,
        ) -> RubyObject:
            values = {
                "@id": identifier,
                "@real_name": ruby_text(name),
                "@real_name_plural": ruby_text(name_plural),
                "@real_portion_name": (
                    ruby_text(portion_name) if portion_name is not None else None
                ),
                "@real_portion_name_plural": (
                    ruby_text(portion_name_plural)
                    if portion_name_plural is not None
                    else None
                ),
                "@pocket": pocket,
                "@price": price,
                "@sell_price": sell_price,
                "@bp_price": bp_price,
                "@field_use": field_use,
                "@battle_use": battle_use,
                "@flags": [ruby_text(flag) for flag in flags],
                "@consumable": consumable,
                "@show_quantity": None,
                "@move": move,
                "@real_description": ruby_text(description),
                "@pbs_file_suffix": ruby_text(""),
            }
            return RubyObject(
                "GameData::Item",
                {ivar: values[ivar] for ivar in ITEM_IVARS},
            )

        target_item_description = "Synthetic unique item description."
        shared_item_description = "Synthetic shared item description."
        machine_description = "Synthetic machine item description."
        item_root = {
            "POTION": item_object(
                "POTION",
                "Synthetic Potion",
                "Synthetic Potions",
                target_item_description,
                pocket=2,
                price=300,
                sell_price=150,
                field_use=1,
                battle_use=1,
                flags=("Fling_30",),
            ),
            "ORANBERRY": item_object(
                "ORANBERRY",
                "Synthetic Oran Berry",
                "Synthetic Oran Berries",
                shared_item_description,
                pocket=5,
                price=20,
                sell_price=10,
                bp_price=2,
                field_use=1,
                battle_use=1,
                flags=("Berry", "Fling_10"),
                consumable=False,
                portion_name="Synthetic Oran portion",
                portion_name_plural="Synthetic Oran portions",
            ),
            "SITRUSBERRY": item_object(
                "SITRUSBERRY",
                "Synthetic Sitrus Berry",
                "Synthetic Sitrus Berries",
                shared_item_description,
                pocket=5,
                price=20,
                sell_price=10,
                field_use=1,
                battle_use=1,
                flags=("Berry",),
                portion_name="Synthetic Sitrus portion",
                portion_name_plural="Synthetic Sitrus portions",
            ),
            "TM001": item_object(
                "TM001",
                "Synthetic TM",
                "Synthetic TMs",
                machine_description,
                pocket=4,
                price=3000,
                sell_price=1500,
                field_use=3,
                consumable=False,
                move="TACKLE",
            ),
        }
        (data / "items.dat").write_bytes(dumps(item_root))
        core_bank = [{} for _index in range(31)]
        core_bank[7] = {
            ruby_text("Synthetic obsolete item alias"): ruby_text(
                "Synthetic obsolete item alias"
            ),
        }
        core_bank[7].update(
            {
                ruby_text(value.ivars["@real_name"].text()): ruby_text(
                    value.ivars["@real_name"].text()
                )
                for value in item_root.values()
            }
        )
        core_bank[8] = {
            ruby_text(value.ivars["@real_name_plural"].text()): ruby_text(
                value.ivars["@real_name_plural"].text()
            )
            for value in item_root.values()
        }
        core_bank[9] = {
            ruby_text(message): ruby_text(message)
            for message in (
                target_item_description,
                shared_item_description,
                machine_description,
            )
        }
        core_bank[28] = {
            ruby_text(value.ivars["@real_portion_name"].text()): ruby_text(
                value.ivars["@real_portion_name"].text()
            )
            for value in item_root.values()
            if value.ivars["@real_portion_name"] is not None
        }
        core_bank[29] = {
            ruby_text(value.ivars["@real_portion_name_plural"].text()): ruby_text(
                value.ivars["@real_portion_name_plural"].text()
            )
            for value in item_root.values()
            if value.ivars["@real_portion_name_plural"] is not None
        }
    if species_validation:
        from essentials_species import SPECIES_IVARS

        def species_object(
            identifier: str,
            species: str,
            form: int,
            name: str,
            category_value: RubyString,
            pokedex_value: RubyString,
        ) -> RubyObject:
            values = {
                "@id": identifier,
                "@species": species,
                "@form": form,
                "@real_name": ruby_text(name),
                "@real_form_name": None,
                "@real_category": category_value,
                "@real_pokedex_entry": pokedex_value,
                "@pokedex_form": form,
                "@types": ["NORMAL"],
                "@base_stats": {"HP": 45},
                "@evs": {},
                "@base_exp": 64,
                "@growth_rate": "Medium",
                "@gender_ratio": "Female50Percent",
                "@catch_rate": 45,
                "@happiness": 70,
                "@moves": [[1, "TACKLE"]],
                "@tutor_moves": [],
                "@egg_moves": [],
                "@abilities": ["OVERGROW"],
                "@hidden_abilities": [],
                "@wild_item_common": [],
                "@wild_item_uncommon": [],
                "@wild_item_rare": [],
                "@egg_groups": ["Monster"],
                "@hatch_steps": 5120,
                "@incense": None,
                "@offspring": [],
                "@evolutions": [],
                "@height": 7,
                "@weight": 69,
                "@color": "Green",
                "@shape": "Quadruped",
                "@habitat": "Grassland",
                "@generation": 1,
                "@flags": [],
                "@mega_stone": None,
                "@mega_move": None,
                "@unmega_form": 0,
                "@mega_message": 0,
                "@pbs_file_suffix": ruby_text(""),
            }
            return RubyObject(
                "GameData::Species",
                {ivar: values[ivar] for ivar in SPECIES_IVARS},
            )

        target_pokedex = ruby_text("Synthetic unique Bulbasaur Pokédex entry.")
        inherited_pokedex = ruby_text("Synthetic inherited Cubone Pokédex entry.")
        charmander_pokedex = ruby_text("Synthetic Charmander Pokédex entry.")
        explicit_form_pokedex = ruby_text("Synthetic explicit form Pokédex entry.")
        target_category = ruby_text("Synthetic unique Seed")
        inherited_category = ruby_text("Synthetic shared Lonely")
        charmander_category = ruby_text("Synthetic Lizard")
        explicit_form_category = ruby_text("Synthetic Regional Lizard")
        species_root = {
            "BULBASAUR": species_object(
                "BULBASAUR",
                "BULBASAUR",
                0,
                "Synthetic Bulbasaur",
                target_category,
                target_pokedex,
            ),
            "CUBONE": species_object(
                "CUBONE",
                "CUBONE",
                0,
                "Synthetic Cubone",
                inherited_category,
                inherited_pokedex,
            ),
            "CHARMANDER": species_object(
                "CHARMANDER",
                "CHARMANDER",
                0,
                "Synthetic Charmander",
                charmander_category,
                charmander_pokedex,
            ),
            "CUBONE_1": species_object(
                "CUBONE_1",
                "CUBONE",
                1,
                "Synthetic Cubone",
                inherited_category,
                inherited_pokedex,
            ),
            "CHARMANDER_1": species_object(
                "CHARMANDER_1",
                "CHARMANDER",
                1,
                "Synthetic Charmander",
                explicit_form_category,
                explicit_form_pokedex,
            ),
        }
        (data / "species.dat").write_bytes(dumps(species_root))
        core_bank = [{} for _index in range(31)]
        core_bank[3] = {
            ruby_text(message): ruby_text(message)
            for message in (
                target_pokedex.text(),
                inherited_pokedex.text(),
                charmander_pokedex.text(),
                explicit_form_pokedex.text(),
            )
        }
        core_bank[2] = {
            ruby_text(message): ruby_text(message)
            for message in (
                target_category.text(),
                inherited_category.text(),
                charmander_category.text(),
                explicit_form_category.text(),
            )
        }
    if map_metadata_validation:
        from essentials_map_metadata import MAP_METADATA_IVARS

        def map_metadata_object(
            map_id: int,
            name: str,
            *,
            outdoor: bool | None = None,
            announce: bool | None = None,
            position: list[int] | None = None,
        ) -> RubyObject:
            values = {
                "@id": map_id,
                "@real_name": ruby_text(name),
                "@outdoor_map": outdoor,
                "@announce_location": announce,
                "@can_bicycle": None,
                "@always_bicycle": None,
                "@teleport_destination": None,
                "@weather": None,
                "@town_map_position": position,
                "@dive_map_id": None,
                "@dark_map": None,
                "@safari_map": None,
                "@snap_edges": None,
                "@still_reflections": None,
                "@random_dungeon": None,
                "@battle_background": ruby_text("field") if outdoor else None,
                "@wild_battle_BGM": None,
                "@trainer_battle_BGM": None,
                "@wild_victory_BGM": None,
                "@trainer_victory_BGM": None,
                "@wild_capture_ME": None,
                "@town_map_size": None,
                "@battle_environment": None,
                "@flags": [],
                "@pbs_file_suffix": ruby_text(""),
            }
            return RubyObject(
                "GameData::MapMetadata",
                {ivar: values[ivar] for ivar in MAP_METADATA_IVARS},
            )

        target_map_name = "Synthetic unique route"
        shared_map_name = "Synthetic shared town"
        map_metadata_root = {
            1: map_metadata_object(
                1,
                target_map_name,
                outdoor=True,
                announce=True,
                position=[0, 4, 7],
            ),
            2: map_metadata_object(2, shared_map_name, position=[0, 4, 8]),
            3: map_metadata_object(3, shared_map_name, position=[0, 4, 8]),
        }
        (data / "map_metadata.dat").write_bytes(dumps(map_metadata_root))
        bank = [{} for _index in range(31)]
        target_map_key = ruby_text(target_map_name)
        bank[19] = {
            target_map_key: ruby_text(target_map_name),
        }
        bank[21] = {
            target_map_key: ruby_text(target_map_name),
            ruby_text(shared_map_name): ruby_text(shared_map_name),
        }
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
    if species_validation:
        pbs.write_bytes(
            b"\xef\xbb\xbf# synthetic species fixture\r\n"
            b"[BULBASAUR]\r\n"
            b"Name = Synthetic Bulbasaur\r\n"
            b"Category = Synthetic unique Seed\r\n"
            b"Color = Green\r\n"
            b"GrowthRate = Medium\r\n"
            b"Pokedex = Synthetic unique Bulbasaur Pok\xc3\xa9dex entry.\r\n"
            b"\r\n"
            b"[CUBONE]\r\n"
            b"Name = Synthetic Cubone\r\n"
            b"Category = Synthetic shared Lonely\r\n"
            b"Color = Green\r\n"
            b"GrowthRate = Medium\r\n"
            b"Pokedex = Synthetic inherited Cubone Pok\xc3\xa9dex entry.\r\n"
            b"\r\n"
            b"[CHARMANDER]\r\n"
            b"Name = Synthetic Charmander\r\n"
            b"Category = Synthetic Lizard\r\n"
            b"Color = Green\r\n"
            b"GrowthRate = Medium\r\n"
            b"Pokedex = Synthetic Charmander Pok\xc3\xa9dex entry.\r\n"
        )
        (pbs.parent / "pokemon_forms.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic species forms fixture\r\n"
            b"[CUBONE,1]\r\n"
            b"FormName = Synthetic inherited form\r\n"
            b"\r\n"
            b"[CHARMANDER,1]\r\n"
            b"FormName = Synthetic explicit form\r\n"
            b"Category = Synthetic Regional Lizard\r\n"
            b"Pokedex = Synthetic explicit form Pok\xc3\xa9dex entry.\r\n"
        )
    if phone_validation:
        (pbs.parent / "phone.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic phone fixture\r\n"
            b"[default]\r\n"
            b"Intro = Synthetic default intro one\r\n"
            b"End = Synthetic default end\r\n"
            b"Intro = Synthetic default intro two\r\n"
            b"Body = Synthetic default body\r\n"
            b"\r\n"
            b"[YOUNGSTER,Synthetic Contact]\r\n"
            b"Intro = Synthetic trainer intro\r\n"
            b"End = Synthetic trainer end\r\n"
        )
    if trainer_validation:
        (pbs.parent / "trainers.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic trainer fixture\r\n"
            b"[YOUNGSTER,Synthetic Battler]\r\n"
            b"LoseText = Synthetic unique trainer defeat\r\n"
            b"Pokemon = BULBASAUR,5\r\n"
            b"\r\n"
            b"[CAMPER,Synthetic Shared One]\r\n"
            b"LoseText = Synthetic shared trainer defeat\r\n"
            b"Pokemon = CHARMANDER,5\r\n"
            b"\r\n"
            b"[CAMPER,Synthetic Shared Two]\r\n"
            b"LoseText = Synthetic shared trainer defeat\r\n"
            b"Pokemon = SQUIRTLE,5\r\n"
            b"\r\n"
            b"[RIVAL,Synthetic Placeholder]\r\n"
            b"LoseText = ...\r\n"
            b"Pokemon = PIKACHU,5\r\n"
        )
    if ability_validation:
        (pbs.parent / "abilities.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic ability fixture\r\n"
            b"[OVERGROW]\r\n"
            b"Name = Synthetic Overgrow\r\n"
            b"Description = Synthetic unique ability description.\r\n"
            b"\r\n"
            b"[BLAZE]\r\n"
            b"Name = Synthetic Blaze\r\n"
            b"Description = Synthetic shared ability description.\r\n"
            b"Flags = FasterEggHatching\r\n"
            b"\r\n"
            b"[TORRENT]\r\n"
            b"Name = Synthetic Torrent\r\n"
            b"Description = Synthetic shared ability description.\r\n"
        )
    if move_validation:
        (pbs.parent / "moves.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic move fixture\r\n"
            b"[TACKLE]\r\n"
            b"Name = Synthetic Tackle\r\n"
            b"Type = NORMAL\r\n"
            b"Category = Physical\r\n"
            b"Power = 40\r\n"
            b"Accuracy = 100\r\n"
            b"TotalPP = 35\r\n"
            b"Target = NearOther\r\n"
            b"FunctionCode = None\r\n"
            b"Flags = Contact,CanProtect\r\n"
            b"Description = Synthetic unique move description.\r\n"
            b"\r\n"
            b"[EMBER]\r\n"
            b"Name = Synthetic Ember\r\n"
            b"Type = FIRE\r\n"
            b"Category = Special\r\n"
            b"Power = 40\r\n"
            b"Accuracy = 100\r\n"
            b"TotalPP = 25\r\n"
            b"Target = NearOther\r\n"
            b"FunctionCode = None\r\n"
            b"Flags = CanProtect\r\n"
            b"EffectChance = 10\r\n"
            b"Description = Synthetic shared move description.\r\n"
            b"\r\n"
            b"[GROWL]\r\n"
            b"Name = Synthetic Growl\r\n"
            b"Type = NORMAL\r\n"
            b"Category = Status\r\n"
            b"Accuracy = 100\r\n"
            b"TotalPP = 40\r\n"
            b"Target = AllNearFoes\r\n"
            b"Priority = -1\r\n"
            b"FunctionCode = None\r\n"
            b"Description = Synthetic shared move description.\r\n"
        )
    if item_validation:
        (pbs.parent / "items.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic item fixture\r\n"
            b"[POTION]\r\n"
            b"Name = Synthetic Potion\r\n"
            b"NamePlural = Synthetic Potions\r\n"
            b"Pocket = 2\r\n"
            b"Price = 300\r\n"
            b"FieldUse = OnPokemon\r\n"
            b"BattleUse = OnPokemon\r\n"
            b"Flags = Fling_30\r\n"
            b"Description = Synthetic unique item description.\r\n"
            b"\r\n"
            b"[ORANBERRY]\r\n"
            b"Name = Synthetic Oran Berry\r\n"
            b"NamePlural = Synthetic Oran Berries\r\n"
            b"PortionName = Synthetic Oran portion\r\n"
            b"PortionNamePlural = Synthetic Oran portions\r\n"
            b"Pocket = 5\r\n"
            b"Price = 20\r\n"
            b"BPPrice = 2\r\n"
            b"FieldUse = OnPokemon\r\n"
            b"BattleUse = OnPokemon\r\n"
            b"Flags = Berry,Fling_10\r\n"
            b"Consumable = false\r\n"
            b"Description = Synthetic shared item description.\r\n"
            b"\r\n"
            b"[SITRUSBERRY]\r\n"
            b"Name = Synthetic Sitrus Berry\r\n"
            b"NamePlural = Synthetic Sitrus Berries\r\n"
            b"PortionName = Synthetic Sitrus portion\r\n"
            b"PortionNamePlural = Synthetic Sitrus portions\r\n"
            b"Pocket = 5\r\n"
            b"Price = 20\r\n"
            b"FieldUse = OnPokemon\r\n"
            b"BattleUse = OnPokemon\r\n"
            b"Flags = Berry\r\n"
            b"Description = Synthetic shared item description.\r\n"
            b"\r\n"
            b"[TM001]\r\n"
            b"Name = Synthetic TM\r\n"
            b"NamePlural = Synthetic TMs\r\n"
            b"Pocket = 4\r\n"
            b"Price = 3000\r\n"
            b"FieldUse = TM\r\n"
            b"Move = TACKLE\r\n"
            b"Description = Synthetic machine item description.\r\n"
        )
    if map_metadata_validation:
        (pbs.parent / "map_metadata.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic map metadata fixture\r\n"
            b"[001]   # Synthetic unique route\r\n"
            b"Name = Synthetic unique route\r\n"
            b"Outdoor = true\r\n"
            b"ShowArea = true\r\n"
            b"MapPosition = 0,4,7\r\n"
            b"BattleBack = field\r\n"
            b"\r\n"
            b"[002]   # Synthetic shared town one\r\n"
            b"Name = Synthetic shared town\r\n"
            b"MapPosition = 0,4,8\r\n"
            b"\r\n"
            b"[003]   # Synthetic shared town two\r\n"
            b"Name = Synthetic shared town\r\n"
            b"MapPosition = 0,4,8\r\n"
        )
    if point_validation:
        (pbs.parent / "town_map.txt").write_bytes(
            b"\xef\xbb\xbf# synthetic point fixture\r\n"
            b"[0]\r\n"
            b"Name = Synthetic Region\r\n"
            b"Filename = synthetic.png\r\n"
            b"Point = 4,7,Synthetic Route\r\n"
            b"# keep this comment exactly here\r\n"
            b"Point = 5,8,Untouched Town,Untouched Description,1,2,3\r\n"
            b"Point = 6,9,Synthetic Coast,Synthetic Tunnel\r\n"
            + f"Point = 7,10,Synthetic Hidden Island,,,,,{point_eight_switch}\r\n".encode("ascii")
            + b"Point = 8,10,Untouched Hidden Island,,,,,52\r\n"
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
                        [
                            6,
                            9,
                            ruby_text("Synthetic Coast"),
                            ruby_text("Synthetic Tunnel"),
                            None,
                            None,
                            None,
                            None,
                        ],
                        [
                            7,
                            10,
                            ruby_text("Synthetic Hidden Island"),
                            None,
                            None,
                            None,
                            None,
                            point_eight_switch,
                        ],
                        [
                            8,
                            10,
                            ruby_text("Untouched Hidden Island"),
                            None,
                            None,
                            None,
                            None,
                            52,
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


def prepare_point_project(
    base: Path,
    *,
    description: bool = False,
    description_field_count: int = 4,
    name_field_count: int = 3,
    point_eight_switch: int = 51,
):
    root = base / "game"
    project = base / "project"
    prepare_v21_game(
        root,
        point_validation=True,
        point_eight_switch=point_eight_switch,
    )
    extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
    rows = [dict(row) for row in extraction.rows]
    selected = next(
        row
        for row in rows
        if row["type"]
        == (
            "PBS v21.1 — Point.Description"
            if description
            else "PBS v21.1 — Point.Name"
        )
        and row["fichier"] == "PBS/town_map.txt"
        and row["pbs_field_count"]
        == (description_field_count if description else name_field_count)
    )
    selected["traduction_fr"] = selected["texte_source"] + (
        (
            " [TEST PFT v21.1 POINT DESCRIPTION 7]"
            if description_field_count == 7
            else " [TEST PFT v21.1 POINT DESCRIPTION]"
        )
        if description
        else (
            " [TEST PFT v21.1 POINT 8]"
            if name_field_count == 8
            else " [TEST PFT v21.1 POINT]"
        )
    )
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = (
        (
            "validation_synthetique_v21_1_point_description_7"
            if description_field_count == 7
            else "validation_synthetique_v21_1_point_description"
        )
        if description
        else (
            "validation_synthetique_v21_1_point_8"
            if name_field_count == 8
            else "validation_synthetique_v21_1_point"
        )
    )
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
                b"MegaMessage = 1\r\n"
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
                {"IntroMorning", "Body1", "BattleRequest"},
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

        self.assertEqual(7, len(point_rows))
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

    def test_v21_private_point_description_roundtrip_is_four_fields_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_desc_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_point_project(base, description=True)
            target = base / "candidate"
            reports = base / "reports"
            point_path = root / "PBS" / "town_map.txt"
            compiled_path = root / "Data" / "town_map.dat"
            original_payload = point_path.read_bytes()
            original_compiled = compiled_path.read_bytes()
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")

            plan = build_v21_1_point_description_validation_plan(root, csv_path)
            self.assertEqual(
                V21_1_POINT_DESCRIPTION_VALIDATION_SCOPE,
                plan.validation_scope,
            )
            self.assertEqual(1, plan.counts().get("applicable", 0))
            item = next(item for item in plan.items if item.decision == "applicable")
            self.assertEqual("PBS v21.1 — Point.Description", item.type)
            self.assertEqual("4", str(item.pbs_field_count))
            self.assertEqual("3", str(item.pbs_field_index))
            compiled_proof = json.loads(item.pbs_compiled_structure)
            self.assertEqual([0, "@point", 2, 3], compiled_proof["compiled_path"])
            self.assertEqual(
                [
                    "int",
                    "int",
                    "RubyString",
                    "RubyString",
                    "NoneType",
                    "NoneType",
                    "NoneType",
                    "NoneType",
                ],
                compiled_proof["point_types"],
            )

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
            self.assertEqual(
                expected_payload,
                (target / "PBS" / "town_map.txt").read_bytes(),
            )

            rebuilt = {
                row["id_stable"]: row
                for row in extract_pbs(
                    target / "PBS" / "town_map.txt",
                    "PBS/town_map.txt",
                )
            }[selected["id_stable"]]
            self.assertEqual(selected["traduction_fr"], rebuilt["texte_source"])
            self.assertEqual(selected["sous_index"], rebuilt["sous_index"])
            self.assertEqual(4, rebuilt["pbs_field_count"])
            self.assertEqual(3, rebuilt["pbs_field_index"])

            original_root = load_town_map_bytes(original_compiled)
            candidate_raw = (target / "Data" / "town_map.dat").read_bytes()
            candidate_root = load_town_map_bytes(candidate_raw)
            original_point = original_root[0].ivars["@point"][2]
            candidate_point = candidate_root[0].ivars["@point"][2]
            original_target = original_point[3]
            candidate_target = candidate_point[3]
            self.assertEqual(selected["texte_source"], original_target.text())
            self.assertEqual(selected["traduction_fr"], candidate_target.text())
            self.assertEqual(dumps(original_point[:3]), dumps(candidate_point[:3]))
            self.assertEqual([None, None, None, None], candidate_point[4:])
            self.assertEqual(original_point[4:], candidate_point[4:])
            self.assertEqual(
                graph_sha256(original_root, masked_string=original_target),
                graph_sha256(candidate_root, masked_string=candidate_target),
            )
            self.assertEqual(
                dumps(original_root[0].ivars["@point"][:2]),
                dumps(candidate_root[0].ivars["@point"][:2]),
            )
            self.assertEqual(
                selected["traduction_fr"],
                extract_compiled_point_text(
                    candidate_raw,
                    section="0",
                    occurrence=3,
                    field_index=3,
                    pbs_fields=[
                        "6",
                        "9",
                        "Synthetic Coast",
                        selected["traduction_fr"],
                    ],
                ),
            )

    def test_v21_private_point_description_seven_fields_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_desc7_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_point_project(
                base,
                description=True,
                description_field_count=7,
            )
            target = base / "candidate"
            reports = base / "reports"
            pbs_path = root / "PBS" / "town_map.txt"
            compiled_path = root / "Data" / "town_map.dat"
            original_pbs = pbs_path.read_bytes()
            original_compiled = compiled_path.read_bytes()
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")

            plan = build_v21_1_point_description_seven_fields_validation_plan(
                root,
                csv_path,
            )
            self.assertEqual(
                V21_1_POINT_DESCRIPTION_SEVEN_FIELDS_VALIDATION_SCOPE,
                plan.validation_scope,
            )
            self.assertEqual(1, plan.counts().get("applicable", 0))
            item = next(item for item in plan.items if item.decision == "applicable")
            self.assertEqual("PBS v21.1 — Point.Description", item.type)
            self.assertEqual("7", str(item.pbs_field_count))
            self.assertEqual("3", str(item.pbs_field_index))
            compiled_proof = json.loads(item.pbs_compiled_structure)
            self.assertEqual([0, "@point", 1, 3], compiled_proof["compiled_path"])
            self.assertEqual(
                [
                    "int",
                    "int",
                    "RubyString",
                    "RubyString",
                    "int",
                    "int",
                    "int",
                    "NoneType",
                ],
                compiled_proof["point_types"],
            )

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
            expected_pbs = original_pbs.replace(
                selected["texte_source"].encode("utf-8"),
                selected["traduction_fr"].encode("utf-8"),
                1,
            )
            self.assertEqual(
                expected_pbs,
                (target / "PBS" / "town_map.txt").read_bytes(),
            )

            rebuilt = {
                row["id_stable"]: row
                for row in extract_pbs(
                    target / "PBS" / "town_map.txt",
                    "PBS/town_map.txt",
                )
            }[selected["id_stable"]]
            self.assertEqual(selected["traduction_fr"], rebuilt["texte_source"])
            self.assertEqual(selected["sous_index"], rebuilt["sous_index"])
            self.assertEqual(7, rebuilt["pbs_field_count"])
            self.assertEqual(3, rebuilt["pbs_field_index"])

            original_root = load_town_map_bytes(original_compiled)
            candidate_raw = (target / "Data" / "town_map.dat").read_bytes()
            candidate_root = load_town_map_bytes(candidate_raw)
            original_point = original_root[0].ivars["@point"][1]
            candidate_point = candidate_root[0].ivars["@point"][1]
            original_target = original_point[3]
            candidate_target = candidate_point[3]
            self.assertEqual(selected["texte_source"], original_target.text())
            self.assertEqual(selected["traduction_fr"], candidate_target.text())
            self.assertEqual(dumps(original_point[:3]), dumps(candidate_point[:3]))
            self.assertEqual([1, 2, 3, None], candidate_point[4:])
            self.assertEqual(dumps(original_point[4:]), dumps(candidate_point[4:]))
            self.assertEqual(
                graph_sha256(original_root, masked_string=original_target),
                graph_sha256(candidate_root, masked_string=candidate_target),
            )
            self.assertEqual(
                dumps(original_root[0].ivars["@point"][0]),
                dumps(candidate_root[0].ivars["@point"][0]),
            )
            self.assertEqual(
                dumps(original_root[0].ivars["@point"][2]),
                dumps(candidate_root[0].ivars["@point"][2]),
            )
            self.assertEqual(
                selected["traduction_fr"],
                extract_compiled_point_text(
                    candidate_raw,
                    section="0",
                    occurrence=2,
                    field_index=3,
                    pbs_fields=[
                        "5",
                        "8",
                        "Untouched Town",
                        selected["traduction_fr"],
                        "1",
                        "2",
                        "3",
                    ],
                ),
            )

    def test_v21_point_description_seven_fields_refuses_pbs_changes(self) -> None:
        mutations = {
            "wrong field count": None,
            "removed field": (
                b"Untouched Description,1,2,3",
                b"Untouched Description,1,2",
            ),
            "additional field": (
                b"Untouched Description,1,2,3",
                b"Untouched Description,1,2,3,4",
            ),
            "permuted fields": (
                b"Untouched Description,1,2,3",
                b"Untouched Description,2,1,3",
            ),
            "numeric value changed": (
                b"Untouched Description,1,2,3",
                b"Untouched Description,1,9,3",
            ),
            "name changed": (b"Untouched Town", b"Changed Town"),
            "other description changed": (
                b"Synthetic Tunnel",
                b"Changed Other Description",
            ),
            "coordinates changed": (b"Point = 5,8,", b"Point = 9,8,"),
        }
        for mutation, replacement in mutations.items():
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_desc7_pbs_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    description=True,
                    description_field_count=7,
                )
                plan = build_v21_1_point_description_seven_fields_validation_plan(
                    root,
                    csv_path,
                )
                item = next(item for item in plan.items if item.decision == "applicable")
                if mutation == "wrong field count":
                    item.pbs_field_count = "8"
                else:
                    assert replacement is not None
                    old, new = replacement
                    pbs_path = root / "PBS" / "town_map.txt"
                    pbs_path.write_bytes(pbs_path.read_bytes().replace(old, new, 1))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|sources|source|inventaire|métadonnées|forme",
                ):
                    simulate_plan(plan)

    def test_v21_point_description_seven_fields_refuses_compiled_changes(self) -> None:
        for mutation in (
            "description type",
            "name changed",
            "coordinates changed",
            "numeric value changed",
            "numeric became nil",
            "nil became value",
            "other description changed",
            "marshal structure changed",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_desc7_dat_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    description=True,
                    description_field_count=7,
                )
                plan = build_v21_1_point_description_seven_fields_validation_plan(
                    root,
                    csv_path,
                )
                compiled_path = root / "Data" / "town_map.dat"
                compiled = load(compiled_path)
                target = compiled[0].ivars["@point"][1]
                if mutation == "description type":
                    target[3] = 3
                elif mutation == "name changed":
                    target[2] = ruby_text("Changed Town")
                elif mutation == "coordinates changed":
                    target[0] = 9
                elif mutation == "numeric value changed":
                    target[5] = 9
                elif mutation == "numeric became nil":
                    target[4] = None
                elif mutation == "nil became value":
                    target[7] = 4
                elif mutation == "other description changed":
                    compiled[0].ivars["@point"][2][3] = ruby_text(
                        "Changed Other Description"
                    )
                else:
                    del compiled[0].ivars["@flags"]
                compiled_path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|sources|source|inventaire|compilée|town_map",
                ):
                    simulate_plan(plan)

    def test_v21_point_description_seven_fields_refuses_proof_and_provenance_changes(
        self,
    ) -> None:
        for mutation in (
            "compiled proof",
            "provenance",
            "pbs after plan",
            "compiled after plan",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_desc7_guard_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    description=True,
                    description_field_count=7,
                )
                plan = build_v21_1_point_description_seven_fields_validation_plan(
                    root,
                    csv_path,
                )
                target = base / "candidate"
                if mutation == "compiled proof":
                    item = next(
                        item for item in plan.items if item.decision == "applicable"
                    )
                    proof = json.loads(item.pbs_compiled_structure)
                    proof["point_types"][4] = "NoneType"
                    item.pbs_compiled_structure = json.dumps(proof)
                    with self.assertRaisesRegex(
                        ReconstructionError,
                        "Point|compilée|preuve",
                    ):
                        simulate_plan(plan)
                    continue

                simulate_plan(plan)
                if mutation == "provenance":
                    plan.project_provenance_token = "altered-provenance"
                elif mutation == "pbs after plan":
                    path = root / "PBS" / "town_map.txt"
                    path.write_bytes(
                        path.read_bytes().replace(
                            b"Untouched Description",
                            b"Changed after planning",
                            1,
                        )
                    )
                else:
                    path = root / "Data" / "town_map.dat"
                    compiled = load(path)
                    compiled[0].ivars["@point"][1][5] = 9
                    path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "provenance|sources|source|inventaire|changé",
                ):
                    reconstruct_copy(plan, target, base / "reports")
                self.assertFalse(target.exists())

    def test_v21_private_point_name_eight_fields_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_name8_rt_") as temporary:
            base = Path(temporary)
            root, csv_path, selected = prepare_point_project(
                base,
                name_field_count=8,
            )
            target = base / "candidate"
            reports = base / "reports"
            pbs_path = root / "PBS" / "town_map.txt"
            compiled_path = root / "Data" / "town_map.dat"
            original_pbs = pbs_path.read_bytes()
            original_compiled = compiled_path.read_bytes()
            source_before = snapshot_tree(root)

            with self.assertRaisesRegex(ReconstructionError, "Reconstruction bloquée"):
                build_plan(root, csv_path, mode="accepted")

            plan = build_v21_1_point_name_eight_fields_validation_plan(
                root,
                csv_path,
            )
            self.assertEqual(
                V21_1_POINT_NAME_EIGHT_FIELDS_VALIDATION_SCOPE,
                plan.validation_scope,
            )
            self.assertEqual(1, plan.counts().get("applicable", 0))
            item = next(item for item in plan.items if item.decision == "applicable")
            self.assertEqual("PBS v21.1 — Point.Name", item.type)
            self.assertEqual("8", str(item.pbs_field_count))
            self.assertEqual("2", str(item.pbs_field_index))
            compiled_proof = json.loads(item.pbs_compiled_structure)
            self.assertEqual([0, "@point", 3, 2], compiled_proof["compiled_path"])
            self.assertEqual(
                [
                    "int",
                    "int",
                    "RubyString",
                    "NoneType",
                    "NoneType",
                    "NoneType",
                    "NoneType",
                    "int",
                ],
                compiled_proof["point_types"],
            )

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
            expected_pbs = original_pbs.replace(
                selected["texte_source"].encode("utf-8"),
                selected["traduction_fr"].encode("utf-8"),
                1,
            )
            self.assertEqual(
                expected_pbs,
                (target / "PBS" / "town_map.txt").read_bytes(),
            )

            rebuilt = {
                row["id_stable"]: row
                for row in extract_pbs(
                    target / "PBS" / "town_map.txt",
                    "PBS/town_map.txt",
                )
            }[selected["id_stable"]]
            self.assertEqual(selected["traduction_fr"], rebuilt["texte_source"])
            self.assertEqual(selected["sous_index"], rebuilt["sous_index"])
            self.assertEqual(8, rebuilt["pbs_field_count"])
            self.assertEqual(2, rebuilt["pbs_field_index"])

            original_root = load_town_map_bytes(original_compiled)
            candidate_raw = (target / "Data" / "town_map.dat").read_bytes()
            candidate_root = load_town_map_bytes(candidate_raw)
            original_point = original_root[0].ivars["@point"][3]
            candidate_point = candidate_root[0].ivars["@point"][3]
            original_target = original_point[2]
            candidate_target = candidate_point[2]
            self.assertEqual(selected["texte_source"], original_target.text())
            self.assertEqual(selected["traduction_fr"], candidate_target.text())
            self.assertEqual([None, None, None, None, 51], candidate_point[3:])
            self.assertEqual(dumps(original_point[3:]), dumps(candidate_point[3:]))
            self.assertEqual(
                graph_sha256(original_root, masked_string=original_target),
                graph_sha256(candidate_root, masked_string=candidate_target),
            )
            self.assertEqual(
                dumps(original_root[0].ivars["@point"][4]),
                dumps(candidate_root[0].ivars["@point"][4]),
            )
            self.assertEqual(
                selected["traduction_fr"],
                extract_compiled_point_text(
                    candidate_raw,
                    section="0",
                    occurrence=4,
                    field_index=2,
                    pbs_fields=[
                        "7",
                        "10",
                        selected["traduction_fr"],
                        "",
                        "",
                        "",
                        "",
                        "51",
                    ],
                ),
            )

    def test_v21_point_name_eight_fields_refuses_pbs_changes(self) -> None:
        mutations = {
            "wrong field count": None,
            "eight became seven": (
                b"Synthetic Hidden Island,,,,,51",
                b"Synthetic Hidden Island,,,,51",
            ),
            "additional ninth field": (
                b"Synthetic Hidden Island,,,,,51",
                b"Synthetic Hidden Island,,,,,,51",
            ),
            "permuted optional fields": (
                b"Synthetic Hidden Island,,,,,51",
                b"Synthetic Hidden Island,,,,51,",
            ),
            "eighth field changed": (b",,,,,51", b",,,,,53"),
            "coordinates changed": (b"Point = 7,10,", b"Point = 9,10,"),
            "description became present": (
                b"Synthetic Hidden Island,,,,,51",
                b"Synthetic Hidden Island,Unexpected description,,,,51",
            ),
            "destination became present": (
                b"Synthetic Hidden Island,,,,,51",
                b"Synthetic Hidden Island,,1,,,51",
            ),
            "other point name changed": (
                b"Untouched Hidden Island",
                b"Changed Hidden Island",
            ),
        }
        for mutation, replacement in mutations.items():
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_name8_pbs_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    name_field_count=8,
                )
                plan = build_v21_1_point_name_eight_fields_validation_plan(
                    root,
                    csv_path,
                )
                item = next(item for item in plan.items if item.decision == "applicable")
                if mutation == "wrong field count":
                    item.pbs_field_count = "7"
                else:
                    assert replacement is not None
                    old, new = replacement
                    pbs_path = root / "PBS" / "town_map.txt"
                    pbs_path.write_bytes(pbs_path.read_bytes().replace(old, new, 1))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|sources|source|inventaire|métadonnées|forme",
                ):
                    simulate_plan(plan)

        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_name7_scope_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_point_project(
                base,
                name_field_count=7,
            )
            with self.assertRaisesRegex(
                ReconstructionError,
                "Point|sous-champ|métadonnées",
            ):
                build_v21_1_point_name_eight_fields_validation_plan(root, csv_path)

        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_name8_switch0_") as temporary:
            base = Path(temporary)
            root, csv_path, _selected = prepare_point_project(
                base,
                name_field_count=8,
                point_eight_switch=0,
            )
            plan = build_v21_1_point_name_eight_fields_validation_plan(root, csv_path)
            with self.assertRaisesRegex(ReconstructionError, "switch.*positif"):
                simulate_plan(plan)

    def test_v21_point_name_eight_fields_refuses_compiled_changes(self) -> None:
        for mutation in (
            "switch type",
            "switch became nil",
            "name type",
            "coordinates changed",
            "description became present",
            "destination became present",
            "other point name changed",
            "marshal structure changed",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_name8_dat_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    name_field_count=8,
                )
                plan = build_v21_1_point_name_eight_fields_validation_plan(
                    root,
                    csv_path,
                )
                compiled_path = root / "Data" / "town_map.dat"
                compiled = load(compiled_path)
                target = compiled[0].ivars["@point"][3]
                if mutation == "switch type":
                    target[7] = ruby_text("51")
                elif mutation == "switch became nil":
                    target[7] = None
                elif mutation == "name type":
                    target[2] = 2
                elif mutation == "coordinates changed":
                    target[0] = 9
                elif mutation == "description became present":
                    target[3] = ruby_text("Unexpected description")
                elif mutation == "destination became present":
                    target[4] = 1
                elif mutation == "other point name changed":
                    compiled[0].ivars["@point"][4][2] = ruby_text(
                        "Changed Hidden Island"
                    )
                else:
                    del compiled[0].ivars["@flags"]
                compiled_path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|sources|source|inventaire|compilée|town_map",
                ):
                    simulate_plan(plan)

    def test_v21_point_name_eight_fields_refuses_proof_and_provenance_changes(
        self,
    ) -> None:
        for mutation in (
            "compiled proof",
            "compiled path",
            "provenance",
            "pbs after plan",
            "compiled after plan",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_name8_guard_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    name_field_count=8,
                )
                plan = build_v21_1_point_name_eight_fields_validation_plan(
                    root,
                    csv_path,
                )
                target = base / "candidate"
                if mutation in {"compiled proof", "compiled path"}:
                    item = next(
                        item for item in plan.items if item.decision == "applicable"
                    )
                    if mutation == "compiled proof":
                        proof = json.loads(item.pbs_compiled_structure)
                        proof["point_types"][7] = "NoneType"
                        item.pbs_compiled_structure = json.dumps(proof)
                    else:
                        item.pbs_compiled_path = json.dumps([0, "@point", 4, 2])
                    with self.assertRaisesRegex(
                        ReconstructionError,
                        "Point|compilée|preuve",
                    ):
                        simulate_plan(plan)
                    continue

                simulate_plan(plan)
                if mutation == "provenance":
                    plan.project_provenance_token = "altered-provenance"
                elif mutation == "pbs after plan":
                    path = root / "PBS" / "town_map.txt"
                    path.write_bytes(
                        path.read_bytes().replace(
                            b"Synthetic Hidden Island",
                            b"Changed after planning",
                            1,
                        )
                    )
                else:
                    path = root / "Data" / "town_map.dat"
                    compiled = load(path)
                    compiled[0].ivars["@point"][3][7] = 53
                    path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "provenance|sources|source|inventaire|changé",
                ):
                    reconstruct_copy(plan, target, base / "reports")
                self.assertFalse(target.exists())

    def test_v21_point_description_scope_refuses_other_point_forms(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_v21_pt_desc_form_") as temporary:
            base = Path(temporary)
            root = base / "game"
            project = base / "project"
            prepare_v21_game(root, point_validation=True)
            pbs_path = root / "PBS" / "town_map.txt"
            pbs_path.write_bytes(
                pbs_path.read_bytes().replace(
                    b"Synthetic Coast,Synthetic Tunnel\r\n",
                    b"Synthetic Coast,Synthetic Tunnel,4,5,6\r\n",
                )
            )
            compiled_path = root / "Data" / "town_map.dat"
            compiled = load(compiled_path)
            compiled[0].ivars["@point"][2][4:7] = [4, 5, 6]
            compiled_path.write_bytes(dumps(compiled))
            extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
            rows = [dict(row) for row in extraction.rows]
            selected = next(
                row
                for row in rows
                if row["type"] == "PBS v21.1 — Point.Description"
                and row["pbs_field_count"] == 7
            )
            selected["traduction_fr"] = "Translated synthetic description"
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
                "métadonnées|Point|quatre|occurrence",
            ):
                build_v21_1_point_description_validation_plan(root, csv_path)

    def test_v21_point_description_refuses_pbs_identity_and_shape_changes(self) -> None:
        mutations = {
            "wrong sub-index": None,
            "wrong field count": None,
            "description removed": (
                b"Synthetic Coast,Synthetic Tunnel",
                b"Synthetic Coast",
            ),
            "description appeared elsewhere": (
                b"Synthetic Route\r\n",
                b"Synthetic Route,Unexpected Description\r\n",
            ),
            "name changed": (b"Synthetic Coast", b"Changed Coast"),
            "coordinates changed": (b"Point = 6,9,", b"Point = 7,9,"),
            "numeric parameter changed": (
                b"Untouched Description,1,2,3",
                b"Untouched Description,9,2,3",
            ),
        }
        for mutation, replacement in mutations.items():
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_desc_pbs_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    description=True,
                )
                plan = build_v21_1_point_description_validation_plan(root, csv_path)
                item = next(item for item in plan.items if item.decision == "applicable")
                if mutation == "wrong sub-index":
                    item.sub_index = "2:field:3"
                elif mutation == "wrong field count":
                    item.pbs_field_count = "7"
                else:
                    assert replacement is not None
                    old, new = replacement
                    pbs_path = root / "PBS" / "town_map.txt"
                    pbs_path.write_bytes(pbs_path.read_bytes().replace(old, new, 1))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|sources|source|inventaire|occurrence|métadonnées",
                ):
                    simulate_plan(plan)

    def test_v21_point_description_refuses_compiled_value_type_and_structure_changes(self) -> None:
        for mutation in (
            "description absent",
            "description type",
            "name changed",
            "coordinates changed",
            "numeric parameter changed",
            "marshal structure changed",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_desc_dat_bad_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    description=True,
                )
                plan = build_v21_1_point_description_validation_plan(root, csv_path)
                compiled_path = root / "Data" / "town_map.dat"
                compiled = load(compiled_path)
                target = compiled[0].ivars["@point"][2]
                if mutation == "description absent":
                    target[3] = None
                elif mutation == "description type":
                    target[3] = 3
                elif mutation == "name changed":
                    target[2] = ruby_text("Changed Coast")
                elif mutation == "coordinates changed":
                    target[0] = 7
                elif mutation == "numeric parameter changed":
                    compiled[0].ivars["@point"][1][4] = 9
                else:
                    del compiled[0].ivars["@flags"]
                compiled_path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "Point|sources|source|inventaire|compilée|town_map",
                ):
                    simulate_plan(plan)

    def test_v21_point_description_refuses_provenance_and_post_plan_changes(self) -> None:
        for mutation in ("provenance", "pbs after plan", "compiled after plan"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                prefix="pft_v21_pt_desc_guard_"
            ) as temporary:
                base = Path(temporary)
                root, csv_path, _selected = prepare_point_project(
                    base,
                    description=True,
                )
                plan = build_v21_1_point_description_validation_plan(root, csv_path)
                simulate_plan(plan)
                target = base / "candidate"
                if mutation == "provenance":
                    plan.project_provenance_token = "altered-provenance"
                elif mutation == "pbs after plan":
                    path = root / "PBS" / "town_map.txt"
                    path.write_bytes(
                        path.read_bytes().replace(
                            b"Synthetic Tunnel",
                            b"Changed after planning",
                            1,
                        )
                    )
                else:
                    path = root / "Data" / "town_map.dat"
                    compiled = load(path)
                    compiled[0].ivars["@point"][2][3] = ruby_text(
                        "Changed after planning"
                    )
                    path.write_bytes(dumps(compiled))

                with self.assertRaisesRegex(
                    ReconstructionError,
                    "provenance|sources|source|inventaire|changé",
                ):
                    reconstruct_copy(plan, target, base / "reports")
                self.assertFalse(target.exists())

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
