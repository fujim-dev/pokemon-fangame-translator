"""Strict read-only provenance proofs for SPECIES_FORM_NAMES split between core/game.

Reuses full historical PBS/compiled verifier against an in-memory canonical
bank; the canonical bank is never written to disk. Reconstruction forbidden.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib,json
from essentials_phone import graph_sha256
from essentials_species import (
    COMPILED_SPECIES_FILE, SPECIES_MESSAGES_FILE, SpeciesEntryProof,
    SpeciesIntegrityError, _load_marshal,_ruby_text
)
from essentials_species_form import (
    SPECIES_FORM_NAME_MESSAGES_INDEX,build_species_form_name_proofs
)
from ruby_marshal_reader import RubyString
from ruby_marshal_writer import dumps

GAME_MESSAGES_FILE="Data/messages_game.dat"
FORMAT="pft_v21_1_form_name_split_readonly_v1"

@dataclass(frozen=True)
class SplitFormNameReadOnlyProof:
    proof: SpeciesEntryProof
    runtime_file: str
    runtime_sha256: str

def build_split_form_name_readonly_proofs(base_pbs_raw:bytes,forms_pbs_raw:bytes,
       compiled_raw:bytes,core_raw:bytes,game_raw:bytes)->dict:
    idx=SPECIES_FORM_NAME_MESSAGES_INDEX
    compiled=_load_marshal(compiled_raw,COMPILED_SPECIES_FILE)
    core=_load_marshal(core_raw,SPECIES_MESSAGES_FILE)
    game=_load_marshal(game_raw,GAME_MESSAGES_FILE)
    if not isinstance(compiled,dict) or not isinstance(core,list) or not isinstance(game,list):
        raise SpeciesIntegrityError("Structures FormName inattendues")
    if idx>=len(core) or idx>=len(game) or not isinstance(core[idx],dict) or not isinstance(game[idx],dict):
        raise SpeciesIntegrityError("Banque SPECIES_FORM_NAMES manquante")
    if not game[idx]:
        raise SpeciesIntegrityError("Pas de partition FormName core/game")
    distinct=[]
    for item in compiled.values():
        if not hasattr(item,"ivars") or "@real_form_name" not in item.ivars:
            raise SpeciesIntegrityError("FormName compilé absent")
        name=item.ivars["@real_form_name"]
        if name is not None:
            distinct.append(_ruby_text(name,"FormName compilé").text())
    unique=list(dict.fromkeys(distinct))
    required=set(unique)
    origins={}
    core_extras=0
    for label,raw,root in ((SPECIES_MESSAGES_FILE,core_raw,core),(GAME_MESSAGES_FILE,game_raw,game)):
        for position,(key,value) in enumerate(root[idx].items()):
            if not isinstance(key,RubyString) or not isinstance(value,RubyString):
                raise SpeciesIntegrityError("Message FormName non UTF-8")
            if key.ivars!={"E":True} or value.ivars!={"E":True} or key is value:
                raise SpeciesIntegrityError("Objets de banque FormName invalides")
            name=key.text()
            if value.text()!=name or name in origins:
                raise SpeciesIntegrityError("Valeur discordante ou clé dupliquée dans les banques")
            if name not in required:
                if label==GAME_MESSAGES_FILE:
                    raise SpeciesIntegrityError("Clé inattendue dans la banque game")
                core_extras+=1
                continue
            origins[name]=(label,raw,position,key,value,root)
    if set(origins)!=required:
        raise SpeciesIntegrityError("Correspondance FormName incomplète")
    virtual=list(core)
    virtual[idx]={origins[name][3]:origins[name][4] for name in unique}
    # Historical validator enforces PBS identities, compiled structure and inherited/absent rules.
    verified=build_species_form_name_proofs(base_pbs_raw,forms_pbs_raw,compiled_raw,dumps(virtual))
    result={}
    for identity,old in verified.items():
        label,raw,position,key,value,root=origins[old.source]
        sha=hashlib.sha256(raw).hexdigest()
        metadata={
            "format":FORMAT,"runtime_file":label,"file_sha256":sha,
            "root_graph_sha256":graph_sha256(root),"bank_index":idx,
            "bank_length":len(root[idx]),"entry_index":position,
            "key_sha256":hashlib.sha256(key.data).hexdigest(),
            "value_sha256":hashlib.sha256(value.data).hexdigest(),
            "source_sha256":hashlib.sha256(old.source.encode("utf-8")).hexdigest(),
            "historical_core_extra_keys":core_extras,
            "reconstruction_allowed":False
        }
        proof=SpeciesEntryProof(
            source=old.source,pbs_structure=old.pbs_structure,
            compiled_path=old.compiled_path,compiled_structure=old.compiled_structure,
            runtime_path=json.dumps([idx,"entry",position],separators=(",",":")),
            runtime_structure=json.dumps(metadata,sort_keys=True,separators=(",",":"))
        )
        result[identity]=SplitFormNameReadOnlyProof(proof,label,sha)
    return result
