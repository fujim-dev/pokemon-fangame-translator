"""Map metadata: preserve the final line terminator or its absence."""
import unittest
from essentials_map_metadata import parse_map_metadata_pbs, MapMetadataIntegrityError

class MapMetadataFinalNewlineTests(unittest.TestCase):
    def test_missing_final_crlf_on_name_is_preserved(self):
        raw=b"\xef\xbb\xbf[001]\r\nName = Test Map"
        result=parse_map_metadata_pbs(raw)
        self.assertEqual("",result.sections[0].name.newline)
        self.assertEqual(raw,b"\xef\xbb\xbf"+"".join(result.content_lines).encode("utf-8"))

    def test_last_line_with_crlf_stays_crlf(self):
        raw=b"\xef\xbb\xbf[001]\r\nName = Test Map\r\n"
        result=parse_map_metadata_pbs(raw)
        self.assertEqual("\r\n",result.sections[0].name.newline)

    def test_lf_in_middle_is_refused(self):
        raw=b"\xef\xbb\xbf[001]\nName = Test Map\r\n"
        with self.assertRaises(MapMetadataIntegrityError):
            parse_map_metadata_pbs(raw)

    def test_cr_in_middle_is_refused(self):
        raw=b"\xef\xbb\xbf[001]\rName = Test Map"
        with self.assertRaises(MapMetadataIntegrityError):
            parse_map_metadata_pbs(raw)

if __name__=="__main__":unittest.main()
