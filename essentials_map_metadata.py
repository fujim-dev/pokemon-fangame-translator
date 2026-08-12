# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée d'un nom de carte v21.1.

Le module ne lance jamais Ruby. Il relie ``PBS/map_metadata.txt`` à
``Data/map_metadata.dat`` puis à la banque ``MAP_NAMES`` de
``Data/messages_game.dat``. La porte privée refuse les noms partagés : leur
mutation demanderait d'ajouter ou de réordonner des clés dans la banque.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from essentials_phone import graph_sha256
from ruby_marshal_reader import (
    MarshalReader,
    RubyHashKey,
    RubyObject,
    RubyString,
    RubyUserDefined,
)
from ruby_marshal_writer import dumps


MAP_METADATA_PBS_FILE = "PBS/map_metadata.txt"
COMPILED_MAP_METADATA_FILE = "Data/map_metadata.dat"
MAP_METADATA_MESSAGES_FILE = "Data/messages_game.dat"
MAP_NAME_MESSAGES_INDEX = 21
MAP_METADATA_CLASS = "GameData::MapMetadata"
MAP_METADATA_PBS_PROOF_FORMAT = "pft_v21_1_map_metadata_name_pbs_v1"
COMPILED_MAP_METADATA_PROOF_FORMAT = "pft_v21_1_compiled_map_metadata_name_v1"
MAP_METADATA_RUNTIME_PROOF_FORMAT = "pft_v21_1_map_metadata_name_runtime_v1"
MAP_METADATA_IVARS = (
    "@id",
    "@real_name",
    "@outdoor_map",
    "@announce_location",
    "@can_bicycle",
    "@always_bicycle",
    "@teleport_destination",
    "@weather",
    "@town_map_position",
    "@dive_map_id",
    "@dark_map",
    "@safari_map",
    "@snap_edges",
    "@still_reflections",
    "@random_dungeon",
    "@battle_background",
    "@wild_battle_BGM",
    "@trainer_battle_BGM",
    "@wild_victory_BGM",
    "@trainer_victory_BGM",
    "@wild_capture_ME",
    "@town_map_size",
    "@battle_environment",
    "@flags",
    "@pbs_file_suffix",
)


class MapMetadataIntegrityError(ValueError):
    """La triple correspondance du nom de carte n'est pas démontrable."""


@dataclass(frozen=True)
class MapMetadataPbsAssignment:
    section: str
    map_id: int
    section_index: int
    line_index: int
    line_number: int
    prefix: str
    source: str
    trailing: str
    newline: str


@dataclass(frozen=True)
class MapMetadataPbsSection:
    identifier: str
    map_id: int
    name: MapMetadataPbsAssignment


@dataclass(frozen=True)
class MapMetadataPbsDocument:
    content_lines: tuple[str, ...]
    sections: tuple[MapMetadataPbsSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class MapMetadataTarget:
    assignment: MapMetadataPbsAssignment
    compiled_value: RubyString
    compiled_path: tuple[object, ...]
    message_key: RubyString
    message_value: RubyString
    message_path: tuple[object, ...]
    source_usage_count: int
    runtime_key_reference_count: int
    runtime_value_reference_count: int


@dataclass(frozen=True)
class MapMetadataEntryProof:
    source: str
    pbs_structure: str
    compiled_path: str
    compiled_structure: str
    runtime_path: str
    runtime_structure: str


@dataclass
class _MapMetadataAnalysis:
    pbs: MapMetadataPbsDocument
    compiled_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], MapMetadataTarget]
    compiled_graph_sha256: str
    messages_graph_sha256: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_marshal(raw: bytes, label: str) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise MapMetadataIntegrityError(f"{label} n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise MapMetadataIntegrityError(
            f"{label} est illisible sans exécuter Ruby."
        ) from exc
    if reader.pos != len(raw) or dumps(root) != raw:
        raise MapMetadataIntegrityError(
            f"Le lecteur/écrivain Marshal ne reproduit pas exactement {label}."
        )
    return root


def _ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise MapMetadataIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MapMetadataIntegrityError(f"{label} n'est pas une chaîne UTF-8 valide.") from exc
    return value


def _reference_count(value: object, target: object) -> int:
    seen: set[int] = set()

    def visit(current: object) -> int:
        count = int(current is target)
        if isinstance(current, RubyHashKey):
            return count + visit(current.value)
        if isinstance(current, (RubyString, RubyUserDefined, RubyObject, list, dict)):
            identity = id(current)
            if identity in seen:
                return count
            seen.add(identity)
        if isinstance(current, (RubyString, RubyUserDefined, RubyObject)):
            return count + sum(
                visit(key) + visit(child) for key, child in current.ivars.items()
            )
        if isinstance(current, list):
            return count + sum(visit(child) for child in current)
        if isinstance(current, dict):
            return count + sum(
                visit(key) + visit(child) for key, child in current.items()
            )
        return count

    return visit(value)


def _parse_assignment(body: str) -> tuple[str, str, str, str] | None:
    separator = body.find("=")
    if separator < 0:
        return None
    key = body[:separator].strip()
    if not key:
        return None
    after = body[separator + 1 :]
    leading_length = len(after) - len(after.lstrip(" \t"))
    remaining = after[leading_length:]
    trailing_length = len(remaining) - len(remaining.rstrip(" \t"))
    source = remaining[:-trailing_length] if trailing_length else remaining
    prefix = body[: separator + 1] + after[:leading_length]
    trailing = remaining[-trailing_length:] if trailing_length else ""
    return prefix, key, source, trailing


def parse_map_metadata_pbs(raw: bytes) -> MapMetadataPbsDocument:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise MapMetadataIntegrityError("PBS/map_metadata.txt doit conserver son BOM UTF-8.")
    payload = raw[3:]
    if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(b"\r\n", b""):
        raise MapMetadataIntegrityError(
            "PBS/map_metadata.txt doit conserver exclusivement ses CRLF."
        )
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MapMetadataIntegrityError(
            "PBS/map_metadata.txt n'est pas un UTF-8 valide."
        ) from exc
    lines = tuple(content.splitlines(keepends=True))
    if not lines or any(not line.endswith("\r\n") for line in lines):
        raise MapMetadataIntegrityError(
            "Chaque ligne de PBS/map_metadata.txt doit conserver son CRLF."
        )

    sections: list[MapMetadataPbsSection] = []
    identifier = ""
    map_id = 0
    section_index = -1
    name: MapMetadataPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal name
        if section_index < 0:
            return
        if name is None:
            raise MapMetadataIntegrityError("Une carte ne possède pas exactement un champ Name.")
        sections.append(MapMetadataPbsSection(identifier, map_id, name))
        name = None

    for line_index, raw_line in enumerate(lines):
        body = raw_line[:-2]
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        section_match = re.fullmatch(r"\[(\d{3,4})\](?:[ \t]+#.*)?", stripped)
        if section_match:
            finish_section()
            section_index += 1
            identifier = section_match.group(1)
            map_id = int(identifier)
            if map_id <= 0 or identifier != f"{map_id:03d}":
                raise MapMetadataIntegrityError("Identifiant numérique de carte non canonique.")
            continue
        if section_index < 0:
            raise MapMetadataIntegrityError("Affectation de carte située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise MapMetadataIntegrityError("Ligne map_metadata non reconnue.")
        prefix, key, source, trailing = parsed
        if key == "Name":
            if name is not None or not source or source != source.strip():
                raise MapMetadataIntegrityError("Name de carte absent, dupliqué ou ambigu.")
            name = MapMetadataPbsAssignment(
                section=identifier,
                map_id=map_id,
                section_index=section_index,
                line_index=line_index,
                line_number=line_index + 1,
                prefix=prefix,
                source=source,
                trailing=trailing,
                newline="\r\n",
            )
    finish_section()
    identifiers = [section.identifier for section in sections]
    map_ids = [section.map_id for section in sections]
    if (
        not sections
        or len(set(identifiers)) != len(identifiers)
        or len(set(map_ids)) != len(map_ids)
    ):
        raise MapMetadataIntegrityError("Sections map_metadata absentes ou dupliquées.")
    return MapMetadataPbsDocument(lines, tuple(sections), _sha256(raw))


def _validate_compiled_entry(
    key: object,
    section: MapMetadataPbsSection,
    value: object,
) -> RubyObject:
    if not isinstance(key, int) or key != section.map_id:
        raise MapMetadataIntegrityError("La clé numérique compilée de carte a changé.")
    if not isinstance(value, RubyObject) or value.class_name != MAP_METADATA_CLASS:
        raise MapMetadataIntegrityError("Une entrée compilée n'est pas GameData::MapMetadata.")
    if tuple(value.ivars) != MAP_METADATA_IVARS:
        raise MapMetadataIntegrityError("La structure compilée d'une carte a changé.")
    if (
        value.ivars["@id"] != section.map_id
        or _ruby_text(value.ivars["@real_name"], "Le nom compilé de carte").text()
        != section.name.source
        or not isinstance(value.ivars["@flags"], list)
        or _ruby_text(
            value.ivars["@pbs_file_suffix"], "Le suffixe PBS de carte"
        ).text()
        != ""
    ):
        raise MapMetadataIntegrityError("Les métadonnées compilées de la carte ont changé.")
    return value


def _analyze_sources(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _MapMetadataAnalysis:
    pbs = parse_map_metadata_pbs(pbs_raw)
    compiled_root = _load_marshal(compiled_raw, COMPILED_MAP_METADATA_FILE)
    if not isinstance(compiled_root, dict) or len(compiled_root) != len(pbs.sections):
        raise MapMetadataIntegrityError(
            "La racine map_metadata.dat ne correspond pas aux sections PBS."
        )

    flattened: list[tuple[MapMetadataPbsSection, RubyString]] = []
    for section, (key, raw_object) in zip(pbs.sections, compiled_root.items()):
        compiled_object = _validate_compiled_entry(key, section, raw_object)
        compiled_value = compiled_object.ivars["@real_name"]
        if _reference_count(compiled_root, compiled_value) != 1:
            raise MapMetadataIntegrityError(
                "Une chaîne de map_metadata.dat est partagée ou ambiguë."
            )
        flattened.append((section, compiled_value))

    messages_root = _load_marshal(messages_raw, MAP_METADATA_MESSAGES_FILE)
    if (
        not isinstance(messages_root, list)
        or len(messages_root) <= MAP_NAME_MESSAGES_INDEX
        or not isinstance(messages_root[MAP_NAME_MESSAGES_INDEX], dict)
    ):
        raise MapMetadataIntegrityError(
            "La banque MAP_NAMES n'est pas à l'index v21.1 attendu."
        )
    source_counts: dict[str, int] = {}
    ordered_unique: list[str] = []
    for section, _value in flattened:
        source = section.name.source
        source_counts[source] = source_counts.get(source, 0) + 1
        if source_counts[source] == 1:
            ordered_unique.append(source)
    message_items = list(messages_root[MAP_NAME_MESSAGES_INDEX].items())
    if len(message_items) != len(ordered_unique):
        raise MapMetadataIntegrityError(
            "La banque MAP_NAMES ne couvre pas exactement map_metadata.dat."
        )
    runtime_by_source: dict[str, tuple[RubyString, RubyString, int, int, int]] = {}
    for message_index, (source, (key, value)) in enumerate(zip(ordered_unique, message_items)):
        message_key = _ruby_text(key, "La clé MAP_NAMES")
        message_value = _ruby_text(value, "La valeur MAP_NAMES")
        if message_key.text() != source or message_value.text() != source:
            raise MapMetadataIntegrityError("L'ordre de la banque MAP_NAMES ne correspond plus.")
        runtime_by_source[source] = (
            message_key,
            message_value,
            message_index,
            _reference_count(messages_root, message_key),
            _reference_count(messages_root, message_value),
        )

    targets: dict[tuple[str, str, int], MapMetadataTarget] = {}
    for section, compiled_value in flattened:
        message_key, message_value, message_index, key_refs, value_refs = runtime_by_source[
            section.name.source
        ]
        targets[(section.identifier, "Name", 1)] = MapMetadataTarget(
            assignment=section.name,
            compiled_value=compiled_value,
            compiled_path=(section.map_id, "@real_name"),
            message_key=message_key,
            message_value=message_value,
            message_path=(MAP_NAME_MESSAGES_INDEX, "entry", message_index),
            source_usage_count=source_counts[section.name.source],
            runtime_key_reference_count=key_refs,
            runtime_value_reference_count=value_refs,
        )
    return _MapMetadataAnalysis(
        pbs,
        compiled_root,
        messages_root,
        targets,
        graph_sha256(compiled_root),
        graph_sha256(messages_root),
    )


def _pbs_proof(analysis: _MapMetadataAnalysis, target: MapMetadataTarget) -> str:
    assignment = target.assignment
    proof = {
        "format": MAP_METADATA_PBS_PROOF_FORMAT,
        "pbs_file": MAP_METADATA_PBS_FILE,
        "file_sha256": analysis.pbs.file_sha256,
        "encoding": "utf-8-sig",
        "bom": "utf-8",
        "newline": "CRLF",
        "line_number": assignment.line_number,
        "section_index": assignment.section_index,
        "map_id": assignment.map_id,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "key": "Name",
        "key_occurrence": 1,
        "prefix_sha256": _sha256(assignment.prefix.encode("utf-8")),
        "trailing_sha256": _sha256(assignment.trailing.encode("utf-8")),
        "line_sha256": _sha256(
            analysis.pbs.content_lines[assignment.line_index].encode("utf-8")
        ),
        "source_sha256": _sha256(assignment.source.encode("utf-8")),
        "source_usage_count": target.source_usage_count,
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _compiled_proof(
    analysis: _MapMetadataAnalysis,
    target: MapMetadataTarget,
    compiled_raw: bytes,
) -> str:
    compiled_object = analysis.compiled_root[target.assignment.map_id]
    proof = {
        "format": COMPILED_MAP_METADATA_PROOF_FORMAT,
        "compiled_file": COMPILED_MAP_METADATA_FILE,
        "file_sha256": _sha256(compiled_raw),
        "root_type": "Hash",
        "root_size": len(analysis.compiled_root),
        "root_graph_sha256": analysis.compiled_graph_sha256,
        "non_target_section_graph_sha256": graph_sha256(
            compiled_object,
            masked=(target.compiled_value,),
        ),
        "section_index": target.assignment.section_index,
        "map_id": target.assignment.map_id,
        "section_class": MAP_METADATA_CLASS,
        "section_ivars": list(MAP_METADATA_IVARS),
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.compiled_value.ivars),
        "target_value_sha256": _sha256(target.compiled_value.data),
        "target_reference_count": 1,
        "compiled_path": list(target.compiled_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _MapMetadataAnalysis,
    target: MapMetadataTarget,
    messages_raw: bytes,
) -> str:
    proof = {
        "format": MAP_METADATA_RUNTIME_PROOF_FORMAT,
        "runtime_file": MAP_METADATA_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "message_type_index": MAP_NAME_MESSAGES_INDEX,
        "message_count": len(analysis.messages_root[MAP_NAME_MESSAGES_INDEX]),
        "root_graph_sha256": analysis.messages_graph_sha256,
        "non_target_graph_sha256": _runtime_graph_without_entry(
            analysis.messages_root, target.message_path[-1]
        ),
        "target_key_sha256": _sha256(target.message_key.data),
        "target_value_sha256": _sha256(target.message_value.data),
        "target_value_equals_source": target.message_value.text()
        == target.assignment.source,
        "target_key_reference_count": target.runtime_key_reference_count,
        "target_value_reference_count": target.runtime_value_reference_count,
        "source_usage_count": target.source_usage_count,
        "runtime_path": list(target.message_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_graph_without_entry(messages_root: list, target_index: int) -> str:
    """Empreinte tout le graphe sauf l'unique paire MAP_NAMES ciblée.

    Une clé MAP_NAMES peut être le même objet Ruby qu'une clé d'une autre banque
    (cas réel de Route 2). Le réinjecteur crée alors une nouvelle clé uniquement
    dans MAP_NAMES et doit prouver que toutes les autres références à l'ancien
    objet restent strictement inchangées.
    """
    bank = messages_root[MAP_NAME_MESSAGES_INDEX]
    if not isinstance(bank, dict) or not (0 <= target_index < len(bank)):
        raise MapMetadataIntegrityError("Index MAP_NAMES cible invalide.")
    clone = list(messages_root)
    clone[MAP_NAME_MESSAGES_INDEX] = {
        key: value
        for index, (key, value) in enumerate(bank.items())
        if index != target_index
    }
    return graph_sha256(clone)


def build_map_metadata_name_proofs(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> dict[tuple[str, str, int], MapMetadataEntryProof]:
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    return {
        key: MapMetadataEntryProof(
            source=target.assignment.source,
            pbs_structure=_pbs_proof(analysis, target),
            compiled_path=json.dumps(list(target.compiled_path), separators=(",", ":")),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(list(target.message_path), separators=(",", ":")),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
        for key, target in analysis.targets.items()
    }


def rebuild_map_metadata_name_payloads(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
    source: str,
    translation: str,
    pbs_structure: str,
    compiled_path: str,
    compiled_structure: str,
    runtime_path: str,
    runtime_structure: str,
) -> dict[str, bytes]:
    if not translation or any(character in translation for character in ("\r", "\n")):
        raise MapMetadataIntegrityError("Le nom de carte traduit ne tient pas sur une ligne PBS.")
    translated = translation.encode("utf-8")
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    target_key = (section, "Name", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise MapMetadataIntegrityError("Le nom de carte ne correspond plus à la source.")
    expected = build_map_metadata_name_proofs(pbs_raw, compiled_raw, messages_raw)[target_key]
    if (
        expected.pbs_structure != pbs_structure
        or expected.compiled_path != compiled_path
        or expected.compiled_structure != compiled_structure
        or expected.runtime_path != runtime_path
        or expected.runtime_structure != runtime_structure
    ):
        raise MapMetadataIntegrityError(
            "La preuve du nom de carte ne correspond plus aux trois sources."
        )
    if target.source_usage_count != 1:
        raise MapMetadataIntegrityError(
            "Ce nom est partagé par plusieurs cartes et reste volontairement bloqué."
        )
    if target.runtime_key_reference_count < 1 or target.runtime_value_reference_count < 1:
        raise MapMetadataIntegrityError("Les références Marshal de ce nom sont incohérentes.")
    if json.loads(runtime_structure).get("target_value_equals_source") is not True:
        raise MapMetadataIntegrityError("La banque MAP_NAMES possède déjà une traduction différente.")
    if translation in {
        item.text()
        for item in analysis.messages_root[MAP_NAME_MESSAGES_INDEX]
        if item is not target.message_key
    }:
        raise MapMetadataIntegrityError("La traduction entrerait en collision avec un autre nom.")

    assignment = target.assignment
    pbs_lines = list(analysis.pbs.content_lines)
    pbs_lines[assignment.line_index] = (
        assignment.prefix + translation + assignment.trailing + assignment.newline
    )
    rebuilt_pbs = b"\xef\xbb\xbf" + "".join(pbs_lines).encode("utf-8")

    compiled_before = graph_sha256(analysis.compiled_root, masked=(target.compiled_value,))
    compiled_object = analysis.compiled_root[assignment.map_id]
    compiled_object.ivars["@real_name"] = RubyString(
        translated, dict(target.compiled_value.ivars)
    )
    rebuilt_compiled_target = compiled_object.ivars["@real_name"]
    if graph_sha256(
        analysis.compiled_root, masked=(rebuilt_compiled_target,)
    ) != compiled_before:
        raise MapMetadataIntegrityError("La mutation modifierait map_metadata.dat hors cible.")
    rebuilt_compiled = dumps(analysis.compiled_root)

    runtime_before = _runtime_graph_without_entry(
        analysis.messages_root, target.message_path[-1]
    )
    replacement_hash: dict = {}
    old_hash = analysis.messages_root[MAP_NAME_MESSAGES_INDEX]
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_hash[RubyString(translated, dict(old_key.ivars))] = RubyString(
                translated, dict(old_value.ivars)
            )
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[MAP_NAME_MESSAGES_INDEX] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[target.message_path[-1]]
    if (
        _runtime_graph_without_entry(
            analysis.messages_root, target.message_path[-1]
        )
        != runtime_before
    ):
        raise MapMetadataIntegrityError(
            "La mutation modifierait MAP_NAMES ou une banque alias hors cible."
        )
    rebuilt_messages = dumps(analysis.messages_root)

    rebuilt = _analyze_sources(rebuilt_pbs, rebuilt_compiled, rebuilt_messages)
    rebuilt_target = rebuilt.targets.get(target_key)
    if (
        rebuilt_target is None
        or rebuilt_target.assignment.source != translation
        or rebuilt_target.compiled_value.text() != translation
        or rebuilt_target.message_key.text() != translation
        or rebuilt_target.message_value.text() != translation
        or rebuilt_target.compiled_path != target.compiled_path
        or rebuilt_target.message_path != target.message_path
    ):
        raise MapMetadataIntegrityError("La relecture ne retrouve pas le nom de carte exact.")
    return {
        MAP_METADATA_PBS_FILE: rebuilt_pbs,
        COMPILED_MAP_METADATA_FILE: rebuilt_compiled,
        MAP_METADATA_MESSAGES_FILE: rebuilt_messages,
    }


def extract_map_metadata_name_texts(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
) -> tuple[str, str, str]:
    target = _analyze_sources(pbs_raw, compiled_raw, messages_raw).targets.get(
        (section, "Name", 1)
    )
    if target is None:
        raise MapMetadataIntegrityError("Le nom de carte est introuvable.")
    return (
        target.assignment.source,
        target.compiled_value.text(),
        target.message_value.text(),
    )
