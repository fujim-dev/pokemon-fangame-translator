"""Synthetic read-only proofs of split Move names and descriptions."""
import json,unittest
from essentials_move import MOVE_IVARS,MoveIntegrityError
from essentials_move_split import build_split_move_readonly_proofs
from ruby_marshal_reader import RubyString,RubyObject
from ruby_marshal_writer import dumps

def t(s):
    return RubyString(s.encode("utf-8"),{"E":True})

def fixtures():
    def move(id,name,desc,target="NearOther"):
        ivars=dict.fromkeys(MOVE_IVARS)
        ivars.update({"@id":id,"@real_name":t(name),"@type":"NORMAL",
            "@category":2,"@power":0,"@accuracy":100,"@total_pp":10,
            "@target":target,"@priority":0,"@function_code":t("None"),
            "@flags":[],"@effect_chance":0,"@real_description":t(desc),
            "@pbs_file_suffix":t("")})
        return RubyObject("GameData::Move",ivars)
    compiled={"ALPHA":move("ALPHA","Alpha","Alpha Desc"),
              "BETA":move("BETA","Beta","Beta Desc")}
    pbs=b"\xef\xbb\xbf"+(
        "[ALPHA]\r\nName = Alpha\r\nType = NORMAL\r\nCategory = Status\r\n"
        "Accuracy = 100\r\nTotalPP = 10\r\nTarget = NearOther\r\n"
        "FunctionCode = None\r\nDescription = Alpha Desc\r\n"
        "[BETA]\r\nName = Beta\r\nType = NORMAL\r\nCategory = Status\r\n"
        "Accuracy = 100\r\nTotalPP = 10\r\nTarget = NearOther\r\n"
        "FunctionCode = None\r\nDescription = Beta Desc").encode("utf-8")
    core=[{} for _ in range(7)]
    game=[{} for _ in range(7)]
    core[5]={t("Alpha"):t("Alpha"),t("Historical"):t("Historical")}
    core[6]={t("Alpha Desc"):t("Alpha Desc")}
    game[5]={t("Beta"):t("Beta")}
    game[6]={t("Beta Desc"):t("Beta Desc")}
    return pbs,compiled,core,game

def prove(p,c,core,game):
    return build_split_move_readonly_proofs(p,dumps(c),dumps(core),dumps(game))

class SplitMoveReadonlyTests(unittest.TestCase):
    def test_exact_core_game_provenance_and_non_reconstructable(self):
        p,c,core,game=fixtures()
        result=prove(p,c,core,game)
        self.assertEqual(4,len(result))
        self.assertEqual("Data/messages_core.dat",result[("ALPHA","Name",1)].runtime_file)
        self.assertEqual("Data/messages_game.dat",result[("BETA","Description",1)].runtime_file)
        for bound in result.values():
            self.assertFalse(json.loads(bound.proof.runtime_structure)["reconstruction_allowed"])
            self.assertEqual([],json.loads(bound.proof.compiled_structure)["technical_mismatches"])

    def test_target_drift_is_reported_without_writing(self):
        p,c,core,game=fixtures()
        c["ALPHA"].ivars["@target"]="User"
        result=prove(p,c,core,game)
        details=json.loads(result[("ALPHA","Name",1)].proof.compiled_structure)
        self.assertEqual(["Target"],details["technical_mismatches"])
        self.assertFalse(details["reconstruction_allowed"])

    def test_bad_compiled_name_refused(self):
        p,c,core,game=fixtures()
        c["BETA"].ivars["@real_name"]=t("Not Beta")
        with self.assertRaises(MoveIntegrityError):prove(p,c,core,game)

    def test_bad_compiled_category_refused(self):
        p,c,core,game=fixtures()
        c["ALPHA"].ivars["@category"]=1
        with self.assertRaises(MoveIntegrityError):prove(p,c,core,game)

    def test_bad_message_value_refused(self):
        p,c,core,game=fixtures()
        key=next(iter(game[5]))
        game[5][key]=t("Incorrect")
        with self.assertRaises(MoveIntegrityError):prove(p,c,core,game)

    def test_missing_game_bank_refused(self):
        p,c,core,game=fixtures()
        game[5]={}
        with self.assertRaises(MoveIntegrityError):prove(p,c,core,game)

    def test_extra_game_key_refused(self):
        p,c,core,game=fixtures()
        game[6][t("Unexpected")]=t("Unexpected")
        with self.assertRaises(MoveIntegrityError):prove(p,c,core,game)

if __name__=="__main__":
    unittest.main()
