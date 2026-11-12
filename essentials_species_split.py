"""Preuves Pokédex v21.1 en lecture seule pour les deux banques de messages.

La banque virtuelle n'est jamais enregistrée. Ces preuves ne permettent aucune
reconstruction : elles documentent uniquement l'origine physique de chaque clé.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from essentials_phone import graph_sha256
from essentials_species import (
    COMPILED_SPECIES_FILE,
    POKEDEX_ENTRIES_MESSAGES_INDEX,
    SPECIES_MESSAGES_FILE,
    SpeciesEntryProof,
    SpeciesIntegrityError,
    _load_marshal,
    _ruby_text,
    build_species_pokedex_proofs,
)
from ruby_marshal_reader import RubyString
from ruby_marshal_writer import dumps


GAME_MESSAGES_FILE = "Data/messages_game.dat"
SPLIT_POKEDEX_READONLY_FORMAT = "pft_v21_1_species_pokedex_split_readonly_v1"


@dataclass(frozen=True)
class SplitPokedexReadOnlyProof:
    proof: SpeciesEntryProof
    runtime_file: str
    runtime_sha256: str


def build_split_pokedex_readonly_proofs(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    core_raw: bytes,
    game_raw: bytes,
) -> dict[tuple[str, str, int], SplitPokedexReadOnlyProof]:
    """Vérifie les sources par clés, pas par l'ordre des banques physiques."""
    index = POKEDEX_ENTRIES_MESSAGES_INDEX
    compiled = _load_marshal(compiled_raw, COMPILED_SPECIES_FILE)
    core = _load_marshal(core_raw, SPECIES_MESSAGES_FILE)
    game = _load_marshal(game_raw, GAME_MESSAGES_FILE)
    if not isinstance(compiled, dict) or not isinstance(core, list) or not isinstance(game, list):
        raise SpeciesIntegrityError("Structure compilée ou banques de messages inattendues.")
    if index >= len(core) or index >= len(game):
        raise SpeciesIntegrityError("Index POKEDEX_ENTRIES absent.")
    if not isinstance(core[index], dict) or not isinstance(game[index], dict):
        raise SpeciesIntegrityError("Les banques Pokédex doivent être des dictionnaires.")
    if not game[index]:
        raise SpeciesIntegrityError("Répartition Pokédex sans entrées dans la banque du jeu.")

    compiled_sources = []
    for obj in compiled.values():
        if not hasattr(obj, "ivars") or "@real_pokedex_entry" not in obj.ivars:
            raise SpeciesIntegrityError("Une espèce compilée ne possède pas de Pokédex.")
        compiled_sources.append(_ruby_text(
            obj.ivars["@real_pokedex_entry"], "Description Pokédex compilée"
        ).text())
    distinct_sources = list(dict.fromkeys(compiled_sources))
    required = set(distinct_sources)

    origin_by_source: dict[str, tuple[str, bytes, int, RubyString, RubyString, list]] = {}
    extras_core = 0
    for label, raw, root in (
        (SPECIES_MESSAGES_FILE, core_raw, core),
        (GAME_MESSAGES_FILE, game_raw, game),
    ):
        for position, (key, value) in enumerate(root[index].items()):
            if not isinstance(key, RubyString) or not isinstance(value, RubyString):
                raise SpeciesIntegrityError("Clé ou valeur Pokédex non RubyString.")
            if key.ivars != {"E": True} or value.ivars != {"E": True} or key is value:
                raise SpeciesIntegrityError("Métadonnées ou identité de message inattendues.")
            source = key.text()
            if value.text() != source:
                raise SpeciesIntegrityError("Une banque contient un texte de valeur inattendu.")
            if source in origin_by_source:
                raise SpeciesIntegrityError("Clé Pokédex ambiguë entre les deux banques.")
            if source not in required:
                if label == GAME_MESSAGES_FILE:
                    raise SpeciesIntegrityError("La banque du jeu contient une clé non compilée.")
                extras_core += 1
                continue
            origin_by_source[source] = (label, raw, position, key, value, root)

    if set(origin_by_source) != required:
        raise SpeciesIntegrityError("Des textes Pokédex compilés ne sont dans aucune banque.")

    # Réutilise la validation complète PBS, formes, espèces et alias existants.
    # L'ordre virtuel sert uniquement à ce validateur historique.
    virtual = list(core)
    virtual[index] = {
        origin_by_source[source][3]: origin_by_source[source][4]
        for source in distinct_sources
    }
    verified = build_species_pokedex_proofs(
        base_pbs_raw, forms_pbs_raw, compiled_raw, dumps(virtual)
    )
    result = {}
    for identity, validated in verified.items():
        label, raw, position, key, value, root = origin_by_source[validated.source]
        sha = hashlib.sha256(raw).hexdigest()
        metadata = {
            "format": SPLIT_POKEDEX_READONLY_FORMAT,
            "runtime_file": label,
            "file_sha256": sha,
            "root_graph_sha256": graph_sha256(root),
            "bank_index": index,
            "bank_length": len(root[index]),
            "entry_index": position,
            "key_sha256": hashlib.sha256(key.data).hexdigest(),
            "value_sha256": hashlib.sha256(value.data).hexdigest(),
            "source_sha256": hashlib.sha256(validated.source.encode("utf-8")).hexdigest(),
            "historical_core_extra_keys": extras_core,
            "reconstruction_allowed": False,
        }
        proof = SpeciesEntryProof(
            source=validated.source,
            pbs_structure=validated.pbs_structure,
            compiled_path=validated.compiled_path,
            compiled_structure=validated.compiled_structure,
            runtime_path=json.dumps([index, "entry", position], separators=(",", ":")),
            runtime_structure=json.dumps(
                metadata, sort_keys=True, ensure_ascii=True, separators=(",", ":")
            ),
        )
        result[identity] = SplitPokedexReadOnlyProof(proof, label, sha)
    return result
