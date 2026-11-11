"""Trainer PBS: preserve a final line without CRLF, reject mixed line endings."""
import unittest
from essentials_trainer import TrainerIntegrityError, parse_trainer_pbs

BOM = b"\xef\xbb\xbf"
PREFIX = b"[YOUNGSTER,Synthetic]\r\nLoseText = Synthetic defeat\r\n"


class TrainerFinalNewlineTests(unittest.TestCase):
    def test_accepts_final_non_lose_assignment_without_crlf(self):
        data = BOM + PREFIX + b"Pokemon = SYNTHETIC,5"
        result = parse_trainer_pbs(data)
        self.assertEqual(1, len(result.sections))
        self.assertEqual("\r\n", result.sections[0].lose_text.newline)

    def test_accepts_final_lose_text_without_crlf(self):
        data = BOM + b"[YOUNGSTER,Synthetic]\r\nLoseText = Synthetic defeat"
        result = parse_trainer_pbs(data)
        self.assertEqual("", result.sections[0].lose_text.newline)

    def test_refuses_internal_missing_crlf(self):
        data = BOM + b"[YOUNGSTER,Synthetic]\r\nLoseText = Synthetic defeat\nPokemon = SYNTHETIC,5"
        with self.assertRaises(TrainerIntegrityError):
            parse_trainer_pbs(data)

    def test_preserves_existing_final_crlf(self):
        result = parse_trainer_pbs(BOM + PREFIX)
        self.assertEqual("\r\n", result.sections[0].lose_text.newline)


if __name__ == "__main__":
    unittest.main()
