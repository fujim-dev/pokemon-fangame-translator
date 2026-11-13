"""Price omission follows Ruby GameData::Item default zero; no reconstruction enabled."""
import unittest
from essentials_item import parse_item_pbs,ItemIntegrityError

def item(*, price="", pocket="Pocket = 1\r\n"):
    return ("\ufeff[TESTITEM]\r\nName = Test item\r\nNamePlural = Test items\r\n"+
        pocket+price+"Description = A synthetic item.\r\n").encode("utf-8")

class MissingPriceDefaultTests(unittest.TestCase):
    def test_missing_price_defaults_to_zero_without_inserting_a_line(self):
        raw=item()
        parsed=parse_item_pbs(raw)
        self.assertEqual(0,parsed.sections[0].price)
        self.assertNotIn("Price",parsed.sections[0].assignment_order)
        self.assertEqual(raw,b"\xef\xbb\xbf"+"".join(parsed.content_lines).encode("utf-8"))
    def test_explicit_price_is_preserved(self):
        self.assertEqual(25,parse_item_pbs(item(price="Price = 25\r\n")).sections[0].price)
    def test_invalid_price_still_rejected(self):
        with self.assertRaises(ItemIntegrityError):
            parse_item_pbs(item(price="Price = -1\r\n"))
    def test_required_pocket_still_rejected(self):
        with self.assertRaises(ItemIntegrityError):
            parse_item_pbs(item(pocket=""))
if __name__=="__main__":unittest.main()
