"""Read-only source-key provenance for Essentials v21.1 Move text across two banks.

Technical PBS/compiled drift is accepted *only* for Target and Accuracy after
all other fields pass the existing strict validator in a disposable in-memory
object. This route NEVER permits reconstruction or silently repairs game data.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib,json,re
from collections import Counter
from essentials_phone import graph_sha256
from essentials_move import (
    MOVE_PBS_FILE,COMPILED_MOVE_FILE,MOVE_MESSAGES_FILE,
    MOVE_NAME_MESSAGES_INDEX,MOVE_DESCRIPTION_MESSAGES_INDEX,
    MOVE_TEXT_FIELDS,MOVE_CLASS,MOVE_IVARS,
    MoveIntegrityError,MoveEntryProof,parse_move_pbs,
    _load_marshal,_ruby_text,_validate_compiled_entry
)
from ruby_marshal_reader import RubyObject,RubyString

GAME_MESSAGES_FILE="Data/messages_game.dat"
FORMAT="pft_v21_1_move_split_readonly_v1"

@dataclass(frozen=True)
class SplitMoveReadOnlyProof:
    proof:MoveEntryProof
    runtime_file:str
    runtime_sha256:str

def sha(raw:bytes)->str:
    return hashlib.sha256(raw).hexdigest()

def j(value)->str:
    return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(",",":"))

def _validated_move(key,section,obj):
    if not isinstance(obj,RubyObject) or obj.class_name!=MOVE_CLASS or tuple(obj.ivars)!=MOVE_IVARS:
        raise MoveIntegrityError("Structure de Move inattendue.")
    mismatches=[]
    target=obj.ivars["@target"]
    accuracy=obj.ivars["@accuracy"]
    if not isinstance(target,str) or re.fullmatch(r"[A-Za-z0-9_]+",target) is None:
        raise MoveIntegrityError("Target compilé non canonique.")
    if type(accuracy) is not int or not 0<=accuracy<=100:
        raise MoveIntegrityError("Accuracy compilée non canonique.")
    if target!=section.target:mismatches.append("Target")
    if accuracy!=section.accuracy:mismatches.append("Accuracy")
    if mismatches:
        # Copy ONLY the ivar mapping for a disposable validation object.
        check=RubyObject(obj.class_name,dict(obj.ivars))
        check.ivars["@target"]=section.target
        check.ivars["@accuracy"]=section.accuracy
    else:
        check=obj
    # Existing strict validator checks every other identity, flag, type,
    # damage/category numeric value, text and PBS suffix.
    _validate_compiled_entry(key,section,check)
    return tuple(mismatches)

def build_split_move_readonly_proofs(pbs_raw:bytes,compiled_raw:bytes,
        core_raw:bytes,game_raw:bytes)->dict[tuple[str,str,int],SplitMoveReadOnlyProof]:
    pbs=parse_move_pbs(pbs_raw)
    compiled=_load_marshal(compiled_raw,COMPILED_MOVE_FILE)
    core=_load_marshal(core_raw,MOVE_MESSAGES_FILE)
    game=_load_marshal(game_raw,GAME_MESSAGES_FILE)
    if not isinstance(compiled,dict) or len(compiled)!=len(pbs.sections):
        raise MoveIntegrityError("La racine moves.dat ne correspond pas au PBS.")
    if list(compiled)!=[s.identifier for s in pbs.sections]:
        raise MoveIntegrityError("Les identifiants ou l'ordre compilé des Moves ont changé.")
    if not isinstance(core,list) or not isinstance(game,list):
        raise MoveIntegrityError("Racines des banques Move invalides.")
    technical_by_id={}
    for section,(key,obj) in zip(pbs.sections,compiled.items()):
        technical_by_id[section.identifier]=_validated_move(key,section,obj)
    bank_sources={}
    origins={}
    extras={}
    for field,(ivar,index) in MOVE_TEXT_FIELDS.items():
        if index>=len(core) or index>=len(game) or not isinstance(core[index],dict) or not isinstance(game[index],dict):
            raise MoveIntegrityError("Index de banque Move non canonique.")
        if not game[index]:
            raise MoveIntegrityError("Répartition Move core/game absente.")
        required_sources=[section.name.source if field=="Name" else section.description.source for section in pbs.sections]
        distinct=list(dict.fromkeys(required_sources))
        required=set(distinct)
        bank_sources[field]=required_sources
        by_source={}
        extras_core=0
        for label,raw,root in ((MOVE_MESSAGES_FILE,core_raw,core),(GAME_MESSAGES_FILE,game_raw,game)):
            for pos,(key,val) in enumerate(root[index].items()):
                k=_ruby_text(key,"Clé Move")
                v=_ruby_text(val,"Valeur Move")
                name=k.text()
                if name in by_source or k is v or v.text()!=name:
                    raise MoveIntegrityError("Clé dupliquée ou valeur Move incohérente.")
                if name not in required:
                    if label==GAME_MESSAGES_FILE:
                        raise MoveIntegrityError("Clé Move inattendue dans la banque du jeu.")
                    extras_core+=1
                    continue
                by_source[name]=(label,raw,root,pos,k,v)
        if set(by_source)!=required:
            raise MoveIntegrityError("Couverture des textes Move incomplète.")
        # Every compiled text is checked against its PBS counterpart independently.
        for section,obj in zip(pbs.sections,compiled.values()):
            assignment=section.name if field=="Name" else section.description
            if _ruby_text(obj.ivars[ivar],"Texte Move compilé").text()!=assignment.source:
                raise MoveIntegrityError("Texte Move compilé différent du PBS.")
        origins[field]=by_source
        extras[field]=extras_core
    graph_hashes={MOVE_MESSAGES_FILE:graph_sha256(core),GAME_MESSAGES_FILE:graph_sha256(game)}
    file_hashes={MOVE_MESSAGES_FILE:sha(core_raw),GAME_MESSAGES_FILE:sha(game_raw)}
    source_counts={field:Counter(values) for field,values in bank_sources.items()}
    pbs_sha=sha(pbs_raw);compiled_sha=sha(compiled_raw)
    result={}
    for section in pbs.sections:
        obj=compiled[section.identifier]
        for field,(ivar,index) in MOVE_TEXT_FIELDS.items():
            assignment=section.name if field=="Name" else section.description
            label,raw,root,pos,key,val=origins[field][assignment.source]
            count=source_counts[field][assignment.source]
            metadata={"format":FORMAT,"runtime_file":label,"file_sha256":file_hashes[label],
                "root_graph_sha256":graph_hashes[label],"bank_index":index,
                "bank_length":len(root[index]),"entry_index":pos,
                "source_sha256":sha(assignment.source.encode("utf-8")),
                "key_sha256":sha(key.data),"value_sha256":sha(val.data),
                "historical_core_extra_keys":extras[field],
                "source_usage_count":count,"reconstruction_allowed":False,
                "technical_mismatches":list(technical_by_id[section.identifier])}
            pbs_meta={"format":FORMAT,"pbs_file":MOVE_PBS_FILE,
                "file_sha256":pbs_sha,"section":section.identifier,
                "section_index":assignment.section_index,"field":field,
                "line_number":assignment.line_number,
                "line_sha256":sha(pbs.content_lines[assignment.line_index].encode("utf-8")),
                "source_sha256":sha(assignment.source.encode("utf-8")),
                "newline":assignment.newline,"reconstruction_allowed":False}
            cmp_meta={"format":FORMAT,"compiled_file":COMPILED_MOVE_FILE,
                "file_sha256":compiled_sha,"section":section.identifier,
                "field":ivar,"target_value_sha256":sha(obj.ivars[ivar].data),
                "technical_mismatches":list(technical_by_id[section.identifier]),
                "reconstruction_allowed":False}
            proof=MoveEntryProof(
                source=assignment.source,pbs_structure=j(pbs_meta),
                compiled_path=j([assignment.section_index,ivar]),
                compiled_structure=j(cmp_meta),
                runtime_path=j([index,"entry",pos]),
                runtime_structure=j(metadata)
            )
            result[(section.identifier,field,1)]=SplitMoveReadOnlyProof(proof,label,file_hashes[label])
    return result
