# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée d'une description de Move v21.1.

Le module ne lance jamais Ruby. Il relie les champs réellement textuels de
``PBS/moves.txt`` aux objets ``GameData::Move`` de ``Data/moves.dat`` puis aux
banques ``MOVE_NAMES`` et ``MOVE_DESCRIPTIONS`` de
``Data/messages_core.dat``. ``Category`` est validé comme enum technique et ne
devient jamais une occurrence traduisible.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
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


MOVE_PBS_FILE = "PBS/moves.txt"
COMPILED_MOVE_FILE = "Data/moves.dat"
MOVE_MESSAGES_FILE = "Data/messages_core.dat"
MOVE_NAME_MESSAGES_INDEX = 5
MOVE_DESCRIPTION_MESSAGES_INDEX = 6
MOVE_CLASS = "GameData::Move"
MOVE_PBS_PROOF_FORMAT = "pft_v21_1_move_pbs_v1"
COMPILED_MOVE_PROOF_FORMAT = "pft_v21_1_compiled_move_v1"
MOVE_RUNTIME_PROOF_FORMAT = "pft_v21_1_move_runtime_v1"
MOVE_CATEGORY_CODES = {"Physical": 0, "Special": 1, "Status": 2}
MOVE_TEXT_FIELDS = {
    "Name": ("@real_name", MOVE_NAME_MESSAGES_INDEX),
    "Description": ("@real_description", MOVE_DESCRIPTION_MESSAGES_INDEX),
}
MOVE_IVARS = (
    "@id",
    "@real_name",
    "@type",
    "@category",
    "@power",
    "@accuracy",
    "@total_pp",
    "@target",
    "@priority",
    "@function_code",
    "@flags",
    "@effect_chance",
    "@real_description",
    "@pbs_file_suffix",
)
MOVE_PBS_KEYS = frozenset(
    {
        "Name",
        "Type",
        "Category",
        "Power",
        "Accuracy",
        "TotalPP",
        "Target",
        "Priority",
        "FunctionCode",
        "Flags",
        "EffectChance",
        "Description",
    }
)


class MoveIntegrityError(ValueError):
    """La triple correspondance d'un texte de Move n'est pas démontrable."""


@dataclass(frozen=True)
class MovePbsAssignment:
    section: str
    section_index: int
    line_index: int
    line_number: int
    key: str
    prefix: str
    source: str
    trailing: str
    newline: str


@dataclass(frozen=True)
class MovePbsSection:
    identifier: str
    name: MovePbsAssignment
    move_type: str
    category_label: str
    category_code: int
    power: int
    accuracy: int
    total_pp: int
    target: str
    priority: int
    function_code: str
    flags: tuple[str, ...]
    effect_chance: int
    description: MovePbsAssignment


@dataclass(frozen=True)
class MovePbsDocument:
    content_lines: tuple[str, ...]
    sections: tuple[MovePbsSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class MoveTarget:
    field: str
    assignment: MovePbsAssignment
    compiled_value: RubyString
    compiled_path: tuple[object, ...]
    compiled_reference_count: int
    message_key: RubyString
    message_value: RubyString
    message_path: tuple[object, ...]
    source_usage_count: int
    runtime_key_reference_count: int
    runtime_value_reference_count: int


@dataclass(frozen=True)
class MoveEntryProof:
    source: str
    pbs_structure: str
    compiled_path: str
    compiled_structure: str
    runtime_path: str
    runtime_structure: str


@dataclass
class _MoveAnalysis:
    pbs: MovePbsDocument
    compiled_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], MoveTarget]
    compiled_graph_sha256: str
    messages_graph_sha256: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_marshal(raw: bytes, label: str) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise MoveIntegrityError(f"{label} n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise MoveIntegrityError(f"{label} est illisible sans exécuter Ruby.") from exc
    if reader.pos != len(raw) or dumps(root) != raw:
        raise MoveIntegrityError(
            f"Le lecteur/écrivain Marshal ne reproduit pas exactement {label}."
        )
    return root


def _ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise MoveIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MoveIntegrityError(f"{label} n'est pas une chaîne UTF-8 valide.") from exc
    return value


def _reference_counts(value: object) -> Counter[int]:
    """Compte chaque reference en une seule traversee du graphe."""
    counts: Counter[int] = Counter()
    seen: set[int] = set()

    def visit(current: object) -> None:
        counts[id(current)] += 1
        if isinstance(current, RubyHashKey):
            visit(current.value)
            return
        if isinstance(current, (RubyString, RubyUserDefined, RubyObject, list, dict)):
            identity = id(current)
            if identity in seen:
                return
            seen.add(identity)
        if isinstance(current, (RubyString, RubyUserDefined, RubyObject)):
            for key, child in current.ivars.items():
                visit(key)
                visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)
        elif isinstance(current, dict):
            for key, child in current.items():
                visit(key)
                visit(child)

    visit(value)
    return counts


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


def _canonical_text(value: str, label: str) -> str:
    if not value or value != value.strip():
        raise MoveIntegrityError(f"{label} est absent ou ambigu.")
    return value


def _token(value: str, label: str, *, uppercase: bool = False) -> str:
    value = _canonical_text(value, label)
    pattern = r"[A-Z][A-Z0-9_]*" if uppercase else r"[A-Za-z0-9_]+"
    if re.fullmatch(pattern, value) is None:
        raise MoveIntegrityError(f"{label} n'est pas canonique.")
    return value


def _unsigned(value: str, label: str) -> int:
    value = _canonical_text(value, label)
    if re.fullmatch(r"0|[1-9]\d*", value) is None:
        raise MoveIntegrityError(f"{label} n'est pas un entier non signé canonique.")
    return int(value)


def _signed(value: str, label: str) -> int:
    value = _canonical_text(value, label)
    if re.fullmatch(r"0|-?[1-9]\d*", value) is None:
        raise MoveIntegrityError(f"{label} n'est pas un entier signé canonique.")
    return int(value)


def _flags(value: str) -> tuple[str, ...]:
    value = _canonical_text(value, "Flags de Move")
    parsed = tuple(value.split(","))
    if any(
        flag != flag.strip()
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", flag) is None
        for flag in parsed
    ):
        raise MoveIntegrityError("Flags de Move non canoniques.")
    return parsed


def parse_move_pbs(raw: bytes) -> MovePbsDocument:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise MoveIntegrityError("PBS/moves.txt doit conserver son BOM UTF-8.")
    payload = raw[3:]
    if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(b"\r\n", b""):
        raise MoveIntegrityError("PBS/moves.txt doit conserver exclusivement ses CRLF.")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MoveIntegrityError("PBS/moves.txt n'est pas un UTF-8 valide.") from exc
    lines = tuple(content.splitlines(keepends=True))
    if not lines or any(not line.endswith("\r\n") for line in lines):
        raise MoveIntegrityError("Chaque ligne de PBS/moves.txt doit conserver son CRLF.")

    sections: list[MovePbsSection] = []
    identifier = ""
    section_index = -1
    assignments: dict[str, MovePbsAssignment] = {}

    def finish_section() -> None:
        nonlocal assignments
        if section_index < 0:
            return
        required = {
            "Name",
            "Type",
            "Category",
            "Accuracy",
            "TotalPP",
            "Target",
            "FunctionCode",
            "Description",
        }
        if not required.issubset(assignments):
            raise MoveIntegrityError("Une Move ne possède pas son schéma PBS minimal exact.")
        category_label = _canonical_text(
            assignments["Category"].source,
            "Category de Move",
        )
        if category_label not in MOVE_CATEGORY_CODES:
            raise MoveIntegrityError("Category de Move n'est pas un enum technique reconnu.")
        sections.append(
            MovePbsSection(
                identifier=identifier,
                name=assignments["Name"],
                move_type=_token(assignments["Type"].source, "Type de Move", uppercase=True),
                category_label=category_label,
                category_code=MOVE_CATEGORY_CODES[category_label],
                power=_unsigned(assignments["Power"].source, "Power de Move")
                if "Power" in assignments
                else 0,
                accuracy=_unsigned(assignments["Accuracy"].source, "Accuracy de Move"),
                total_pp=_unsigned(assignments["TotalPP"].source, "TotalPP de Move"),
                target=_token(assignments["Target"].source, "Target de Move"),
                priority=_signed(assignments["Priority"].source, "Priority de Move")
                if "Priority" in assignments
                else 0,
                function_code=_token(
                    assignments["FunctionCode"].source,
                    "FunctionCode de Move",
                ),
                flags=_flags(assignments["Flags"].source)
                if "Flags" in assignments
                else (),
                effect_chance=_unsigned(
                    assignments["EffectChance"].source,
                    "EffectChance de Move",
                )
                if "EffectChance" in assignments
                else 0,
                description=assignments["Description"],
            )
        )
        assignments = {}

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
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", identifier) is None:
                raise MoveIntegrityError("Identifiant de Move non canonique.")
            continue
        if section_index < 0:
            raise MoveIntegrityError("Affectation de Move située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise MoveIntegrityError("Ligne de Move non reconnue.")
        prefix, key, source, trailing = parsed
        if key not in MOVE_PBS_KEYS:
            raise MoveIntegrityError(f"Champ de Move inconnu : {key}.")
        if key in assignments:
            raise MoveIntegrityError(f"Champ de Move dupliqué : {key}.")
        if key in MOVE_TEXT_FIELDS:
            _canonical_text(source, f"{key} de Move")
        assignments[key] = MovePbsAssignment(
            section=identifier,
            section_index=section_index,
            line_index=line_index,
            line_number=line_index + 1,
            key=key,
            prefix=prefix,
            source=source,
            trailing=trailing,
            newline="\r\n",
        )
    finish_section()
    identifiers = [section.identifier for section in sections]
    if not sections or len(set(identifiers)) != len(identifiers):
        raise MoveIntegrityError("Sections de Moves absentes ou dupliquées.")
    return MovePbsDocument(lines, tuple(sections), _sha256(raw))


def _validate_compiled_entry(
    key: object,
    section: MovePbsSection,
    value: object,
) -> RubyObject:
    if not isinstance(key, str) or key != section.identifier:
        raise MoveIntegrityError("La clé compilée de Move a changé.")
    if not isinstance(value, RubyObject) or value.class_name != MOVE_CLASS:
        raise MoveIntegrityError("Une entrée compilée n'est pas GameData::Move.")
    if tuple(value.ivars) != MOVE_IVARS:
        raise MoveIntegrityError("La structure compilée d'une Move a changé.")
    expected_integers = {
        "@category": section.category_code,
        "@power": section.power,
        "@accuracy": section.accuracy,
        "@total_pp": section.total_pp,
        "@priority": section.priority,
        "@effect_chance": section.effect_chance,
    }
    if any(type(value.ivars[name]) is not int for name in expected_integers):
        raise MoveIntegrityError("Les champs numériques techniques de la Move ont changé de type.")
    flags = value.ivars["@flags"]
    if not isinstance(flags, list):
        raise MoveIntegrityError("Les Flags compilés de la Move ne sont plus un tableau.")
    actual_flags = tuple(_ruby_text(flag, "Un flag compilé de Move").text() for flag in flags)
    if (
        value.ivars["@id"] != section.identifier
        or _ruby_text(value.ivars["@real_name"], "Le nom compilé de Move").text()
        != section.name.source
        or value.ivars["@type"] != section.move_type
        or any(value.ivars[name] != expected for name, expected in expected_integers.items())
        or value.ivars["@target"] != section.target
        or _ruby_text(
            value.ivars["@function_code"],
            "Le FunctionCode compilé de Move",
        ).text()
        != section.function_code
        or actual_flags != section.flags
        or _ruby_text(
            value.ivars["@real_description"],
            "La description compilée de Move",
        ).text()
        != section.description.source
        or _ruby_text(value.ivars["@pbs_file_suffix"], "Le suffixe PBS de Move").text()
        != ""
    ):
        raise MoveIntegrityError(
            "Les métadonnées ou champs techniques compilés de la Move ont changé."
        )
    return value


def _technical_sha256(section: MovePbsSection) -> str:
    payload = json.dumps(
        {
            "id": section.identifier,
            "type": section.move_type,
            "category_label": section.category_label,
            "category_code": section.category_code,
            "power": section.power,
            "accuracy": section.accuracy,
            "total_pp": section.total_pp,
            "target": section.target,
            "priority": section.priority,
            "function_code": section.function_code,
            "flags": list(section.flags),
            "effect_chance": section.effect_chance,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return _sha256(payload)


def _analyze_sources(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _MoveAnalysis:
    pbs = parse_move_pbs(pbs_raw)
    compiled_root = _load_marshal(compiled_raw, COMPILED_MOVE_FILE)
    if not isinstance(compiled_root, dict) or len(compiled_root) != len(pbs.sections):
        raise MoveIntegrityError("La racine moves.dat ne correspond pas aux sections PBS.")
    compiled_reference_counts = _reference_counts(compiled_root)

    compiled_by_field: dict[str, list[tuple[MovePbsSection, MovePbsAssignment, RubyString]]] = {
        field: [] for field in MOVE_TEXT_FIELDS
    }
    for section, (key, raw_object) in zip(pbs.sections, compiled_root.items()):
        compiled_object = _validate_compiled_entry(key, section, raw_object)
        for field, (ivar, _bank_index) in MOVE_TEXT_FIELDS.items():
            assignment = section.name if field == "Name" else section.description
            compiled_by_field[field].append(
                (section, assignment, _ruby_text(compiled_object.ivars[ivar], field))
            )

    messages_root = _load_marshal(messages_raw, MOVE_MESSAGES_FILE)
    if not isinstance(messages_root, list):
        raise MoveIntegrityError("La racine messages_core.dat n'est pas un Array v21.1.")
    message_reference_counts = _reference_counts(messages_root)

    targets: dict[tuple[str, str, int], MoveTarget] = {}
    for field, (_ivar, bank_index) in MOVE_TEXT_FIELDS.items():
        if len(messages_root) <= bank_index or not isinstance(messages_root[bank_index], dict):
            raise MoveIntegrityError(f"La banque de Move {field} n'est pas à l'index attendu.")
        entries = compiled_by_field[field]
        sources = [assignment.source for _section, assignment, _value in entries]
        source_counts = Counter(sources)
        ordered_unique = list(dict.fromkeys(sources))
        message_items = list(messages_root[bank_index].items())
        decoded: list[tuple[str, RubyString, RubyString]] = []
        for key, value in message_items:
            message_key = _ruby_text(key, f"Une clé de banque Move {field}")
            message_value = _ruby_text(value, f"Une valeur de banque Move {field}")
            if message_value.text() != message_key.text():
                raise MoveIntegrityError("Une banque Move possède déjà une valeur divergente.")
            decoded.append((message_key.text(), message_key, message_value))
        decoded_sources = [source for source, _key, _value in decoded]
        if len(set(decoded_sources)) != len(decoded_sources):
            raise MoveIntegrityError("Une banque Move contient des clés textuelles ambiguës.")
        if field == "Description":
            if decoded_sources != ordered_unique:
                raise MoveIntegrityError(
                    "La banque MOVE_DESCRIPTIONS ne couvre pas exactement moves.dat."
                )
        else:
            positions = []
            by_source_index = {source: index for index, source in enumerate(decoded_sources)}
            for source in ordered_unique:
                if source not in by_source_index:
                    raise MoveIntegrityError("La banque MOVE_NAMES ne couvre pas moves.dat.")
                positions.append(by_source_index[source])
            if positions != sorted(positions):
                raise MoveIntegrityError("L'ordre de MOVE_NAMES ne correspond plus à moves.dat.")
        runtime_by_source = {
            source: (key, value, index)
            for index, (source, key, value) in enumerate(decoded)
            if source in source_counts
        }
        if len(runtime_by_source) != len(source_counts):
            raise MoveIntegrityError("Une source Move ne possède pas de banque déterministe.")
        for section, assignment, compiled_value in entries:
            message_key, message_value, message_index = runtime_by_source[assignment.source]
            targets[(section.identifier, field, 1)] = MoveTarget(
                field=field,
                assignment=assignment,
                compiled_value=compiled_value,
                compiled_path=(assignment.section_index, MOVE_TEXT_FIELDS[field][0]),
                compiled_reference_count=compiled_reference_counts[id(compiled_value)],
                message_key=message_key,
                message_value=message_value,
                message_path=(bank_index, "entry", message_index),
                source_usage_count=source_counts[assignment.source],
                runtime_key_reference_count=message_reference_counts[id(message_key)],
                runtime_value_reference_count=message_reference_counts[id(message_value)],
            )
    return _MoveAnalysis(
        pbs=pbs,
        compiled_root=compiled_root,
        messages_root=messages_root,
        targets=targets,
        compiled_graph_sha256=graph_sha256(compiled_root),
        messages_graph_sha256=graph_sha256(messages_root),
    )


def _pbs_proof(
    analysis: _MoveAnalysis,
    target: MoveTarget,
) -> str:
    assignment = target.assignment
    section = analysis.pbs.sections[assignment.section_index]
    other_text = section.description if target.field == "Name" else section.name
    proof = {
        "format": MOVE_PBS_PROOF_FORMAT,
        "pbs_file": MOVE_PBS_FILE,
        "file_sha256": analysis.pbs.file_sha256,
        "encoding": "utf-8-sig",
        "bom": "utf-8",
        "newline": "CRLF",
        "line_number": assignment.line_number,
        "section_index": assignment.section_index,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "field": target.field,
        "key_occurrence": 1,
        "prefix_sha256": _sha256(assignment.prefix.encode("utf-8")),
        "trailing_sha256": _sha256(assignment.trailing.encode("utf-8")),
        "line_sha256": _sha256(
            analysis.pbs.content_lines[assignment.line_index].encode("utf-8")
        ),
        "source_sha256": _sha256(assignment.source.encode("utf-8")),
        "other_text_sha256": _sha256(other_text.source.encode("utf-8")),
        "technical_fields_sha256": _technical_sha256(section),
        "category_code": section.category_code,
        "source_usage_count": target.source_usage_count,
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _compiled_proof(
    analysis: _MoveAnalysis,
    target: MoveTarget,
    compiled_raw: bytes,
) -> str:
    section = analysis.pbs.sections[target.assignment.section_index]
    compiled_object = list(analysis.compiled_root.values())[target.assignment.section_index]
    proof = {
        "format": COMPILED_MOVE_PROOF_FORMAT,
        "compiled_file": COMPILED_MOVE_FILE,
        "file_sha256": _sha256(compiled_raw),
        "root_type": "Hash",
        "root_size": len(analysis.compiled_root),
        "root_graph_sha256": analysis.compiled_graph_sha256,
        "non_target_section_graph_sha256": graph_sha256(
            compiled_object,
            masked=(target.compiled_value,),
        ),
        "section_index": target.assignment.section_index,
        "section_sha256": _sha256(target.assignment.section.encode("utf-8")),
        "section_class": MOVE_CLASS,
        "section_ivars": list(MOVE_IVARS),
        "field": target.field,
        "technical_fields_sha256": _technical_sha256(section),
        "category_code": section.category_code,
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.compiled_value.ivars),
        "target_value_sha256": _sha256(target.compiled_value.data),
        "target_reference_count": target.compiled_reference_count,
        "compiled_path": list(target.compiled_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _MoveAnalysis,
    target: MoveTarget,
    messages_raw: bytes,
) -> str:
    bank_index = MOVE_TEXT_FIELDS[target.field][1]
    proof = {
        "format": MOVE_RUNTIME_PROOF_FORMAT,
        "runtime_file": MOVE_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "field": target.field,
        "message_type_index": bank_index,
        "message_count": len(analysis.messages_root[bank_index]),
        "root_graph_sha256": analysis.messages_graph_sha256,
        "non_target_bank_graph_sha256": graph_sha256(
            analysis.messages_root[bank_index],
            masked=(target.message_key, target.message_value),
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


def build_move_text_proofs(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> dict[tuple[str, str, int], MoveEntryProof]:
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    return {
        key: MoveEntryProof(
            source=target.assignment.source,
            pbs_structure=_pbs_proof(analysis, target),
            compiled_path=json.dumps(list(target.compiled_path), separators=(",", ":")),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(list(target.message_path), separators=(",", ":")),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
        for key, target in analysis.targets.items()
    }


def rebuild_move_description_payloads(
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
        raise MoveIntegrityError("La traduction de Move ne tient pas sur une ligne PBS.")
    translated = translation.encode("utf-8")
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    target_key = (section, "Description", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise MoveIntegrityError("La description de Move ne correspond plus à la source.")
    expected = build_move_text_proofs(pbs_raw, compiled_raw, messages_raw)[target_key]
    if (
        expected.pbs_structure != pbs_structure
        or expected.compiled_path != compiled_path
        or expected.compiled_structure != compiled_structure
        or expected.runtime_path != runtime_path
        or expected.runtime_structure != runtime_structure
    ):
        raise MoveIntegrityError("La preuve de Move ne correspond plus aux trois sources.")
    if target.source_usage_count != 1:
        raise MoveIntegrityError(
            "Cette description est partagée par plusieurs Moves et reste bloquée."
        )
    if target.compiled_reference_count != 1:
        raise MoveIntegrityError("Cette description partage un objet moves.dat et reste bloquée.")
    if target.runtime_key_reference_count != 1 or target.runtime_value_reference_count != 1:
        raise MoveIntegrityError("Cette description partage un objet de banque et reste bloquée.")
    if json.loads(runtime_structure).get("target_value_equals_source") is not True:
        raise MoveIntegrityError("La banque possède déjà une traduction différente.")
    bank_index = MOVE_DESCRIPTION_MESSAGES_INDEX
    if translation in {
        item.text()
        for item in analysis.messages_root[bank_index]
        if item is not target.message_key
    }:
        raise MoveIntegrityError("La traduction entrerait en collision avec une autre clé.")

    assignment = target.assignment
    pbs_lines = list(analysis.pbs.content_lines)
    pbs_lines[assignment.line_index] = (
        assignment.prefix + translation + assignment.trailing + assignment.newline
    )
    rebuilt_pbs = b"\xef\xbb\xbf" + "".join(pbs_lines).encode("utf-8")

    compiled_before = graph_sha256(
        analysis.compiled_root,
        masked=(target.compiled_value,),
    )
    compiled_object = list(analysis.compiled_root.values())[assignment.section_index]
    compiled_object.ivars["@real_description"] = RubyString(
        translated,
        dict(target.compiled_value.ivars),
    )
    rebuilt_compiled_target = compiled_object.ivars["@real_description"]
    if graph_sha256(
        analysis.compiled_root,
        masked=(rebuilt_compiled_target,),
    ) != compiled_before:
        raise MoveIntegrityError("La mutation modifierait moves.dat hors description cible.")
    rebuilt_compiled = dumps(analysis.compiled_root)

    runtime_before = graph_sha256(
        analysis.messages_root,
        masked=(target.message_key, target.message_value),
    )
    replacement_hash: dict = {}
    old_hash = analysis.messages_root[bank_index]
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_hash[RubyString(translated, dict(old_key.ivars))] = RubyString(
                translated,
                dict(old_value.ivars),
            )
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[bank_index] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[target.message_path[-1]]
    if graph_sha256(
        analysis.messages_root,
        masked=(rebuilt_key, rebuilt_value),
    ) != runtime_before:
        raise MoveIntegrityError("La mutation modifierait messages_core.dat hors cible.")
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
        raise MoveIntegrityError("La relecture ne retrouve pas la description exacte.")
    return {
        MOVE_PBS_FILE: rebuilt_pbs,
        COMPILED_MOVE_FILE: rebuilt_compiled,
        MOVE_MESSAGES_FILE: rebuilt_messages,
    }


def extract_move_description_texts(
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
        raise MoveIntegrityError("La description de Move est introuvable.")
    return (
        target.assignment.source,
        target.compiled_value.text(),
        target.message_value.text(),
    )
