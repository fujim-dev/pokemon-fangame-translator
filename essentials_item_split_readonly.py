"""Read-only multi-PBS and two-bank Item proof, with actual Ruby message key normalization.

No rewriting or reconstruction. Every PBS assignment, full compiled Item and
physical message key/value must agree; supplemental PBS suffix is explicit.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import re

from essentials_item import (
    COMPILED_ITEM_FILE, ITEM_MESSAGES_FILE, ITEM_PBS_FILE, ITEM_TEXT_FIELDS,
    ItemEntryProof, ItemIntegrityError, _load_marshal, _ruby_text,
    _validate_compiled_entry, parse_item_pbs,
)
from ruby_marshal_reader import RubyObject, RubyString

SUPPLEMENTAL_ITEM_PBS_FILE = "PBS/items_raid_bait.txt"
GAME_MESSAGES_FILE = "Data/messages_game.dat"
READONLY_FORMAT = "pft_v21_1_items_multi_pbs_split_readonly_v1"


@dataclass(frozen=True)
class ItemSplitReadOnlyProof:
    proof: ItemEntryProof
    pbs_file: str
    runtime_file: str
    runtime_sha256: str


def _ruby_message_key(source: str) -> str:
    """Static equivalent for Essentials Translation.stringToKey (Scripts 6:481-489)."""
    if re.search(r"[\r\n\t\x01]|^\s+|\s+$|\s{2,}", source):
        return re.sub(r"\s{2,}", " ", re.sub(r"\s+$", "", re.sub(r"^\s+", "", source)))
    return source


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_split_item_readonly_proofs(
    main_pbs_raw: bytes,
    supplemental_pbs_raw: bytes,
    compiled_raw: bytes,
    core_raw: bytes,
    game_raw: bytes,
) -> dict[tuple[str, str, int], ItemSplitReadOnlyProof]:
    main_doc = parse_item_pbs(main_pbs_raw)
    extra_doc = parse_item_pbs(supplemental_pbs_raw)
    if len(extra_doc.sections) != 1:
        raise ItemIntegrityError("La source Item complémentaire n'a pas une seule section.")
    sections = [
        (ITEM_PBS_FILE, main_doc, section, "")
        for section in main_doc.sections
    ] + [
        (SUPPLEMENTAL_ITEM_PBS_FILE, extra_doc, section, "raid_bait")
        for section in extra_doc.sections
    ]
    expected_ids = [section.identifier for _, _, section, _ in sections]
    if len(set(expected_ids)) != len(expected_ids):
        raise ItemIntegrityError("Identifiant Item en double entre les PBS.")
    compiled = _load_marshal(compiled_raw, COMPILED_ITEM_FILE)
    if not isinstance(compiled, dict) or list(compiled) != expected_ids:
        raise ItemIntegrityError("L'ordre ou le compte des Items compilés ne correspond pas aux deux PBS.")
    core = _load_marshal(core_raw, ITEM_MESSAGES_FILE)
    game = _load_marshal(game_raw, GAME_MESSAGES_FILE)
    if not isinstance(core, list) or not isinstance(game, list):
        raise ItemIntegrityError("La racine des banques Item n'est pas un Array.")
    if not any(isinstance(game[index], dict) and game[index] for _, index in ITEM_TEXT_FIELDS.values() if index < len(game)):
        raise ItemIntegrityError("Aucun découpage physique Item core/game n'est constaté.")

    per_field = {field: [] for field in ITEM_TEXT_FIELDS}
    for compiled_position, ((pbs_file, doc, section, suffix), (key, value)) in enumerate(zip(sections, compiled.items())):
        if suffix:
            if not isinstance(value, RubyObject) or "@pbs_file_suffix" not in value.ivars:
                raise ItemIntegrityError("Le suffixe du complément compilé est absent.")
            if _ruby_text(value.ivars["@pbs_file_suffix"], "suffixe PBS Item").text() != suffix:
                raise ItemIntegrityError("Le suffixe du complément Item ne correspond pas au PBS.")
            # Historical technical validator assumes empty PBS suffix. Validate a
            # shallow copy with that one contextual field masked, never the real object.
            surrogate = RubyObject(value.class_name, dict(value.ivars))
            surrogate.ivars["@pbs_file_suffix"] = RubyString(b"", {"E": True})
            _validate_compiled_entry(key, section, surrogate)
        else:
            _validate_compiled_entry(key, section, value)
        for field, (ivar, index) in ITEM_TEXT_FIELDS.items():
            assignment = section.text_assignment(field)
            compiled_value = value.ivars[ivar]
            if assignment is None:
                if compiled_value is not None:
                    raise ItemIntegrityError("Une chaîne Item optionnelle absente existe dans le compilé.")
                continue
            actual = _ruby_text(compiled_value, "texte Item compilé").text()
            if actual != assignment.source:
                raise ItemIntegrityError("Le texte compilé Item diffère du PBS.")
            per_field[field].append((pbs_file, doc, assignment, compiled_position, compiled_value))

    pbs_hashes = {ITEM_PBS_FILE: _hash(main_pbs_raw), SUPPLEMENTAL_ITEM_PBS_FILE: _hash(supplemental_pbs_raw)}
    runtime_hashes = {ITEM_MESSAGES_FILE: _hash(core_raw), GAME_MESSAGES_FILE: _hash(game_raw)}
    bank_roots = [(ITEM_MESSAGES_FILE, core), (GAME_MESSAGES_FILE, game)]
    proofs = {}
    for field, (_ivar, index) in ITEM_TEXT_FIELDS.items():
        entries = per_field[field]
        expected_sources = set(assignment.source for _, _, assignment, _, _ in entries)
        source_counts = Counter(assignment.source for _, _, assignment, _, _ in entries)
        expected_keys = {_ruby_message_key(source) for source in expected_sources}
        if len(expected_keys) != len(expected_sources):
            raise ItemIntegrityError("Deux sources Item distinctes se normalisent vers la même clé.")
        origins = {}
        extras_core = 0
        for label, bank in bank_roots:
            if index >= len(bank) or not isinstance(bank[index], dict):
                raise ItemIntegrityError("Index de banque Item absent ou mal typé.")
            for position, (key, value) in enumerate(bank[index].items()):
                name = _ruby_text(key, "clé de message Item").text()
                actual = _ruby_text(value, "valeur de message Item").text()
                if key is value or name in origins:
                    raise ItemIntegrityError("Identité d'objet ou clé ambiguë dans les banques Item.")
                if name not in expected_keys:
                    if label == GAME_MESSAGES_FILE or actual != name:
                        raise ItemIntegrityError("Une banque Item contient une clé historique non canonique.")
                    extras_core += 1
                    continue
                origins[name] = (label, position, key, value, actual)
        if set(origins) != expected_keys:
            raise ItemIntegrityError("Des clés Item compilées n'ont pas de provenance physique.")
        for pbs_file, doc, assignment, compiled_position, compiled_value in entries:
            name = _ruby_message_key(assignment.source)
            runtime_file, position, key, value, runtime_value = origins[name]
            if runtime_value != assignment.source:
                raise ItemIntegrityError("La valeur de banque Item doit correspondre au texte PBS complet.")
            metadata = {
                "format": READONLY_FORMAT,
                "pbs_file": pbs_file,
                "pbs_sha256": pbs_hashes[pbs_file],
                "pbs_line_number": assignment.line_number,
                "pbs_line_sha256": _hash(doc.content_lines[assignment.line_index].encode("utf-8")),
                "compiled_file": COMPILED_ITEM_FILE,
                "compiled_sha256": _hash(compiled_raw),
                "compiled_index": compiled_position,
                "compiled_field": field,
                "compiled_ivar": ITEM_TEXT_FIELDS[field][0],
                "source_sha256": _hash(assignment.source.encode("utf-8")),
                "source_usage_count": source_counts[assignment.source],
                "runtime_file": runtime_file,
                "runtime_sha256": runtime_hashes[runtime_file],
                "runtime_bank_index": index,
                "runtime_entry_index": position,
                "normalized_key_sha256": _hash(name.encode("utf-8")),
                "key_normalization_applied": name != assignment.source,
                "runtime_key_sha256": _hash(key.data),
                "runtime_value_sha256": _hash(value.data),
                "historical_core_extra_keys": extras_core,
                "supplemental_pbs": pbs_file == SUPPLEMENTAL_ITEM_PBS_FILE,
                "reconstruction_allowed": False,
            }
            serialized = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
            proof = ItemEntryProof(
                source=assignment.source,
                pbs_structure=serialized,
                compiled_path=json.dumps([compiled_position, ITEM_TEXT_FIELDS[field][0]], separators=(",", ":")),
                compiled_structure=serialized,
                runtime_path=json.dumps([index, "entry", position], separators=(",", ":")),
                runtime_structure=serialized,
            )
            identity = (assignment.section, field, 1)
            if identity in proofs:
                raise ItemIntegrityError("Occurrence Item en double.")
            proofs[identity] = ItemSplitReadOnlyProof(
                proof=proof, pbs_file=pbs_file, runtime_file=runtime_file,
                runtime_sha256=runtime_hashes[runtime_file]
            )
    return proofs
