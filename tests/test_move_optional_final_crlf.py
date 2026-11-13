import unittest
from essentials_move import parse_move_pbs,MoveIntegrityError

def make(last_crlf):
    text=("[TESTMOVE]\r\nName = Test Move\r\nType = NORMAL\r\n"
          "Category = Status\r\nAccuracy = 100\r\nTotalPP = 10\r\n"
          "Target = NearOther\r\nFunctionCode = None\r\n"
          "Description = Test description")
    return b"\xef\xbb\xbf"+(text+("\r\n" if last_crlf else "")).encode("utf-8")

class MoveOptionalLastCRLFTests(unittest.TestCase):
    def test_without_final_crlf_preserved(self):
        raw=make(False)
        doc=parse_move_pbs(raw)
        self.assertEqual("",doc.sections[0].description.newline)
        self.assertEqual(raw,b"\xef\xbb\xbf"+"".join(doc.content_lines).encode("utf-8"))

    def test_with_final_crlf_preserved(self):
        doc=parse_move_pbs(make(True))
        self.assertEqual("\r\n",doc.sections[0].description.newline)

    def test_lf_in_middle_refused(self):
        data=make(False).replace(b"Name = Test Move\r\n",b"Name = Test Move\n")
        with self.assertRaises(MoveIntegrityError):parse_move_pbs(data)

    def test_cr_in_middle_refused(self):
        data=make(False).replace(b"Name = Test Move\r\n",b"Name = Test Move\r")
        with self.assertRaises(MoveIntegrityError):parse_move_pbs(data)
