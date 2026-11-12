"""Read-only v21.1 map-name provenance with RPG Maker XP MapInfos fallback.

Compiled map names absent from PBS are verified against MapInfos, never
invented as PBS assignments or exposed as editable translation rows.
"""
from __future__ import annotations
import hashlib,json
from essentials_map_metadata import (
    MAP_METADATA_PBS_FILE,COMPILED_MAP_METADATA_FILE,MAP_METADATA_MESSAGES_FILE,
    MAP_NAME_MESSAGES_INDEX,MAP_METADATA_CLASS,MAP_METADATA_IVARS,
    MapMetadataIntegrityError,MapMetadataEntryProof,
    parse_map_metadata_pbs,_load_marshal,_ruby_text
)
from ruby_marshal_reader import RubyObject,RubyString

MAP_INFOS_FILE="Data/MapInfos.rxdata"
READONLY_PBS_FORMAT="pft_v21_1_map_name_implicit_pbs_readonly_v1"
READONLY_COMPILED_FORMAT="pft_v21_1_map_name_implicit_compiled_readonly_v1"
READONLY_RUNTIME_FORMAT="pft_v21_1_map_name_implicit_runtime_readonly_v1"

def _sha(raw:bytes)->str:
    return hashlib.sha256(raw).hexdigest()

def _proof(d:dict)->str:
    return json.dumps(d,sort_keys=True,separators=(",",":"),ensure_ascii=True)

def _read_mapinfo_name(value:object)->str:
    if not isinstance(value,RubyObject) or value.class_name!="RPG::MapInfo":
        raise MapMetadataIntegrityError("MapInfos : classe RPG::MapInfo absente.")
    name=value.ivars.get("@name")
    if not isinstance(name,RubyString) or name.ivars not in ({},{"E":True}):
        raise MapMetadataIntegrityError("MapInfos : nom RGSS invalide.")
    try:
        text=name.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MapMetadataIntegrityError("MapInfos : nom illisible.") from exc
    if not text:
        raise MapMetadataIntegrityError("MapInfos : nom vide.")
    return text

def build_implicit_map_name_readonly_proofs(
    pbs_raw:bytes,compiled_raw:bytes,runtime_raw:bytes,mapinfos_raw:bytes
)->dict[tuple[str,str,int],MapMetadataEntryProof]:
    pbs=parse_map_metadata_pbs(pbs_raw,allow_implicit_names=True)
    compiled=_load_marshal(compiled_raw,COMPILED_MAP_METADATA_FILE)
    runtime=_load_marshal(runtime_raw,MAP_METADATA_MESSAGES_FILE)
    infos=_load_marshal(mapinfos_raw,MAP_INFOS_FILE)
    if not isinstance(compiled,dict) or not isinstance(infos,dict):
        raise MapMetadataIntegrityError("Racines compilées ou MapInfos invalides.")
    if not isinstance(runtime,list) or len(runtime)<=MAP_NAME_MESSAGES_INDEX or not isinstance(runtime[MAP_NAME_MESSAGES_INDEX],dict):
        raise MapMetadataIntegrityError("Banque MAP_NAMES invalide.")
    if len(compiled)!=len(pbs.sections) or list(compiled)!=[section.map_id for section in pbs.sections]:
        raise MapMetadataIntegrityError("Les identifiants de carte ne correspondent pas exactement au PBS.")
    if not any(section.name is None for section in pbs.sections):
        raise MapMetadataIntegrityError("Le chemin implicite exige au moins un Name absent.")
    resolved={}
    implicit_ids=[]
    for section in pbs.sections:
        obj=compiled[section.map_id]
        if not isinstance(obj,RubyObject) or obj.class_name!=MAP_METADATA_CLASS:
            raise MapMetadataIntegrityError("Type d'objet MapMetadata non conforme.")
        if tuple(obj.ivars)!=MAP_METADATA_IVARS:
            raise MapMetadataIntegrityError("Structure MapMetadata non conforme.")
        if obj.ivars["@id"]!=section.map_id or not isinstance(obj.ivars["@flags"],list):
            raise MapMetadataIntegrityError("Identité ou flags MapMetadata non conformes.")
        if _ruby_text(obj.ivars["@pbs_file_suffix"],"Suffixe PBS").text()!="":
            raise MapMetadataIntegrityError("Suffixe PBS MapMetadata inattendu.")
        compiled_name=_ruby_text(obj.ivars["@real_name"],"Nom de carte compilé").text()
        if section.name is None:
            info=infos.get(section.map_id)
            if info is None or compiled_name!=_read_mapinfo_name(info):
                raise MapMetadataIntegrityError("Nom de carte implicite non prouvé par MapInfos.")
            implicit_ids.append(section.map_id)
        elif compiled_name!=section.name.source:
            raise MapMetadataIntegrityError("Nom PBS distinct du nom compilé.")
        resolved[section.map_id]=compiled_name
    unique=set(resolved.values())
    message_bank=runtime[MAP_NAME_MESSAGES_INDEX]
    if len(message_bank)!=len(unique):
        raise MapMetadataIntegrityError("La banque MAP_NAMES ne couvre pas exactement les noms compilés.")
    by_text={}
    for pos,(key,val) in enumerate(message_bank.items()):
        k=_ruby_text(key,"Clé MAP_NAMES")
        v=_ruby_text(val,"Valeur MAP_NAMES")
        name=k.text()
        if name in by_text or name not in unique or v.text()!=name or key is val:
            raise MapMetadataIntegrityError("Une clé MAP_NAMES est absente, dupliquée ou incohérente.")
        by_text[name]=(pos,key,val)
    if set(by_text)!=unique:
        raise MapMetadataIntegrityError("Couverture MAP_NAMES incomplète.")
    counts={n:list(resolved.values()).count(n) for n in unique}
    result={}
    for section in pbs.sections:
        a=section.name
        if a is None:
            continue
        pos,k,v=by_text[a.source]
        identity=(section.identifier,"Name",1)
        common={"reconstruction_allowed":False,"mapinfos_file":MAP_INFOS_FILE,
                "mapinfos_sha256":_sha(mapinfos_raw),"implicit_map_count":len(implicit_ids)}
        pbs_proof={**common,"format":READONLY_PBS_FORMAT,"pbs_file":MAP_METADATA_PBS_FILE,
                   "file_sha256":_sha(pbs_raw),"section_index":a.section_index,"section":a.section,
                   "line_number":a.line_number,"line_sha256":_sha(pbs.content_lines[a.line_index].encode("utf-8")),
                   "source_sha256":_sha(a.source.encode("utf-8")),"source_usage_count":counts[a.source],
                   "key":"Name","key_occurrence":1,"newline":a.newline}
        compiled_proof={**common,"format":READONLY_COMPILED_FORMAT,"compiled_file":COMPILED_MAP_METADATA_FILE,
                        "file_sha256":_sha(compiled_raw),"root_size":len(compiled),
                        "map_id":section.map_id,"field":"@real_name",
                        "target_value_sha256":_sha(compiled[section.map_id].ivars["@real_name"].data)}
        runtime_proof={**common,"format":READONLY_RUNTIME_FORMAT,"runtime_file":MAP_METADATA_MESSAGES_FILE,
                       "file_sha256":_sha(runtime_raw),"message_type_index":MAP_NAME_MESSAGES_INDEX,
                       "bank_length":len(message_bank),"entry_index":pos,
                       "source_sha256":_sha(a.source.encode("utf-8")),
                       "key_sha256":_sha(k.data),"value_sha256":_sha(v.data)}
        result[identity]=MapMetadataEntryProof(
            source=a.source,pbs_structure=_proof(pbs_proof),
            compiled_path=_proof([section.map_id,"@real_name"]),
            compiled_structure=_proof(compiled_proof),
            runtime_path=_proof([MAP_NAME_MESSAGES_INDEX,"entry",pos]),
            runtime_structure=_proof(runtime_proof)
        )
    return result
