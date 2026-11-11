"""Species PBS regression: optional UTF-8 BOM must preserve all byte-level formats."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from essentials_species import (
    SPECIES_PBS_FILE,
    SPECIES_FORMS_PBS_FILE,
    COMPILED_SPECIES_FILE,
    SPECIES_MESSAGES_FILE,
    SpeciesIntegrityError,
    build_species_pokedex_proofs,
    parse_species_base_pbs,
    parse_species_forms_pbs,
    rebuild_species_pokedex_payloads,
)
from test_essentials_v21 import prepare_v21_game

BOM = b"\xef\xbb\xbf"


class SpeciesOptionalBomTests(unittest.TestCase):
    def prepare(self, base):
        root = Path(base) / "game"
        prepare_v21_game(root, species_validation=True)
        pbs_file = root / SPECIES_PBS_FILE
        forms_file = root / SPECIES_FORMS_PBS_FILE
        for path in (pbs_file, forms_file):
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(BOM))
            path.write_bytes(raw[len(BOM):])
        paths = (pbs_file, forms_file, root / COMPILED_SPECIES_FILE, root / SPECIES_MESSAGES_FILE)
        return paths

    def test_no_bom_proofs_preserve_format(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_no_bom_proofs_") as tmp:
            paths = self.prepare(tmp)
            original = [path.read_bytes() for path in paths]
            self.assertEqual(3, len(parse_species_base_pbs(original[0]).base_sections))
            self.assertEqual(2, len(parse_species_forms_pbs(original[1]).form_sections))
            proofs = build_species_pokedex_proofs(*original)
            self.assertEqual(3, len(proofs))
            metadata = json.loads(proofs[("BULBASAUR", "Pokedex", 1)].pbs_structure)
            self.assertEqual("none", metadata["bom"])
            self.assertEqual("none", metadata["forms_bom"])
            self.assertEqual("utf-8", metadata["encoding"])
            self.assertEqual(original, [path.read_bytes() for path in paths])

    def test_no_bom_reconstruction_only_changes_target_data(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_no_bom_rebuild_") as tmp:
            paths = self.prepare(tmp)
            original = [path.read_bytes() for path in paths]
            proof = build_species_pokedex_proofs(*original)[("BULBASAUR", "Pokedex", 1)]
            replacement = "Synthetic translated Pokédex description."
            rebuilt = rebuild_species_pokedex_payloads(
                *original,
                section="BULBASAUR",
                source=proof.source,
                translation=replacement,
                pbs_structure=proof.pbs_structure,
                compiled_path=proof.compiled_path,
                compiled_structure=proof.compiled_structure,
                runtime_path=proof.runtime_path,
                runtime_structure=proof.runtime_structure,
            )
            pbs_output = rebuilt[SPECIES_PBS_FILE]
            self.assertFalse(pbs_output.startswith(BOM))
            self.assertIn(replacement.encode("utf-8"), pbs_output)
            self.assertEqual(original, [path.read_bytes() for path in paths])

    def test_no_bom_still_rejects_mixed_line_endings(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_no_bom_lines_") as tmp:
            paths = self.prepare(tmp)
            malformed = paths[0].read_bytes().replace(b"\r\n", b"\n", 1)
            with self.assertRaises(SpeciesIntegrityError):
                parse_species_base_pbs(malformed)


if __name__ == "__main__":
    unittest.main()
