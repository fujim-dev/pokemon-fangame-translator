"""Ability PBS: the final line may have no CRLF, but internal lines must."""
import unittest
from essentials_ability import AbilityIntegrityError, parse_ability_pbs

BOM = b"\xef\xbb\xbf"
HEADER = b"[SYNTHETIC]\r\nName = Synthetic Ability\r\n"


class AbilityFinalNewlineTests(unittest.TestCase):
    def test_accepts_final_description_without_crlf(self):
        parsed = parse_ability_pbs(BOM + HEADER + b"Description = Synthetic description")
        self.assertEqual("", parsed.sections[0].description.newline)

    def test_accepts_final_description_with_crlf(self):
        parsed = parse_ability_pbs(BOM + HEADER + b"Description = Synthetic description\r\n")
        self.assertEqual("\r\n", parsed.sections[0].description.newline)

    def test_refuses_internal_lf(self):
        with self.assertRaises(AbilityIntegrityError):
            parse_ability_pbs(BOM + b"[SYNTHETIC]\nName = Synthetic Ability\r\nDescription = Synthetic description")

    def test_refuses_missing_required_description(self):
        with self.assertRaises(AbilityIntegrityError):
            parse_ability_pbs(BOM + HEADER)


if __name__ == "__main__":
    unittest.main()
