"""Portable synthetic multi-PBS Item proof and regression tests (no game assets)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters import PokemonEssentialsAdapter
from essentials_item import ItemIntegrityError
from essentials_item_split_readonly import build_split_item_readonly_proofs
from ruby_marshal_reader import RubyObject, RubyString, load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def synthetic_sources():
    with tempfile.TemporaryDirectory(prefix="pft_item_split_synthetic_") as temp:
        root = Path(temp) / "synthetic_game"
        prepare_v21_game(root, item_validation=True)
        pbs = (root / "PBS/items.txt").read_bytes()
        compiled = load(root / "Data/items.dat")
        core = load(root / "Data/messages_core.dat")
    # An additional PBS file contributes one compiled Item, after the base file.
    original = compiled["ORANBERRY"]
    extra = RubyObject(original.class_name, dict(original.ivars))
    extra.ivars["@id"] = "RAIDBAIT"
    content = {
        "Name": "Synthetic Raid Bait",
        "NamePlural": "Synthetic Raid Baits",
        "PortionName": "Synthetic Raid portion",
        "PortionNamePlural": "Synthetic Raid portions",
        "Description": "A synthetic  double-spaced description.",
    }
    for field, attr in (
        ("Name", "@real_name"),
        ("NamePlural", "@real_name_plural"),
        ("PortionName", "@real_portion_name"),
        ("PortionNamePlural", "@real_portion_name_plural"),
        ("Description", "@real_description"),
    ):
        extra.ivars[attr] = ruby_text(content[field])
    extra.ivars["@pbs_file_suffix"] = ruby_text("raid_bait")
    compiled["RAIDBAIT"] = extra
    pbs_extra = (
        "\ufeff[RAIDBAIT]\r\n"
        + "".join(f"{field} = {content[field]}\r\n" for field in (
            "Name", "NamePlural", "PortionName", "PortionNamePlural"
        ))
        + "Pocket = 5\r\nPrice = 20\r\nBPPrice = 2\r\n"
        + "FieldUse = OnPokemon\r\nBattleUse = OnPokemon\r\n"
        + "Flags = Berry,Fling_10\r\nConsumable = false\r\n"
        + "Description = " + content["Description"] + "\r\n"
    ).encode("utf-8")
    # Move one real base key to the game bank, and place the supplemental
    # Item in game too, keeping the historical core alias untouched.
    game = [{} for _ in range(len(core))]
    old = next(k for k in core[7] if k.text() == "Synthetic Potion")
    game[7][old] = core[7].pop(old)
    fields = {"Name": 7, "NamePlural": 8, "PortionName": 28,
              "PortionNamePlural": 29, "Description": 9}
    for field, index in fields.items():
        full = content[field]
        normalized = full.replace("  ", " ")
        game[index][ruby_text(normalized)] = ruby_text(full)
    return (pbs, pbs_extra, dumps(compiled), dumps(core), dumps(game))


class ItemSplitReadOnlySyntheticTests(unittest.TestCase):
    def test_positive_provenance_and_normalized_key(self):
        result = build_split_item_readonly_proofs(*synthetic_sources())
        self.assertEqual(21, len(result))
        self.assertEqual(5, sum(
            v.pbs_file == "PBS/items_raid_bait.txt" for v in result.values()
        ))
        desc = result[("RAIDBAIT", "Description", 1)]
        metadata = json.loads(desc.proof.runtime_structure)
        self.assertTrue(metadata["key_normalization_applied"])
        self.assertFalse(metadata["reconstruction_allowed"])
        self.assertEqual("Data/messages_game.dat", desc.runtime_file)
        self.assertEqual("Data/messages_game.dat", result[("POTION", "Name", 1)].runtime_file)

    def test_bad_supplemental_suffix_rejected(self):
        sources = list(synthetic_sources())
        compiled = load_from_bytes(sources[2])
        compiled["RAIDBAIT"].ivars["@pbs_file_suffix"] = ruby_text("wrong")
        sources[2] = dumps(compiled)
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(*sources)

    def test_tampered_game_value_rejected(self):
        sources = list(synthetic_sources())
        game = load_from_bytes(sources[4])
        key = next(k for k in game[9] if k.text().startswith("A synthetic"))
        game[9][key] = ruby_text("A synthetic double-spaced description.")
        sources[4] = dumps(game)
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(*sources)

    def test_extra_game_key_rejected(self):
        sources = list(synthetic_sources())
        game = load_from_bytes(sources[4])
        game[7][ruby_text("Unexpected synthetic alias")] = ruby_text("Unexpected synthetic alias")
        sources[4] = dumps(game)
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(*sources)

    def test_core_game_key_collision_rejected(self):
        sources = list(synthetic_sources())
        core = load_from_bytes(sources[3])
        game = load_from_bytes(sources[4])
        key = next(iter(game[7]))
        core[7][ruby_text(key.text())] = ruby_text(game[7][key].text())
        sources[3] = dumps(core)
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(*sources)

    def test_unmodified_baseline_is_not_sufficient_without_supplement(self):
        sources = list(synthetic_sources())
        sources[1] = b"\xef\xbb\xbf[OTHER]\r\nName = Missing Item\r\n"
        with self.assertRaises(ItemIntegrityError):
            build_split_item_readonly_proofs(*sources)

    def test_adapter_binds_all_five_supplemental_rows(self):
        with tempfile.TemporaryDirectory(prefix="pft_item_split_adapter_") as temporary:
            root = Path(temporary) / "synthetic_game"
            prepare_v21_game(root, item_validation=True)
            payloads = synthetic_sources()
            for name, raw in zip(
                ("PBS/items.txt", "PBS/items_raid_bait.txt", "Data/items.dat",
                 "Data/messages_core.dat", "Data/messages_game.dat"), payloads
            ):
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
            extraction = PokemonEssentialsAdapter().extract_with_provenance(root)
            item_rows = [
                row for row in extraction.rows
                if row.get("fichier") in {"PBS/items.txt", "PBS/items_raid_bait.txt"}
                and row.get("commande") in {
                    "Name", "NamePlural", "PortionName", "PortionNamePlural", "Description"
                }
            ]
            self.assertFalse(extraction.errors)
            self.assertEqual(21, len(item_rows))
            self.assertEqual(5, sum(
                row["fichier"] == "PBS/items_raid_bait.txt" for row in item_rows
            ))
            for row in item_rows:
                proof = json.loads(row["pbs_runtime_structure"])
                self.assertFalse(proof["reconstruction_allowed"])
                self.assertEqual("pft_v21_1_items_multi_pbs_split_readonly_v1", proof["format"])
                self.assertIn(row["pbs_runtime_file"], {
                    "Data/messages_core.dat", "Data/messages_game.dat"
                })
                self.assertEqual(row["fichier"], proof["pbs_file"])


def load_from_bytes(raw):
    from ruby_marshal_reader import MarshalReader
    reader = MarshalReader(raw)
    reader.pos = 2
    return reader.read_object()


if __name__ == "__main__":
    unittest.main()
