"""Synthetic map names prove absent PBS Name only via RPG::MapInfo."""
import unittest
from essentials_map_metadata import MAP_METADATA_IVARS,MapMetadataIntegrityError,rebuild_map_metadata_name_payloads
from essentials_map_metadata_implicit import build_implicit_map_name_readonly_proofs
from ruby_marshal_reader import RubyString,RubyObject
from ruby_marshal_writer import dumps

def text(s,metadata=True):
    return RubyString(s.encode("utf-8"),{"E":True} if metadata else {})

def case():
    pbs=b"\xef\xbb\xbf[001]\r\nName = First\r\n[002]\r\nOutdoor = true"
    def entry(i,name):
        ivars=dict.fromkeys(MAP_METADATA_IVARS)
        ivars["@id"]=i
        ivars["@real_name"]=text(name)
        ivars["@flags"]=[]
        ivars["@pbs_file_suffix"]=text("")
        return RubyObject("GameData::MapMetadata",ivars)
    compiled={1:entry(1,"First"),2:entry(2,"Inherited")}
    runtime=[{} for _ in range(22)]
    runtime[21]={text(n):text(n) for n in ("First","Inherited")}
    infos={2:RubyObject("RPG::MapInfo",{"@name":text("Inherited",False)})}
    return pbs,compiled,runtime,infos

class ImplicitMapNameReadOnlyTests(unittest.TestCase):
    def test_exact_proofs_exclude_implicit_name(self):
        p,c,r,i=case()
        result=build_implicit_map_name_readonly_proofs(p,dumps(c),dumps(r),dumps(i))
        self.assertEqual({("001","Name",1)},set(result))
        proof=result[("001","Name",1)]
        self.assertEqual("First",proof.source)
        self.assertIn('"reconstruction_allowed":false',proof.runtime_structure)
        with self.assertRaises(MapMetadataIntegrityError):
            rebuild_map_metadata_name_payloads(
                p,dumps(c),dumps(r),section="001",source="First",
                translation="Changed",pbs_structure=proof.pbs_structure,
                compiled_path=proof.compiled_path,compiled_structure=proof.compiled_structure,
                runtime_path=proof.runtime_path,runtime_structure=proof.runtime_structure)

    def test_wrong_mapinfos_name_rejected(self):
        p,c,r,i=case()
        i[2].ivars["@name"]=text("Wrong",False)
        with self.assertRaises(MapMetadataIntegrityError):
            build_implicit_map_name_readonly_proofs(p,dumps(c),dumps(r),dumps(i))

    def test_missing_mapinfos_entry_rejected(self):
        p,c,r,i=case()
        with self.assertRaises(MapMetadataIntegrityError):
            build_implicit_map_name_readonly_proofs(p,dumps(c),dumps(r),dumps({}))

    def test_wrong_compiled_implicit_name_rejected(self):
        p,c,r,i=case()
        c[2].ivars["@real_name"]=text("Wrong")
        with self.assertRaises(MapMetadataIntegrityError):
            build_implicit_map_name_readonly_proofs(p,dumps(c),dumps(r),dumps(i))

    def test_altered_runtime_name_rejected(self):
        p,c,r,i=case()
        key=next(iter(r[21]))
        r[21][key]=text("Altered")
        with self.assertRaises(MapMetadataIntegrityError):
            build_implicit_map_name_readonly_proofs(p,dumps(c),dumps(r),dumps(i))

if __name__=="__main__":
    unittest.main()
