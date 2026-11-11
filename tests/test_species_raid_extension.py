"""Strict synthetic regression for a trailing raid extension in Essentials Species."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_IVARS,
    SPECIES_RAID_EXTENSION_IVARS,
    SpeciesIntegrityError,
    build_species_pokedex_proofs,
    rebuild_species_pokedex_payloads,
)
from ruby_marshal_reader import load
from ruby_marshal_writer import dumps
from test_essentials_v21 import prepare_v21_game


class SpeciesRaidExtensionTests(unittest.TestCase):
    def prepare(self, root: Path):
        prepare_v21_game(root, species_validation=True)
        path = root / COMPILED_SPECIES_FILE
        objects = load(path)
        for entry in objects.values():
            entry.ivars["@raid_ranks"] = [2, 4]
            entry.ivars["@raid_style"] = []
        path.write_bytes(dumps(objects))
        return tuple((root / name).read_bytes() for name in (
            SPECIES_PBS_FILE, SPECIES_FORMS_PBS_FILE,
            COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE,
        ))

    def test_exact_raid_extension_proves_and_rebuilds_without_changing_raid(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_raid_rt_") as tmp:
            root = Path(tmp) / "game"
            parts = self.prepare(root)
            p = build_species_pokedex_proofs(*parts)[("BULBASAUR", "Pokedex", 1)]
            self.assertEqual(
                list(SPECIES_IVARS + SPECIES_RAID_EXTENSION_IVARS),
                json.loads(p.compiled_structure)["section_ivars"],
            )
            outputs = rebuild_species_pokedex_payloads(
                *parts, section="BULBASAUR", source=p.source,
                translation="Synthetic translated ability lore.",
                pbs_structure=p.pbs_structure,
                compiled_path=p.compiled_path,
                compiled_structure=p.compiled_structure,
                runtime_path=p.runtime_path,
                runtime_structure=p.runtime_structure,
            )
            before = load(root / COMPILED_SPECIES_FILE)
            import tempfile as tmpmodule
            with tmpmodule.TemporaryDirectory(prefix="pft_raid_candidate_") as cand:
                path = Path(cand) / "species.dat"
                path.write_bytes(outputs[COMPILED_SPECIES_FILE])
                after = load(path)
            self.assertEqual(list(before), list(after))
            for key in before:
                self.assertEqual(
                    dumps(before[key].ivars["@raid_ranks"]),
                    dumps(after[key].ivars["@raid_ranks"]),
                )
                self.assertEqual(
                    dumps(before[key].ivars["@raid_style"]),
                    dumps(after[key].ivars["@raid_style"]),
                )

    def test_extra_unknown_field_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_raid_unknown_") as tmp:
            root = Path(tmp) / "game"
            self.prepare(root)
            path = root / COMPILED_SPECIES_FILE
            objects = load(path)
            next(iter(objects.values())).ivars["@unknown_extra"] = []
            path.write_bytes(dumps(objects))
            with self.assertRaises(SpeciesIntegrityError):
                build_species_pokedex_proofs(
                    *((root / name).read_bytes() for name in (
                        SPECIES_PBS_FILE, SPECIES_FORMS_PBS_FILE,
                        COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE,
                    ))
                )

    def test_wrong_raid_field_type_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_raid_wrongtype_") as tmp:
            root = Path(tmp) / "game"
            self.prepare(root)
            path = root / COMPILED_SPECIES_FILE
            objects = load(path)
            next(iter(objects.values())).ivars["@raid_style"] = 0
            path.write_bytes(dumps(objects))
            with self.assertRaises(SpeciesIntegrityError):
                build_species_pokedex_proofs(
                    *((root / name).read_bytes() for name in (
                        SPECIES_PBS_FILE, SPECIES_FORMS_PBS_FILE,
                        COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE,
                    ))
                )


if __name__ == "__main__":
    unittest.main()
