"""Preuves d'extraction Ability v21.1 réparties entre core et jeu.

READ-ONLY : aucune preuve émise ici n'est valable pour une reconstruction.
On conserve les vérifications PBS/abilities.dat et on relie chaque texte à la
banque physique contenant réellement sa clé.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json

from essentials_ability import (
    ABILITY_MESSAGES_FILE,
    ABILITY_TEXT_FIELDS,
    ABILITY_PBS_FILE,
    COMPILED_ABILITY_FILE,
    AbilityEntryProof,
    AbilityIntegrityError,
    _load_marshal,
    build_ability_text_proofs,
    parse_ability_pbs,
)
from essentials_phone import graph_sha256
from ruby_marshal_reader import RubyString
from ruby_marshal_writer import dumps


GAME_MESSAGES_FILE = "Data/messages_game.dat"
SPLIT_READONLY_FORMAT = "pft_v21_1_ability_split_runtime_readonly_v1"


@dataclass(frozen=True)
class SplitAbilityReadOnlyProof:
    proof: AbilityEntryProof
    runtime_file: str
    runtime_sha256: str


def build_split_ability_readonly_proofs(
    pbs_raw: bytes,
    compiled_raw: bytes,
    core_raw: bytes,
    game_raw: bytes,
) -> dict[tuple[str, str, int], SplitAbilityReadOnlyProof]:
    """Relie toutes les capacités à leur vraie banque, sans action d'écriture.

    La partition doit être un préfixe/suffixe exact, dans l'ordre original,
    et toutes les valeurs runtime doivent être identiques aux textes PBS.
    """
    pbs = parse_ability_pbs(pbs_raw)
    core = _load_marshal(core_raw, ABILITY_MESSAGES_FILE)
    game = _load_marshal(game_raw, GAME_MESSAGES_FILE)
    if not isinstance(core, list) or not isinstance(game, list):
        raise AbilityIntegrityError("Les banques core/jeu doivent être des tableaux Marshal.")

    merged = list(core)
    locations: dict[tuple[str, str], tuple[str, bytes, int, int, object]] = {}
    for field, (_ivar, index) in ABILITY_TEXT_FIELDS.items():
        if index >= len(core) or index >= len(game):
            raise AbilityIntegrityError("Index de banque Ability absent.")
        core_bank, game_bank = core[index], game[index]
        if not isinstance(core_bank, dict) or not isinstance(game_bank, dict):
            raise AbilityIntegrityError("Banque Ability core/jeu de type inattendu.")
        sources = list(dict.fromkeys(
            (section.name if field == "Name" else section.description).source
            for section in pbs.sections
        ))
        core_items = list(core_bank.items())
        game_items = list(game_bank.items())
        if not game_items or len(core_items) + len(game_items) != len(sources):
            raise AbilityIntegrityError("La partition Ability core/jeu n'est pas complète.")
        for position, ((key, value), source) in enumerate(
            zip(core_items + game_items, sources)
        ):
            if (
                not isinstance(key, RubyString)
                or not isinstance(value, RubyString)
                or key.ivars != {"E": True}
                or value.ivars != {"E": True}
                or key.text() != source
                or value.text() != source
            ):
                raise AbilityIntegrityError("L'ordre ou la valeur Ability core/jeu diffère du PBS.")
            if position < len(core_items):
                origin, origin_raw, entry_index, root = (
                    ABILITY_MESSAGES_FILE, core_raw, position, core
                )
            else:
                origin, origin_raw, entry_index, root = (
                    GAME_MESSAGES_FILE, game_raw, position - len(core_items), game
                )
            locations[(field, source)] = (origin, origin_raw, index, entry_index, root)
        # Construire un instantané virtuel pour réutiliser les preuves PBS/compilé
        # existantes. Ne jamais envoyer cet instantané à la reconstruction.
        merged_bank = dict(core_bank)
        merged_bank.update(game_bank)
        merged[index] = merged_bank

    virtual = build_ability_text_proofs(pbs_raw, compiled_raw, dumps(merged))
    result: dict[tuple[str, str, int], SplitAbilityReadOnlyProof] = {}
    for key, validated in virtual.items():
        field = key[1]
        origin, origin_raw, index, entry_index, root = locations[(field, validated.source)]
        bank_key, bank_value = list(root[index].items())[entry_index]
        runtime_proof = {
            "format": SPLIT_READONLY_FORMAT,
            "runtime_file": origin,
            "file_sha256": hashlib.sha256(origin_raw).hexdigest(),
            "root_graph_sha256": graph_sha256(root),
            "bank_index": index,
            "bank_length": len(root[index]),
            "entry_index": entry_index,
            "key_sha256": hashlib.sha256(bank_key.data).hexdigest(),
            "value_sha256": hashlib.sha256(bank_value.data).hexdigest(),
            "source_sha256": hashlib.sha256(validated.source.encode("utf-8")).hexdigest(),
            "reconstruction_allowed": False,
        }
        proof = AbilityEntryProof(
            source=validated.source,
            pbs_structure=validated.pbs_structure,
            compiled_path=validated.compiled_path,
            compiled_structure=validated.compiled_structure,
            runtime_path=json.dumps([index, "entry", entry_index], separators=(",", ":")),
            runtime_structure=json.dumps(
                runtime_proof, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ),
        )
        result[key] = SplitAbilityReadOnlyProof(
            proof, origin, hashlib.sha256(origin_raw).hexdigest()
        )
    return result
