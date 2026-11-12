"""Strict synthetic tests: implicit Species Category is not a translation target."""
import unittest
from essentials_species_category import parse_species_category_base_pbs, _compiled_entry
from essentials_species import SpeciesIntegrityError, SPECIES_IVARS, SPECIES_CLASS
from ruby_marshal_reader import RubyObject, RubyString

class SpeciesCategoryOptionalDefaultTests(unittest.TestCase):
    def test_missing_category_remains_absent(self):
        raw = b"[TESTMON]\r\nName = Testmon\r\nPokedex = Example\r\n"
        result = parse_species_category_base_pbs(raw)
        self.assertEqual(1, len(result.base_sections))
        self.assertIsNone(result.base_sections[0].category)
        self.assertEqual(raw, b"".join(x.encode("utf-8") for x in result.content_lines))

    def test_missing_name_still_rejected(self):
        raw = b"[TESTMON]\r\nPokedex = Example\r\n"
        with self.assertRaises(SpeciesIntegrityError):
            parse_species_category_base_pbs(raw)

    def test_compiled_category_does_not_silently_accept_other_value(self):
        ivars = dict.fromkeys(SPECIES_IVARS)
        ivars.update({"@id": "TESTMON", "@species": "TESTMON", "@form": 0,
            "@real_name": RubyString(b"Testmon", {"E": True}),
            "@real_category": RubyString(b"NotDefault", {"E": True}),
            "@pbs_file_suffix": RubyString(b"", {"E": True})})
        obj = RubyObject(SPECIES_CLASS, ivars)
        with self.assertRaises(SpeciesIntegrityError):
            _compiled_entry("TESTMON", obj, expected_key="TESTMON",
                species="TESTMON", form=0, expected_name="Testmon",
                expected_category="???")

if __name__ == "__main__":
    unittest.main()
