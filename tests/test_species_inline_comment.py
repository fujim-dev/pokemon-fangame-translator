"""Synthetic Pokedex comment handling: compiler strips inline PBS comments."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from adapters.pokemon_essentials import PokemonEssentialsAdapter
from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SpeciesIntegrityError,
    build_species_pokedex_proofs,
    parse_species_base_pbs,
    rebuild_species_pokedex_payloads,
)
from test_essentials_v21 import prepare_v21_game


class SpeciesInlineCommentTests(unittest.TestCase):
    def prepare(self, base):
        root = Path(base) / "game"
        prepare_v21_game(root, species_validation=True)
        pbs = root / SPECIES_PBS_FILE
        content = pbs.read_bytes()
        original = parse_species_base_pbs(content)
        source = original.base_sections[0].pokedex.source.encode("utf-8")
        needle = source + b"\r\n"
        self.assertEqual(1, content.count(needle))
        pbs.write_bytes(content.replace(needle, source + b" #2.\r\n"))
        return root

    def test_compiler_comment_preserved_but_not_extracted_for_translation(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_inline_comment_") as tmp:
            root = self.prepare(tmp)
            paths = [
                root / SPECIES_PBS_FILE, root / SPECIES_FORMS_PBS_FILE,
                root / COMPILED_SPECIES_FILE, root / SPECIES_MESSAGES_FILE,
            ]
            before = [p.read_bytes() for p in paths]
            validated = parse_species_base_pbs(before[0]).base_sections[0].pokedex
            self.assertNotIn("#2.", validated.source)
            self.assertEqual(" #2.", validated.trailing)
            result = PokemonEssentialsAdapter().extract_with_provenance(root)
            target = next(row for row in result.rows if
                row.get("fichier") == SPECIES_PBS_FILE
                and row.get("evenement_id") == "BULBASAUR"
                and row.get("type") == "PBS — Pokedex"
            )
            self.assertEqual(validated.source, target["texte_source"])
            proof = build_species_pokedex_proofs(*before)[("BULBASAUR", "Pokedex", 1)]
            self.assertEqual(validated.source, proof.source)
            translation = "Synthetic rewritten Pokédex description."
            rebuilt = rebuild_species_pokedex_payloads(
                *before,
                section="BULBASAUR", source=proof.source, translation=translation,
                pbs_structure=proof.pbs_structure,
                compiled_path=proof.compiled_path,
                compiled_structure=proof.compiled_structure,
                runtime_path=proof.runtime_path,
                runtime_structure=proof.runtime_structure,
            )
            self.assertIn(translation.encode("utf-8") + b" #2.\r\n", rebuilt[SPECIES_PBS_FILE])
            self.assertEqual(before, [p.read_bytes() for p in paths])

    def test_unrecognized_suffix_cannot_bypass_compiled_match(self):
        with tempfile.TemporaryDirectory(prefix="pft_species_bad_comment_") as tmp:
            root = self.prepare(tmp)
            p = root / SPECIES_PBS_FILE
            p.write_bytes(p.read_bytes().replace(b" #2.\r\n", b" #unknown.\r\n", 1))
            with self.assertRaises(SpeciesIntegrityError):
                build_species_pokedex_proofs(
                    *((root / name).read_bytes() for name in (
                        SPECIES_PBS_FILE, SPECIES_FORMS_PBS_FILE,
                        COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE,
                    ))
                )


if __name__ == "__main__":
    unittest.main()
