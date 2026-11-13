"""Régression : virgule terminale vide dans un Point PBS Essentials v21.1."""
import unittest

from essentials_town_map import TownMapIntegrityError, _expected_point


class TownMapTrailingEmptyFieldTests(unittest.TestCase):
    def test_ignores_only_a_ninth_empty_field(self):
        fields = ["4", "7", "Synthetic Place", "", "78", "15", "12", "", ""]
        self.assertEqual(
            [4, 7, "Synthetic Place", None, 78, 15, 12, None],
            _expected_point(fields),
        )

    def test_rejects_ninth_field_with_data(self):
        fields = ["4", "7", "Synthetic Place", "", "78", "15", "12", "", "99"]
        with self.assertRaisesRegex(TownMapIntegrityError, "Nombre de sous-champs"):
            _expected_point(fields)

    def test_rejects_tenth_field_even_if_empty(self):
        fields = ["4", "7", "Synthetic Place", "", "78", "15", "12", "", "", ""]
        with self.assertRaisesRegex(TownMapIntegrityError, "Nombre de sous-champs"):
            _expected_point(fields)

    def test_preserves_eight_field_point(self):
        fields = ["4", "7", "Synthetic Place", "", "78", "15", "12", ""]
        self.assertEqual(
            [4, 7, "Synthetic Place", None, 78, 15, 12, None],
            _expected_point(fields),
        )


if __name__ == "__main__":
    unittest.main()
