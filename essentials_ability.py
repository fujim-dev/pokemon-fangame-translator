# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée d'une description de capacité v21.1.

Le module ne lance jamais Ruby. Il relie ``PBS/abilities.txt`` à
``Data/abilities.dat`` puis à la banque ``ABILITY_DESCRIPTIONS`` de
``Data/messages_core.dat``. Une description partagée reste extractible, mais
la porte de reconstruction privée la refuse.
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


ABILITY_PBS_FILE = "PBS/abilities.txt"
COMPILED_ABILITY_FILE = "Data/abilities.dat"
ABILITY_MESSAGES_FILE = "Data/messages_core.dat"
ABILITY_DESCRIPTION_MESSAGES_INDEX = 11
ABILITY_CLASS = "GameData::Ability"
ABILITY_PBS_PROOF_FORMAT = "pft_v21_1_ability_description_pbs_v1"
COMPILED_ABILITY_PROOF_FORMAT = "pft_v21_1_compiled_ability_description_v1"
ABILITY_RUNTIME_PROOF_FORMAT = "pft_v21_1_ability_description_runtime_v1"
ABILITY_IVARS = (
    "@id",
    "@real_name",
    "@real_description",
    "@flags",
    "@pbs_file_suffix",
)


class AbilityIntegrityError(ValueError):
    """La triple correspondance de la description n'est pas démontrable."""


@dataclass(frozen=True)
class AbilityPbsAssignment:
    section: str
    section_index: int
    line_index: int
    line_number: int
    prefix: str
    source: str
    trailing: str
    newline: str


@dataclass(frozen=True)
class AbilityPbsSection:
    identifier: str
    name: str
    flags: tuple[str, ...]
    description: AbilityPbsAssignment


@dataclass(frozen=True)
class AbilityPbsDocument:
    content_lines: tuple[str, ...]
    sections: tuple[AbilityPbsSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class AbilityTarget:
    assignment: AbilityPbsAssignment
    compiled_value: RubyString
    compiled_path: tuple[object, ...]
    message_key: RubyString
    message_value: RubyString
    message_path: tuple[object, ...]
    source_usage_count: int
    runtime_key_reference_count: int
    runtime_value_reference_count: int


@dataclass(frozen=True)
class AbilityEntryProof:
    source: str
    pbs_structure: str
    compiled_path: str
    compiled_structure: str
    runtime_path: str
    runtime_structure: str


@dataclass
class _AbilityAnalysis:
    pbs: AbilityPbsDocument
    compiled_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], AbilityTarget]
    compiled_graph_sha256: str
    messages_graph_sha256: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_marshal(raw: bytes, label: str) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise AbilityIntegrityError(f"{label} n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise AbilityIntegrityError(f"{label} est illisible sans exécuter Ruby.") from exc
    if reader.pos != len(raw) or dumps(root) != raw:
        raise AbilityIntegrityError(
            f"Le lecteur/écrivain Marshal ne reproduit pas exactement {label}."
        )
    return root


def _ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise AbilityIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AbilityIntegrityError(f"{label} n'est pas une chaîne UTF-8 valide.") from exc
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


def parse_ability_pbs(raw: bytes) -> AbilityPbsDocument:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise AbilityIntegrityError("PBS/abilities.txt doit conserver son BOM UTF-8.")
    payload = raw[3:]
    if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(b"\r\n", b""):
        raise AbilityIntegrityError("PBS/abilities.txt doit conserver exclusivement ses CRLF.")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AbilityIntegrityError("PBS/abilities.txt n'est pas un UTF-8 valide.") from exc
    lines = tuple(content.splitlines(keepends=True))
    if not lines or any(not line.endswith("\r\n") for line in lines):
        raise AbilityIntegrityError("Chaque ligne de PBS/abilities.txt doit conserver son CRLF.")

    sections: list[AbilityPbsSection] = []
    identifier = ""
    section_index = -1
    name: str | None = None
    flags: tuple[str, ...] | None = None
    description: AbilityPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal name, flags, description
        if section_index < 0:
            return
        if name is None or description is None:
            raise AbilityIntegrityError("Une capacité ne possède pas exactement Name et Description.")
        sections.append(AbilityPbsSection(identifier, name, flags or (), description))
        name = None
        flags = None
        description = None

    for line_index, raw_line in enumerate(lines):
        body = raw_line[:-2]
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        section_match = re.fullmatch(r"\[([^\]]+)\]", stripped)
        if section_match:
            finish_section()
            section_index += 1
            identifier = section_match.group(1).strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", identifier):
                raise AbilityIntegrityError("Identifiant de capacité non canonique.")
            continue
        if section_index < 0:
            raise AbilityIntegrityError("Affectation de capacité située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise AbilityIntegrityError("Ligne de capacité non reconnue.")
        prefix, key, source, trailing = parsed
        if key == "Name":
            if name is not None or not source or source != source.strip():
                raise AbilityIntegrityError("Name de capacité absent, dupliqué ou ambigu.")
            name = source
        elif key == "Flags":
            if flags is not None or not source or source != source.strip():
                raise AbilityIntegrityError("Flags de capacité dupliqués ou ambigus.")
            parsed_flags = tuple(source.split(","))
            if any(
                flag != flag.strip()
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", flag) is None
                for flag in parsed_flags
            ):
                raise AbilityIntegrityError("Flags de capacité non canoniques.")
            flags = parsed_flags
        elif key == "Description":
            if description is not None or not source or source != source.strip():
                raise AbilityIntegrityError("Description de capacité absente, dupliquée ou ambiguë.")
            description = AbilityPbsAssignment(
                section=identifier,
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
    if not sections or len(set(identifiers)) != len(identifiers):
        raise AbilityIntegrityError("Sections de capacités absentes ou dupliquées.")
    return AbilityPbsDocument(lines, tuple(sections), _sha256(raw))


def _validate_compiled_entry(
    key: object,
    section: AbilityPbsSection,
    value: object,
) -> RubyObject:
    if not isinstance(key, str) or key != section.identifier:
        raise AbilityIntegrityError("La clé compilée de capacité a changé.")
    if not isinstance(value, RubyObject) or value.class_name != ABILITY_CLASS:
        raise AbilityIntegrityError("Une entrée compilée n'est pas GameData::Ability.")
    if tuple(value.ivars) != ABILITY_IVARS:
        raise AbilityIntegrityError("La structure compilée d'une capacité a changé.")
    if (
        value.ivars["@id"] != section.identifier
        or _ruby_text(value.ivars["@real_name"], "Le nom compilé de capacité").text()
        != section.name
        or _ruby_text(
            value.ivars["@real_description"], "La description compilée de capacité"
        ).text()
        != section.description.source
        or not isinstance(value.ivars["@flags"], list)
        or tuple(
            _ruby_text(flag, "Un flag compilé de capacité").text()
            for flag in value.ivars["@flags"]
        )
        != section.flags
        or _ruby_text(value.ivars["@pbs_file_suffix"], "Le suffixe PBS de capacité").text()
        != ""
    ):
        raise AbilityIntegrityError("Les métadonnées compilées de la capacité ont changé.")
    return value


def _analyze_sources(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _AbilityAnalysis:
    pbs = parse_ability_pbs(pbs_raw)
    compiled_root = _load_marshal(compiled_raw, COMPILED_ABILITY_FILE)
    if not isinstance(compiled_root, dict) or len(compiled_root) != len(pbs.sections):
        raise AbilityIntegrityError("La racine abilities.dat ne correspond pas aux sections PBS.")

    flattened: list[tuple[AbilityPbsSection, RubyString]] = []
    for section, (key, raw_object) in zip(pbs.sections, compiled_root.items()):
        compiled_object = _validate_compiled_entry(key, section, raw_object)
        compiled_value = compiled_object.ivars["@real_description"]
        if _reference_count(compiled_root, compiled_value) != 1:
            raise AbilityIntegrityError("Une chaîne abilities.dat est partagée ou ambiguë.")
        flattened.append((section, compiled_value))

    messages_root = _load_marshal(messages_raw, ABILITY_MESSAGES_FILE)
    if (
        not isinstance(messages_root, list)
        or len(messages_root) <= ABILITY_DESCRIPTION_MESSAGES_INDEX
        or not isinstance(messages_root[ABILITY_DESCRIPTION_MESSAGES_INDEX], dict)
    ):
        raise AbilityIntegrityError(
            "La banque ABILITY_DESCRIPTIONS n'est pas à l'index v21.1 attendu."
        )
    source_counts: dict[str, int] = {}
    ordered_unique: list[str] = []
    for section, _value in flattened:
        source = section.description.source
        source_counts[source] = source_counts.get(source, 0) + 1
        if source_counts[source] == 1:
            ordered_unique.append(source)
    message_items = list(messages_root[ABILITY_DESCRIPTION_MESSAGES_INDEX].items())
    if len(message_items) != len(ordered_unique):
        raise AbilityIntegrityError("La banque de descriptions ne couvre pas exactement abilities.dat.")
    runtime_by_source: dict[str, tuple[RubyString, RubyString, int, int, int]] = {}
    for message_index, (source, (key, value)) in enumerate(zip(ordered_unique, message_items)):
        message_key = _ruby_text(key, "La clé ABILITY_DESCRIPTIONS")
        message_value = _ruby_text(value, "La valeur ABILITY_DESCRIPTIONS")
        if message_key.text() != source or message_value.text() != source:
            raise AbilityIntegrityError("L'ordre de la banque de descriptions ne correspond plus.")
        key_references = _reference_count(messages_root, message_key)
        value_references = _reference_count(messages_root, message_value)
        runtime_by_source[source] = (
            message_key,
            message_value,
            message_index,
            key_references,
            value_references,
        )

    targets: dict[tuple[str, str, int], AbilityTarget] = {}
    for section, compiled_value in flattened:
        message_key, message_value, message_index, key_refs, value_refs = runtime_by_source[
            section.description.source
        ]
        targets[(section.identifier, "Description", 1)] = AbilityTarget(
            assignment=section.description,
            compiled_value=compiled_value,
            compiled_path=(section.description.section_index, "@real_description"),
            message_key=message_key,
            message_value=message_value,
            message_path=(ABILITY_DESCRIPTION_MESSAGES_INDEX, "entry", message_index),
            source_usage_count=source_counts[section.description.source],
            runtime_key_reference_count=key_refs,
            runtime_value_reference_count=value_refs,
        )
    return _AbilityAnalysis(
        pbs,
        compiled_root,
        messages_root,
        targets,
        graph_sha256(compiled_root),
        graph_sha256(messages_root),
    )


def _pbs_proof(analysis: _AbilityAnalysis, target: AbilityTarget) -> str:
    assignment = target.assignment
    proof = {
        "format": ABILITY_PBS_PROOF_FORMAT,
        "pbs_file": ABILITY_PBS_FILE,
        "file_sha256": analysis.pbs.file_sha256,
        "encoding": "utf-8-sig",
        "bom": "utf-8",
        "newline": "CRLF",
        "line_number": assignment.line_number,
        "section_index": assignment.section_index,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "key": "Description",
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
    analysis: _AbilityAnalysis,
    target: AbilityTarget,
    compiled_raw: bytes,
) -> str:
    proof = {
        "format": COMPILED_ABILITY_PROOF_FORMAT,
        "compiled_file": COMPILED_ABILITY_FILE,
        "file_sha256": _sha256(compiled_raw),
        "root_type": "Hash",
        "root_size": len(analysis.compiled_root),
        "root_graph_sha256": analysis.compiled_graph_sha256,
        "non_target_section_graph_sha256": graph_sha256(
            list(analysis.compiled_root.values())[target.assignment.section_index],
            masked=(target.compiled_value,),
        ),
        "section_index": target.assignment.section_index,
        "section_sha256": _sha256(target.assignment.section.encode("utf-8")),
        "section_class": ABILITY_CLASS,
        "section_ivars": list(ABILITY_IVARS),
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.compiled_value.ivars),
        "target_value_sha256": _sha256(target.compiled_value.data),
        "target_reference_count": 1,
        "compiled_path": list(target.compiled_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _AbilityAnalysis,
    target: AbilityTarget,
    messages_raw: bytes,
) -> str:
    proof = {
        "format": ABILITY_RUNTIME_PROOF_FORMAT,
        "runtime_file": ABILITY_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "message_type_index": ABILITY_DESCRIPTION_MESSAGES_INDEX,
        "message_count": len(analysis.messages_root[ABILITY_DESCRIPTION_MESSAGES_INDEX]),
        "root_graph_sha256": analysis.messages_graph_sha256,
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


def build_ability_description_proofs(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> dict[tuple[str, str, int], AbilityEntryProof]:
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    return {
        key: AbilityEntryProof(
            source=target.assignment.source,
            pbs_structure=_pbs_proof(analysis, target),
            compiled_path=json.dumps(list(target.compiled_path), separators=(",", ":")),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(list(target.message_path), separators=(",", ":")),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
        for key, target in analysis.targets.items()
    }


def rebuild_ability_description_payloads(
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
        raise AbilityIntegrityError("La traduction de capacité ne tient pas sur une ligne PBS.")
    translated = translation.encode("utf-8")
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    target_key = (section, "Description", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise AbilityIntegrityError("La description de capacité ne correspond plus à la source.")
    expected = build_ability_description_proofs(pbs_raw, compiled_raw, messages_raw)[target_key]
    if (
        expected.pbs_structure != pbs_structure
        or expected.compiled_path != compiled_path
        or expected.compiled_structure != compiled_structure
        or expected.runtime_path != runtime_path
        or expected.runtime_structure != runtime_structure
    ):
        raise AbilityIntegrityError("La preuve de capacité ne correspond plus aux trois sources.")
    if target.source_usage_count != 1:
        raise AbilityIntegrityError(
            "Cette description est partagée par plusieurs capacités et reste bloquée."
        )
    if target.runtime_key_reference_count != 1 or target.runtime_value_reference_count != 1:
        raise AbilityIntegrityError("Cette description partage un objet Marshal et reste bloquée.")
    if json.loads(runtime_structure).get("target_value_equals_source") is not True:
        raise AbilityIntegrityError("La banque possède déjà une traduction différente.")
    if translation in {
        item.text()
        for item in analysis.messages_root[ABILITY_DESCRIPTION_MESSAGES_INDEX]
        if item is not target.message_key
    }:
        raise AbilityIntegrityError("La traduction entrerait en collision avec une autre clé.")

    assignment = target.assignment
    pbs_lines = list(analysis.pbs.content_lines)
    pbs_lines[assignment.line_index] = (
        assignment.prefix + translation + assignment.trailing + assignment.newline
    )
    rebuilt_pbs = b"\xef\xbb\xbf" + "".join(pbs_lines).encode("utf-8")

    compiled_before = graph_sha256(
        analysis.compiled_root, masked=(target.compiled_value,)
    )
    compiled_object = list(analysis.compiled_root.values())[assignment.section_index]
    compiled_object.ivars["@real_description"] = RubyString(
        translated, dict(target.compiled_value.ivars)
    )
    rebuilt_compiled_target = compiled_object.ivars["@real_description"]
    if graph_sha256(
        analysis.compiled_root, masked=(rebuilt_compiled_target,)
    ) != compiled_before:
        raise AbilityIntegrityError("La mutation modifierait abilities.dat hors cible.")
    rebuilt_compiled = dumps(analysis.compiled_root)

    runtime_before = graph_sha256(
        analysis.messages_root, masked=(target.message_key, target.message_value)
    )
    replacement_hash: dict = {}
    old_hash = analysis.messages_root[ABILITY_DESCRIPTION_MESSAGES_INDEX]
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_hash[RubyString(translated, dict(old_key.ivars))] = RubyString(
                translated, dict(old_value.ivars)
            )
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[ABILITY_DESCRIPTION_MESSAGES_INDEX] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[target.message_path[-1]]
    if graph_sha256(
        analysis.messages_root, masked=(rebuilt_key, rebuilt_value)
    ) != runtime_before:
        raise AbilityIntegrityError("La mutation modifierait la banque core hors cible.")
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
        raise AbilityIntegrityError("La relecture ne retrouve pas la description exacte.")
    return {
        ABILITY_PBS_FILE: rebuilt_pbs,
        COMPILED_ABILITY_FILE: rebuilt_compiled,
        ABILITY_MESSAGES_FILE: rebuilt_messages,
    }


def extract_ability_description_texts(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
) -> tuple[str, str, str]:
    target = _analyze_sources(pbs_raw, compiled_raw, messages_raw).targets.get(
        (section, "Description", 1)
    )
    if target is None:
        raise AbilityIntegrityError("La description de capacité est introuvable.")
    return (
        target.assignment.source,
        target.compiled_value.text(),
        target.message_value.text(),
    )
