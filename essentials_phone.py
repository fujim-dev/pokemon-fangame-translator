# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée des messages téléphone v21.1.

Le module ne lance jamais Ruby. Il relie une affectation de ``PBS/phone.txt``
à ``Data/phone.dat`` puis à la banque ``PHONE_MESSAGES`` de
``Data/messages_game.dat``. Toute structure non exactement reconnue est refusée.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import re

from ruby_marshal_reader import (
    MarshalReader,
    RubyHashKey,
    RubyObject,
    RubyString,
    RubyUserDefined,
)
from ruby_marshal_writer import dumps


PHONE_PBS_FILE = "PBS/phone.txt"
COMPILED_PHONE_FILE = "Data/phone.dat"
PHONE_MESSAGES_FILE = "Data/messages_game.dat"
PHONE_MESSAGES_INDEX = 22
PHONE_CLASS = "GameData::PhoneMessage"
PHONE_PBS_PROOF_FORMAT = "pft_v21_1_phone_pbs_v1"
COMPILED_PHONE_PROOF_FORMAT = "pft_v21_1_compiled_phone_v1"
PHONE_RUNTIME_PROOF_FORMAT = "pft_v21_1_phone_messages_runtime_v1"
PHONE_IVARS = (
    "@id",
    "@trainer_type",
    "@real_name",
    "@version",
    "@intro",
    "@intro_morning",
    "@intro_afternoon",
    "@intro_evening",
    "@body",
    "@body1",
    "@body2",
    "@battle_request",
    "@battle_remind",
    "@end",
    "@pbs_file_suffix",
)
PHONE_SCHEMA = {
    "Intro": "@intro",
    "IntroMorning": "@intro_morning",
    "IntroAfternoon": "@intro_afternoon",
    "IntroEvening": "@intro_evening",
    "Body": "@body",
    "Body1": "@body1",
    "Body2": "@body2",
    "BattleRequest": "@battle_request",
    "BattleRemind": "@battle_remind",
    "End": "@end",
}


class PhoneIntegrityError(ValueError):
    """La triple correspondance téléphone n'est pas démontrable."""


@dataclass(frozen=True)
class PhonePbsAssignment:
    section: str
    section_index: int
    key: str
    occurrence: int
    line_index: int
    line_number: int
    prefix: str
    source: str
    trailing: str
    newline: str


@dataclass(frozen=True)
class PhonePbsSection:
    name: str
    compiled_id: tuple[object, ...]
    assignments: tuple[PhonePbsAssignment, ...]


@dataclass(frozen=True)
class PhonePbsDocument:
    content_lines: tuple[str, ...]
    sections: tuple[PhonePbsSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class PhoneTarget:
    assignment: PhonePbsAssignment
    phone_value: RubyString
    phone_path: tuple[object, ...]
    message_key: RubyString
    message_value: RubyString
    message_path: tuple[object, ...]


@dataclass(frozen=True)
class PhoneEntryProof:
    source: str
    pbs_structure: str
    compiled_path: str
    compiled_structure: str
    runtime_path: str
    runtime_structure: str


@dataclass
class _PhoneAnalysis:
    pbs: PhonePbsDocument
    phone_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], PhoneTarget]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_marshal(raw: bytes, label: str) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise PhoneIntegrityError(f"{label} n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise PhoneIntegrityError(f"{label} est illisible sans exécuter Ruby.") from exc
    if reader.pos != len(raw) or dumps(root) != raw:
        raise PhoneIntegrityError(
            f"Le lecteur/écrivain Marshal ne reproduit pas exactement {label}."
        )
    return root


def _ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise PhoneIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhoneIntegrityError(f"{label} n'est pas une chaîne UTF-8 valide.") from exc
    return value


def _section_id(raw_name: str) -> tuple[object, ...]:
    if raw_name.casefold() == "default":
        return ("default",)
    if '"' in raw_name:
        raise PhoneIntegrityError("Une section téléphone citée reste volontairement bloquée.")
    try:
        fields = next(csv.reader([raw_name], skipinitialspace=False))
    except (csv.Error, StopIteration) as exc:
        raise PhoneIntegrityError("Identifiant de section téléphone illisible.") from exc
    if len(fields) not in {2, 3} or any(field != field.strip() for field in fields):
        raise PhoneIntegrityError("Identifiant de section téléphone ambigu.")
    trainer_type, real_name = fields[:2]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", trainer_type) or not real_name:
        raise PhoneIntegrityError("Type ou nom de contact téléphone invalide.")
    version_text = fields[2] if len(fields) == 3 else "0"
    if not re.fullmatch(r"0|[1-9]\d*", version_text):
        raise PhoneIntegrityError("Version de contact téléphone non canonique.")
    return trainer_type, real_name, int(version_text)


def _parse_assignment(body: str) -> tuple[str, str, str, str] | None:
    separator = body.find("=")
    if separator < 0:
        return None
    raw_key = body[:separator]
    key = raw_key.strip()
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


def parse_phone_pbs(raw: bytes) -> PhonePbsDocument:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise PhoneIntegrityError("PBS/phone.txt doit conserver son BOM UTF-8.")
    payload = raw[3:]
    if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(b"\r\n", b""):
        raise PhoneIntegrityError("PBS/phone.txt doit conserver exclusivement ses CRLF.")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhoneIntegrityError("PBS/phone.txt n'est pas un UTF-8 valide.") from exc
    lines = tuple(content.splitlines(keepends=True))
    if not lines or any(not line.endswith("\r\n") for line in lines):
        raise PhoneIntegrityError("Chaque ligne de PBS/phone.txt doit conserver son CRLF.")

    sections: list[PhonePbsSection] = []
    section_name = ""
    section_index = -1
    section_assignments: list[PhonePbsAssignment] = []
    occurrences: dict[str, int] = {}

    def finish_section() -> None:
        nonlocal section_assignments
        if section_index < 0:
            return
        if not section_assignments:
            raise PhoneIntegrityError("Une section téléphone ne contient aucun message.")
        sections.append(
            PhonePbsSection(
                name=section_name,
                compiled_id=_section_id(section_name),
                assignments=tuple(section_assignments),
            )
        )
        section_assignments = []

    for line_index, raw_line in enumerate(lines):
        body = raw_line[:-2]
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        section_match = re.fullmatch(r"\[([^\]]+)\]", stripped)
        if section_match:
            finish_section()
            section_index += 1
            section_name = section_match.group(1).strip()
            if not section_name:
                raise PhoneIntegrityError("Section téléphone vide.")
            occurrences = {}
            continue
        if section_index < 0:
            raise PhoneIntegrityError("Affectation téléphone située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise PhoneIntegrityError("Ligne téléphone non reconnue.")
        prefix, key, source, trailing = parsed
        if key not in PHONE_SCHEMA or not source or source != source.strip():
            raise PhoneIntegrityError("Clé ou texte téléphone non reconnu exactement.")
        occurrences[key] = occurrences.get(key, 0) + 1
        section_assignments.append(
            PhonePbsAssignment(
                section=section_name,
                section_index=section_index,
                key=key,
                occurrence=occurrences[key],
                line_index=line_index,
                line_number=line_index + 1,
                prefix=prefix,
                source=source,
                trailing=trailing,
                newline="\r\n",
            )
        )
    finish_section()
    if not sections or len({section.compiled_id for section in sections}) != len(sections):
        raise PhoneIntegrityError("Sections téléphone absentes ou dupliquées.")
    return PhonePbsDocument(
        content_lines=lines,
        sections=tuple(sections),
        file_sha256=_sha256(raw),
    )


def _graph_payload(value: object, *, masked: frozenset[int] = frozenset()) -> bytes:
    identifiers: dict[int, int] = {}

    def encode(current: object) -> object:
        if isinstance(current, bool):
            return ["bool", current]
        if current is None:
            return ["nil"]
        if type(current) is int:
            return ["int", current]
        if isinstance(current, float):
            return ["float", repr(current)]
        if isinstance(current, str):
            return ["symbol", current]
        if isinstance(current, RubyHashKey):
            return ["RubyHashKey", encode(current.value)]
        if isinstance(current, tuple) and len(current) == 3 and current[0] == "regexp":
            return ["regexp", bytes(current[1]).hex(), int(current[2])]
        if isinstance(current, (RubyString, RubyUserDefined, RubyObject, list, dict)):
            identity = id(current)
            previous = identifiers.get(identity)
            if previous is not None:
                return ["ref", previous]
            object_id = len(identifiers)
            identifiers[identity] = object_id
            if isinstance(current, RubyString):
                digest = "TARGET" if identity in masked else _sha256(current.data)
                return [
                    "RubyString",
                    object_id,
                    digest,
                    [[encode(key), encode(child)] for key, child in current.ivars.items()],
                ]
            if isinstance(current, RubyUserDefined):
                digest = "TARGET" if identity in masked else _sha256(current.data)
                return [
                    "RubyUserDefined",
                    object_id,
                    current.class_name,
                    digest,
                    [[encode(key), encode(child)] for key, child in current.ivars.items()],
                ]
            if isinstance(current, RubyObject):
                return [
                    "RubyObject",
                    object_id,
                    current.class_name,
                    [[encode(key), encode(child)] for key, child in current.ivars.items()],
                ]
            if isinstance(current, list):
                return ["Array", object_id, [encode(child) for child in current]]
            return [
                "Hash",
                object_id,
                [[encode(key), encode(child)] for key, child in current.items()],
            ]
        raise PhoneIntegrityError(
            f"Type Marshal téléphone non pris en charge : {type(current).__name__}."
        )

    return json.dumps(encode(value), ensure_ascii=True, separators=(",", ":")).encode("ascii")


def graph_sha256(value: object, *, masked: tuple[object, ...] = ()) -> str:
    return _sha256(_graph_payload(value, masked=frozenset(id(item) for item in masked)))


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
        if isinstance(current, (RubyString, RubyUserDefined)):
            return count + sum(visit(key) + visit(child) for key, child in current.ivars.items())
        if isinstance(current, RubyObject):
            return count + sum(visit(key) + visit(child) for key, child in current.ivars.items())
        if isinstance(current, list):
            return count + sum(visit(child) for child in current)
        if isinstance(current, dict):
            return count + sum(visit(key) + visit(child) for key, child in current.items())
        return count

    return visit(value)


def _key_id(key: object) -> tuple[object, ...]:
    if isinstance(key, RubyString):
        return (_ruby_text(key, "La clé téléphone par défaut").text(),)
    if isinstance(key, RubyHashKey) and isinstance(key.value, list) and len(key.value) == 3:
        trainer_type, real_name, version = key.value
        if not isinstance(trainer_type, str) or type(version) is not int:
            raise PhoneIntegrityError("Clé composée téléphone invalide.")
        return trainer_type, _ruby_text(real_name, "Le nom de contact").text(), version
    raise PhoneIntegrityError("Type de clé téléphone non reconnu.")


def _validate_phone_object(
    key: object,
    section: PhonePbsSection,
    value: object,
) -> RubyObject:
    if not isinstance(value, RubyObject) or value.class_name != PHONE_CLASS:
        raise PhoneIntegrityError("Une entrée compilée n'est pas GameData::PhoneMessage.")
    if tuple(value.ivars) != PHONE_IVARS or _key_id(key) != section.compiled_id:
        raise PhoneIntegrityError("La structure ou l'identité compilée du contact a changé.")
    expected_id = section.compiled_id
    if len(expected_id) == 1:
        identifier = _ruby_text(value.ivars["@id"], "L'identifiant téléphone").text()
        trainer_type = _ruby_text(value.ivars["@trainer_type"], "Le type par défaut").text()
        if identifier != "default" or trainer_type != "default" or value.ivars["@real_name"] is not None:
            raise PhoneIntegrityError("Le contact téléphone par défaut est incohérent.")
    else:
        identifier = value.ivars["@id"]
        if not isinstance(identifier, list) or len(identifier) != 3:
            raise PhoneIntegrityError("L'identifiant composé du contact a changé.")
        actual_id = (
            identifier[0],
            _ruby_text(identifier[1], "Le nom de l'identifiant composé").text(),
            identifier[2],
        )
        if (
            actual_id != expected_id
            or value.ivars["@trainer_type"] != expected_id[0]
            or _ruby_text(value.ivars["@real_name"], "Le nom réel du contact").text()
            != expected_id[1]
        ):
            raise PhoneIntegrityError("Les métadonnées du contact ne correspondent plus au PBS.")
    expected_version = expected_id[2] if len(expected_id) == 3 else 0
    if (
        type(value.ivars["@version"]) is not int
        or value.ivars["@version"] != expected_version
    ):
        raise PhoneIntegrityError("La version compilée du contact a changé.")
    if _ruby_text(value.ivars["@pbs_file_suffix"], "Le suffixe PBS téléphone").text() != "":
        raise PhoneIntegrityError("Le suffixe PBS téléphone inattendu reste bloqué.")

    expected_by_key = {
        key_name: [assignment.source for assignment in section.assignments if assignment.key == key_name]
        for key_name in PHONE_SCHEMA
    }
    for key_name, ivar in PHONE_SCHEMA.items():
        compiled = value.ivars[ivar]
        expected = expected_by_key[key_name]
        if not expected:
            if compiled is not None:
                raise PhoneIntegrityError("Un champ absent du PBS est présent dans phone.dat.")
            continue
        if not isinstance(compiled, list) or len(compiled) != len(expected):
            raise PhoneIntegrityError("Le nombre de messages compilés ne correspond plus au PBS.")
        actual = [
            _ruby_text(item, f"Le message compilé {key_name}").text()
            for item in compiled
        ]
        if actual != expected:
            raise PhoneIntegrityError("L'ordre ou le texte compilé ne correspond plus au PBS.")
    return value


def _analyze_sources(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _PhoneAnalysis:
    pbs = parse_phone_pbs(pbs_raw)
    phone_root = _load_marshal(compiled_raw, COMPILED_PHONE_FILE)
    if not isinstance(phone_root, dict) or len(phone_root) != len(pbs.sections):
        raise PhoneIntegrityError("La racine de Data/phone.dat ne correspond pas aux sections PBS.")
    compiled_items = list(phone_root.items())
    targets: dict[tuple[str, str, int], PhoneTarget] = {}
    flattened: list[tuple[PhonePbsAssignment, RubyString, tuple[object, ...]]] = []
    for section_index, section in enumerate(pbs.sections):
        key, raw_object = compiled_items[section_index]
        phone_object = _validate_phone_object(key, section, raw_object)
        for key_name, ivar in PHONE_SCHEMA.items():
            assignments = [item for item in section.assignments if item.key == key_name]
            if not assignments:
                continue
            compiled_messages = phone_object.ivars[ivar]
            for array_index, assignment in enumerate(assignments):
                phone_value = compiled_messages[array_index]
                if _reference_count(phone_root, phone_value) != 1:
                    raise PhoneIntegrityError("Une chaîne phone.dat est partagée ou ambiguë.")
                flattened.append(
                    (assignment, phone_value, (section_index, ivar, array_index))
                )

    messages_root = _load_marshal(messages_raw, PHONE_MESSAGES_FILE)
    if (
        not isinstance(messages_root, list)
        or len(messages_root) <= PHONE_MESSAGES_INDEX
        or not isinstance(messages_root[PHONE_MESSAGES_INDEX], dict)
    ):
        raise PhoneIntegrityError("La banque PHONE_MESSAGES n'est pas à l'index v21.1 attendu.")
    message_hash = messages_root[PHONE_MESSAGES_INDEX]
    message_items = list(message_hash.items())
    if len(message_items) != len(flattened):
        raise PhoneIntegrityError("La banque PHONE_MESSAGES ne couvre pas exactement phone.dat.")
    source_values = [assignment.source for assignment, _value, _path in flattened]
    if len(set(source_values)) != len(source_values):
        raise PhoneIntegrityError("Les messages téléphone du corpus ne sont pas uniques.")

    for message_index, ((assignment, phone_value, phone_path), (key, value)) in enumerate(
        zip(flattened, message_items)
    ):
        message_key = _ruby_text(key, "La clé PHONE_MESSAGES")
        message_value = _ruby_text(value, "La valeur PHONE_MESSAGES")
        if message_key.text() != assignment.source:
            raise PhoneIntegrityError("L'ordre de PHONE_MESSAGES ne correspond plus à phone.dat.")
        if _reference_count(messages_root, message_key) != 1 or _reference_count(messages_root, message_value) != 1:
            raise PhoneIntegrityError("Une chaîne PHONE_MESSAGES est partagée ou ambiguë.")
        target_key = (assignment.section, assignment.key, assignment.occurrence)
        targets[target_key] = PhoneTarget(
            assignment=assignment,
            phone_value=phone_value,
            phone_path=phone_path,
            message_key=message_key,
            message_value=message_value,
            message_path=(PHONE_MESSAGES_INDEX, "entry", message_index),
        )
    return _PhoneAnalysis(
        pbs=pbs,
        phone_root=phone_root,
        messages_root=messages_root,
        targets=targets,
    )


def _pbs_proof(analysis: _PhoneAnalysis, target: PhoneTarget) -> str:
    assignment = target.assignment
    line = analysis.pbs.content_lines[assignment.line_index]
    proof = {
        "format": PHONE_PBS_PROOF_FORMAT,
        "pbs_file": PHONE_PBS_FILE,
        "file_sha256": analysis.pbs.file_sha256,
        "encoding": "utf-8-sig",
        "bom": "utf-8",
        "newline": "CRLF",
        "line_number": assignment.line_number,
        "section_index": assignment.section_index,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "key": assignment.key,
        "key_occurrence": assignment.occurrence,
        "prefix_sha256": _sha256(assignment.prefix.encode("utf-8")),
        "trailing_sha256": _sha256(assignment.trailing.encode("utf-8")),
        "line_sha256": _sha256(line.encode("utf-8")),
        "source_sha256": _sha256(assignment.source.encode("utf-8")),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _compiled_proof(
    analysis: _PhoneAnalysis,
    target: PhoneTarget,
    compiled_raw: bytes,
) -> str:
    proof = {
        "format": COMPILED_PHONE_PROOF_FORMAT,
        "compiled_file": COMPILED_PHONE_FILE,
        "file_sha256": _sha256(compiled_raw),
        "root_type": "Hash",
        "root_size": len(analysis.phone_root),
        "root_graph_sha256": graph_sha256(analysis.phone_root),
        "non_target_graph_sha256": graph_sha256(
            analysis.phone_root, masked=(target.phone_value,)
        ),
        "section_index": target.assignment.section_index,
        "section_sha256": _sha256(target.assignment.section.encode("utf-8")),
        "section_class": PHONE_CLASS,
        "section_ivars": list(PHONE_IVARS),
        "key": target.assignment.key,
        "key_occurrence": target.assignment.occurrence,
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.phone_value.ivars),
        "target_value_sha256": _sha256(target.phone_value.data),
        "target_reference_count": 1,
        "compiled_path": list(target.phone_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _PhoneAnalysis,
    target: PhoneTarget,
    messages_raw: bytes,
) -> str:
    proof = {
        "format": PHONE_RUNTIME_PROOF_FORMAT,
        "runtime_file": PHONE_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "message_type_index": PHONE_MESSAGES_INDEX,
        "message_count": len(analysis.messages_root[PHONE_MESSAGES_INDEX]),
        "root_graph_sha256": graph_sha256(analysis.messages_root),
        "non_target_graph_sha256": graph_sha256(
            analysis.messages_root,
            masked=(target.message_key, target.message_value),
        ),
        "target_key_type": "RubyString",
        "target_value_type": "RubyString",
        "target_key_ivars_sha256": graph_sha256(target.message_key.ivars),
        "target_value_ivars_sha256": graph_sha256(target.message_value.ivars),
        "target_key_sha256": _sha256(target.message_key.data),
        "target_value_sha256": _sha256(target.message_value.data),
        "target_value_equals_source": target.message_value.text() == target.assignment.source,
        "target_key_reference_count": 1,
        "target_value_reference_count": 1,
        "runtime_path": list(target.message_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def build_phone_entry_proofs(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> dict[tuple[str, str, int], PhoneEntryProof]:
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    result: dict[tuple[str, str, int], PhoneEntryProof] = {}
    for key, target in analysis.targets.items():
        result[key] = PhoneEntryProof(
            source=target.assignment.source,
            pbs_structure=_pbs_proof(analysis, target),
            compiled_path=json.dumps(list(target.phone_path), separators=(",", ":")),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(list(target.message_path), separators=(",", ":")),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
    return result


def rebuild_phone_payloads(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
    key: str,
    occurrence: int,
    source: str,
    translation: str,
    pbs_structure: str,
    compiled_path: str,
    compiled_structure: str,
    runtime_path: str,
    runtime_structure: str,
) -> dict[str, bytes]:
    if not translation or any(character in translation for character in ("\r", "\n")):
        raise PhoneIntegrityError("La traduction téléphone ne tient pas sur une ligne PBS.")
    try:
        translated = translation.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PhoneIntegrityError("La traduction téléphone n'est pas encodable en UTF-8.") from exc
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    target_key = (section, key, occurrence)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise PhoneIntegrityError("L'occurrence téléphone ciblée ne correspond plus à la source.")
    expected = build_phone_entry_proofs(pbs_raw, compiled_raw, messages_raw)[target_key]
    if (
        expected.pbs_structure != pbs_structure
        or expected.compiled_path != compiled_path
        or expected.compiled_structure != compiled_structure
        or expected.runtime_path != runtime_path
        or expected.runtime_structure != runtime_structure
    ):
        raise PhoneIntegrityError("La preuve téléphone ne correspond plus aux trois sources.")
    runtime_proof = json.loads(runtime_structure)
    if runtime_proof.get("target_value_equals_source") is not True:
        raise PhoneIntegrityError("Le message téléphone possède déjà une traduction différente.")
    other_runtime_keys = {
        item.text()
        for item in analysis.messages_root[PHONE_MESSAGES_INDEX]
        if item is not target.message_key
    }
    if translation in other_runtime_keys:
        raise PhoneIntegrityError("La traduction téléphone entrerait en collision avec une autre clé.")

    pbs_lines = list(analysis.pbs.content_lines)
    assignment = target.assignment
    pbs_lines[assignment.line_index] = (
        assignment.prefix + translation + assignment.trailing + assignment.newline
    )
    rebuilt_pbs = b"\xef\xbb\xbf" + "".join(pbs_lines).encode("utf-8")

    phone_before = graph_sha256(analysis.phone_root, masked=(target.phone_value,))
    phone_object = list(analysis.phone_root.values())[assignment.section_index]
    ivar = PHONE_SCHEMA[key]
    phone_object.ivars[ivar][occurrence - 1] = RubyString(
        translated, dict(target.phone_value.ivars)
    )
    rebuilt_phone_target = phone_object.ivars[ivar][occurrence - 1]
    if graph_sha256(analysis.phone_root, masked=(rebuilt_phone_target,)) != phone_before:
        raise PhoneIntegrityError("La mutation modifierait phone.dat hors chaîne ciblée.")
    rebuilt_compiled = dumps(analysis.phone_root)

    runtime_before = graph_sha256(
        analysis.messages_root,
        masked=(target.message_key, target.message_value),
    )
    old_hash = analysis.messages_root[PHONE_MESSAGES_INDEX]
    replacement_hash: dict = {}
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_key = RubyString(translated, dict(target.message_key.ivars))
            replacement_value = RubyString(translated, dict(target.message_value.ivars))
            replacement_hash[replacement_key] = replacement_value
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[PHONE_MESSAGES_INDEX] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[target.message_path[-1]]
    if graph_sha256(
        analysis.messages_root,
        masked=(rebuilt_key, rebuilt_value),
    ) != runtime_before:
        raise PhoneIntegrityError("La mutation modifierait PHONE_MESSAGES hors cible.")
    rebuilt_messages = dumps(analysis.messages_root)

    rebuilt_analysis = _analyze_sources(rebuilt_pbs, rebuilt_compiled, rebuilt_messages)
    rebuilt_target = rebuilt_analysis.targets.get(target_key)
    if (
        rebuilt_target is None
        or rebuilt_target.assignment.source != translation
        or rebuilt_target.phone_value.text() != translation
        or rebuilt_target.message_key.text() != translation
        or rebuilt_target.message_value.text() != translation
        or rebuilt_target.phone_path != target.phone_path
        or rebuilt_target.message_path != target.message_path
    ):
        raise PhoneIntegrityError("La relecture ne retrouve pas la traduction téléphone exacte.")
    return {
        PHONE_PBS_FILE: rebuilt_pbs,
        COMPILED_PHONE_FILE: rebuilt_compiled,
        PHONE_MESSAGES_FILE: rebuilt_messages,
    }


def extract_phone_target_texts(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
    key: str,
    occurrence: int,
) -> tuple[str, str, str]:
    target = _analyze_sources(pbs_raw, compiled_raw, messages_raw).targets.get(
        (section, key, occurrence)
    )
    if target is None:
        raise PhoneIntegrityError("L'occurrence téléphone est introuvable.")
    return (
        target.assignment.source,
        target.phone_value.text(),
        target.message_value.text(),
    )
