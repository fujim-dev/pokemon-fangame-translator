# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée d'une description d'Item v21.1.

Le module ne lance jamais Ruby. Il relie les champs réellement textuels de
``PBS/items.txt`` aux objets ``GameData::Item`` de ``Data/items.dat`` puis aux
banques de noms, pluriels, portions et descriptions de
``Data/messages_core.dat``. Les usages, prix, flags, identifiants de Move et
autres champs techniques ne deviennent jamais des occurrences traduisibles.
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


ITEM_PBS_FILE = "PBS/items.txt"
COMPILED_ITEM_FILE = "Data/items.dat"
ITEM_MESSAGES_FILE = "Data/messages_core.dat"
ITEM_NAME_MESSAGES_INDEX = 7
ITEM_NAME_PLURAL_MESSAGES_INDEX = 8
ITEM_DESCRIPTION_MESSAGES_INDEX = 9
ITEM_PORTION_NAME_MESSAGES_INDEX = 28
ITEM_PORTION_NAME_PLURAL_MESSAGES_INDEX = 29
ITEM_CLASS = "GameData::Item"
ITEM_PBS_PROOF_FORMAT = "pft_v21_1_item_pbs_v1"
COMPILED_ITEM_PROOF_FORMAT = "pft_v21_1_compiled_item_v1"
ITEM_RUNTIME_PROOF_FORMAT = "pft_v21_1_item_runtime_v1"
ITEM_TEXT_FIELDS = {
    "Name": ("@real_name", ITEM_NAME_MESSAGES_INDEX),
    "NamePlural": ("@real_name_plural", ITEM_NAME_PLURAL_MESSAGES_INDEX),
    "PortionName": ("@real_portion_name", ITEM_PORTION_NAME_MESSAGES_INDEX),
    "PortionNamePlural": (
        "@real_portion_name_plural",
        ITEM_PORTION_NAME_PLURAL_MESSAGES_INDEX,
    ),
    "Description": ("@real_description", ITEM_DESCRIPTION_MESSAGES_INDEX),
}
ITEM_OPTIONAL_TEXT_FIELDS = frozenset({"PortionName", "PortionNamePlural"})
ITEM_FIELD_USE_CODES = {"OnPokemon": 1, "Direct": 2, "TM": 3, "HM": 4, "TR": 5}
ITEM_BATTLE_USE_CODES = {
    "OnPokemon": 1,
    "OnMove": 2,
    "OnBattler": 3,
    "OnFoe": 4,
    "Direct": 5,
}
ITEM_IVARS = (
    "@id",
    "@real_name",
    "@real_name_plural",
    "@real_portion_name",
    "@real_portion_name_plural",
    "@pocket",
    "@price",
    "@sell_price",
    "@bp_price",
    "@field_use",
    "@battle_use",
    "@flags",
    "@consumable",
    "@show_quantity",
    "@move",
    "@real_description",
    "@pbs_file_suffix",
)
ITEM_PBS_KEYS = frozenset(
    {
        "Name",
        "NamePlural",
        "PortionName",
        "PortionNamePlural",
        "Pocket",
        "Price",
        "SellPrice",
        "BPPrice",
        "FieldUse",
        "BattleUse",
        "Flags",
        "Consumable",
        "ShowQuantity",
        "Move",
        "Description",
    }
)


class ItemIntegrityError(ValueError):
    """La triple correspondance d'un texte d'Item n'est pas démontrable."""


@dataclass(frozen=True)
class ItemPbsAssignment:
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
class ItemPbsSection:
    identifier: str
    name: ItemPbsAssignment
    name_plural: ItemPbsAssignment
    portion_name: ItemPbsAssignment | None
    portion_name_plural: ItemPbsAssignment | None
    pocket: int
    price: int
    sell_price: int
    bp_price: int
    field_use: int
    battle_use: int
    flags: tuple[str, ...]
    consumable: bool
    show_quantity: bool | None
    move: str | None
    description: ItemPbsAssignment
    assignment_order: tuple[str, ...]

    def text_assignment(self, field: str) -> ItemPbsAssignment | None:
        return {
            "Name": self.name,
            "NamePlural": self.name_plural,
            "PortionName": self.portion_name,
            "PortionNamePlural": self.portion_name_plural,
            "Description": self.description,
        }.get(field)


@dataclass(frozen=True)
class ItemPbsDocument:
    content_lines: tuple[str, ...]
    sections: tuple[ItemPbsSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class ItemTarget:
    field: str
    assignment: ItemPbsAssignment
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
class ItemEntryProof:
    source: str
    pbs_structure: str
    compiled_path: str
    compiled_structure: str
    runtime_path: str
    runtime_structure: str


@dataclass
class _ItemAnalysis:
    pbs: ItemPbsDocument
    compiled_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], ItemTarget]
    compiled_graph_sha256: str
    messages_graph_sha256: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_marshal(raw: bytes, label: str) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise ItemIntegrityError(f"{label} n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise ItemIntegrityError(f"{label} est illisible sans exécuter Ruby.") from exc
    if reader.pos != len(raw) or dumps(root) != raw:
        raise ItemIntegrityError(
            f"Le lecteur/écrivain Marshal ne reproduit pas exactement {label}."
        )
    return root


def _ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise ItemIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ItemIntegrityError(f"{label} n'est pas une chaîne UTF-8 valide.") from exc
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
        raise ItemIntegrityError(f"{label} est absent ou ambigu.")
    return value


def _token(value: str, label: str, *, uppercase: bool = False) -> str:
    value = _canonical_text(value, label)
    pattern = r"[A-Z][A-Z0-9_]*" if uppercase else r"[A-Za-z0-9_]+"
    if re.fullmatch(pattern, value) is None:
        raise ItemIntegrityError(f"{label} n'est pas canonique.")
    return value


def _unsigned(value: str, label: str) -> int:
    value = _canonical_text(value, label)
    if re.fullmatch(r"0|[1-9]\d*", value) is None:
        raise ItemIntegrityError(f"{label} n'est pas un entier non signé canonique.")
    return int(value)


def _boolean(value: str, label: str) -> bool:
    value = _canonical_text(value, label)
    if value not in {"true", "false"}:
        raise ItemIntegrityError(f"{label} n'est pas un booléen canonique.")
    return value == "true"


def _enum(value: str, label: str, values: dict[str, int]) -> int:
    value = _canonical_text(value, label)
    if value not in values:
        raise ItemIntegrityError(f"{label} n'est pas un enum technique reconnu.")
    return values[value]


def _flags(value: str) -> tuple[str, ...]:
    value = _canonical_text(value, "Flags d'Item")
    parsed = tuple(value.split(","))
    if any(
        flag != flag.strip()
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", flag) is None
        for flag in parsed
    ):
        raise ItemIntegrityError("Flags d'Item non canoniques.")
    return parsed


def _is_important(field_use: int, flags: tuple[str, ...]) -> bool:
    return field_use in {3, 4} or any(flag.casefold() == "keyitem" for flag in flags)


def parse_item_pbs(raw: bytes) -> ItemPbsDocument:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise ItemIntegrityError("PBS/items.txt doit conserver son BOM UTF-8.")
    payload = raw[3:]
    if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(b"\r\n", b""):
        raise ItemIntegrityError("PBS/items.txt doit conserver exclusivement ses CRLF.")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ItemIntegrityError("PBS/items.txt n'est pas un UTF-8 valide.") from exc
    lines = tuple(content.splitlines(keepends=True))
    if not lines or any(not line.endswith("\r\n") for line in lines):
        raise ItemIntegrityError("Chaque ligne de PBS/items.txt doit conserver son CRLF.")

    sections: list[ItemPbsSection] = []
    identifier = ""
    section_index = -1
    assignments: dict[str, ItemPbsAssignment] = {}

    def finish_section() -> None:
        nonlocal assignments
        if section_index < 0:
            return
        required = {"Name", "NamePlural", "Pocket", "Price", "Description"}
        if not required.issubset(assignments):
            raise ItemIntegrityError("Un Item ne possède pas son schéma PBS minimal exact.")
        has_portion_name = "PortionName" in assignments
        has_portion_plural = "PortionNamePlural" in assignments
        if has_portion_name != has_portion_plural:
            raise ItemIntegrityError(
                "Les noms de portion singulier/pluriel doivent rester présents ensemble."
            )
        pocket = _unsigned(assignments["Pocket"].source, "Pocket d'Item")
        if pocket <= 0:
            raise ItemIntegrityError("Pocket d'Item doit rester strictement positif.")
        price = _unsigned(assignments["Price"].source, "Price d'Item")
        field_use = (
            _enum(assignments["FieldUse"].source, "FieldUse d'Item", ITEM_FIELD_USE_CODES)
            if "FieldUse" in assignments
            else 0
        )
        flags = _flags(assignments["Flags"].source) if "Flags" in assignments else ()
        consumable = (
            _boolean(assignments["Consumable"].source, "Consumable d'Item")
            if "Consumable" in assignments
            else not _is_important(field_use, flags)
        )
        sections.append(
            ItemPbsSection(
                identifier=identifier,
                name=assignments["Name"],
                name_plural=assignments["NamePlural"],
                portion_name=assignments.get("PortionName"),
                portion_name_plural=assignments.get("PortionNamePlural"),
                pocket=pocket,
                price=price,
                sell_price=(
                    _unsigned(assignments["SellPrice"].source, "SellPrice d'Item")
                    if "SellPrice" in assignments
                    else price // 2
                ),
                bp_price=(
                    _unsigned(assignments["BPPrice"].source, "BPPrice d'Item")
                    if "BPPrice" in assignments
                    else 1
                ),
                field_use=field_use,
                battle_use=(
                    _enum(
                        assignments["BattleUse"].source,
                        "BattleUse d'Item",
                        ITEM_BATTLE_USE_CODES,
                    )
                    if "BattleUse" in assignments
                    else 0
                ),
                flags=flags,
                consumable=consumable,
                show_quantity=(
                    _boolean(assignments["ShowQuantity"].source, "ShowQuantity d'Item")
                    if "ShowQuantity" in assignments
                    else None
                ),
                move=(
                    _token(assignments["Move"].source, "Move associée à l'Item", uppercase=True)
                    if "Move" in assignments
                    else None
                ),
                description=assignments["Description"],
                assignment_order=tuple(assignments),
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
                raise ItemIntegrityError("Identifiant d'Item non canonique.")
            continue
        if section_index < 0:
            raise ItemIntegrityError("Affectation d'Item située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise ItemIntegrityError("Ligne d'Item non reconnue.")
        prefix, key, source, trailing = parsed
        if key not in ITEM_PBS_KEYS:
            raise ItemIntegrityError(f"Champ d'Item inconnu : {key}.")
        if key in assignments:
            raise ItemIntegrityError(f"Champ d'Item dupliqué : {key}.")
        if key in ITEM_TEXT_FIELDS:
            _canonical_text(source, f"{key} d'Item")
        assignments[key] = ItemPbsAssignment(
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
        raise ItemIntegrityError("Sections de Items absentes ou dupliquées.")
    return ItemPbsDocument(lines, tuple(sections), _sha256(raw))


def _validate_compiled_entry(
    key: object,
    section: ItemPbsSection,
    value: object,
) -> RubyObject:
    if not isinstance(key, str) or key != section.identifier:
        raise ItemIntegrityError("La clé compilée d'Item a changé.")
    if not isinstance(value, RubyObject) or value.class_name != ITEM_CLASS:
        raise ItemIntegrityError("Une entrée compilée n'est pas GameData::Item.")
    if tuple(value.ivars) != ITEM_IVARS:
        raise ItemIntegrityError("La structure compilée d'un Item a changé.")
    expected_integers = {
        "@pocket": section.pocket,
        "@price": section.price,
        "@sell_price": section.sell_price,
        "@bp_price": section.bp_price,
        "@field_use": section.field_use,
        "@battle_use": section.battle_use,
    }
    if any(type(value.ivars[name]) is not int for name in expected_integers):
        raise ItemIntegrityError("Les champs numériques techniques de l'Item ont changé de type.")
    flags = value.ivars["@flags"]
    if not isinstance(flags, list):
        raise ItemIntegrityError("Les Flags compilés de l'Item ne sont plus un tableau.")
    actual_flags = tuple(_ruby_text(flag, "Un flag compilé d'Item").text() for flag in flags)
    expected_texts = {
        "@real_name": section.name,
        "@real_name_plural": section.name_plural,
        "@real_portion_name": section.portion_name,
        "@real_portion_name_plural": section.portion_name_plural,
        "@real_description": section.description,
    }
    for ivar, assignment in expected_texts.items():
        compiled_value = value.ivars[ivar]
        if assignment is None:
            if compiled_value is not None:
                raise ItemIntegrityError("Un nom de portion absent n'est plus nil dans items.dat.")
        elif _ruby_text(compiled_value, f"Le champ compilé {ivar} d'Item").text() != assignment.source:
            raise ItemIntegrityError("Un texte compilé d'Item ne correspond plus au PBS.")
    if (
        value.ivars["@id"] != section.identifier
        or any(value.ivars[name] != expected for name, expected in expected_integers.items())
        or actual_flags != section.flags
        or type(value.ivars["@consumable"]) is not bool
        or value.ivars["@consumable"] is not section.consumable
        or (
            value.ivars["@show_quantity"] is not None
            and type(value.ivars["@show_quantity"]) is not bool
        )
        or value.ivars["@show_quantity"] != section.show_quantity
        or (
            value.ivars["@move"] is not None
            and not isinstance(value.ivars["@move"], str)
        )
        or value.ivars["@move"] != section.move
        or _ruby_text(value.ivars["@pbs_file_suffix"], "Le suffixe PBS d'Item").text()
        != ""
    ):
        raise ItemIntegrityError(
            "Les métadonnées ou champs techniques compilés de l'Item ont changé."
        )
    return value


def _technical_sha256(section: ItemPbsSection) -> str:
    payload = json.dumps(
        {
            "id": section.identifier,
            "assignment_order": list(section.assignment_order),
            "pocket": section.pocket,
            "price": section.price,
            "sell_price": section.sell_price,
            "bp_price": section.bp_price,
            "field_use": section.field_use,
            "battle_use": section.battle_use,
            "flags": list(section.flags),
            "consumable": section.consumable,
            "show_quantity": section.show_quantity,
            "move": section.move,
            "portion_name_present": section.portion_name is not None,
            "portion_name_plural_present": section.portion_name_plural is not None,
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
) -> _ItemAnalysis:
    pbs = parse_item_pbs(pbs_raw)
    compiled_root = _load_marshal(compiled_raw, COMPILED_ITEM_FILE)
    if not isinstance(compiled_root, dict) or len(compiled_root) != len(pbs.sections):
        raise ItemIntegrityError("La racine items.dat ne correspond pas aux sections PBS.")
    compiled_reference_counts = _reference_counts(compiled_root)

    compiled_by_field: dict[str, list[tuple[ItemPbsSection, ItemPbsAssignment, RubyString]]] = {
        field: [] for field in ITEM_TEXT_FIELDS
    }
    for section, (key, raw_object) in zip(pbs.sections, compiled_root.items()):
        compiled_object = _validate_compiled_entry(key, section, raw_object)
        for field, (ivar, _bank_index) in ITEM_TEXT_FIELDS.items():
            assignment = section.text_assignment(field)
            if assignment is None:
                if field not in ITEM_OPTIONAL_TEXT_FIELDS or compiled_object.ivars[ivar] is not None:
                    raise ItemIntegrityError("Un champ textuel optionnel d'Item est incohérent.")
                continue
            compiled_by_field[field].append(
                (section, assignment, _ruby_text(compiled_object.ivars[ivar], field))
            )

    messages_root = _load_marshal(messages_raw, ITEM_MESSAGES_FILE)
    if not isinstance(messages_root, list):
        raise ItemIntegrityError("La racine messages_core.dat n'est pas un Array v21.1.")
    message_reference_counts = _reference_counts(messages_root)

    targets: dict[tuple[str, str, int], ItemTarget] = {}
    for field, (_ivar, bank_index) in ITEM_TEXT_FIELDS.items():
        if len(messages_root) <= bank_index or not isinstance(messages_root[bank_index], dict):
            raise ItemIntegrityError(f"La banque d'Item {field} n'est pas à l'index attendu.")
        entries = compiled_by_field[field]
        if not entries:
            continue
        sources = [assignment.source for _section, assignment, _value in entries]
        source_counts = Counter(sources)
        ordered_unique = list(dict.fromkeys(sources))
        message_items = list(messages_root[bank_index].items())
        decoded: list[tuple[str, RubyString, RubyString]] = []
        for key, value in message_items:
            message_key = _ruby_text(key, f"Une clé de banque d'Item {field}")
            message_value = _ruby_text(value, f"Une valeur de banque d'Item {field}")
            decoded.append((message_key.text(), message_key, message_value))
        decoded_sources = [source for source, _key, _value in decoded]
        if len(set(decoded_sources)) != len(decoded_sources):
            raise ItemIntegrityError("Une banque Item contient des clés textuelles ambiguës.")
        runtime_by_source = {
            source: (key, value, index)
            for index, (source, key, value) in enumerate(decoded)
            if source in source_counts and value.text() == source
        }
        positions = [
            runtime_by_source[source][2]
            for source in ordered_unique
            if source in runtime_by_source
        ]
        if positions != sorted(positions):
            raise ItemIntegrityError("L'ordre de la banque Item ne correspond plus à items.dat.")
        for section, assignment, compiled_value in entries:
            if assignment.source not in runtime_by_source:
                # Une clé de banque ancienne/divergente reste extractible depuis le
                # PBS, mais ne reçoit aucune preuve de reconstruction individuelle.
                continue
            message_key, message_value, message_index = runtime_by_source[assignment.source]
            targets[(section.identifier, field, 1)] = ItemTarget(
                field=field,
                assignment=assignment,
                compiled_value=compiled_value,
                compiled_path=(assignment.section_index, ITEM_TEXT_FIELDS[field][0]),
                compiled_reference_count=compiled_reference_counts[id(compiled_value)],
                message_key=message_key,
                message_value=message_value,
                message_path=(bank_index, "entry", message_index),
                source_usage_count=source_counts[assignment.source],
                runtime_key_reference_count=message_reference_counts[id(message_key)],
                runtime_value_reference_count=message_reference_counts[id(message_value)],
            )
    return _ItemAnalysis(
        pbs=pbs,
        compiled_root=compiled_root,
        messages_root=messages_root,
        targets=targets,
        compiled_graph_sha256=graph_sha256(compiled_root),
        messages_graph_sha256=graph_sha256(messages_root),
    )


def _other_text_fields_sha256(section: ItemPbsSection, target_field: str) -> str:
    values: dict[str, str | None] = {}
    for field in ITEM_TEXT_FIELDS:
        if field == target_field:
            continue
        assignment = section.text_assignment(field)
        values[field] = (
            _sha256(assignment.source.encode("utf-8")) if assignment is not None else None
        )
    return _sha256(
        json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "ascii"
        )
    )


def _pbs_proof(
    analysis: _ItemAnalysis,
    target: ItemTarget,
) -> str:
    assignment = target.assignment
    section = analysis.pbs.sections[assignment.section_index]
    proof = {
        "format": ITEM_PBS_PROOF_FORMAT,
        "pbs_file": ITEM_PBS_FILE,
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
        "other_text_fields_sha256": _other_text_fields_sha256(section, target.field),
        "technical_fields_sha256": _technical_sha256(section),
        "assignment_order_sha256": _sha256(
            json.dumps(list(section.assignment_order), separators=(",", ":")).encode("ascii")
        ),
        "source_usage_count": target.source_usage_count,
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _compiled_proof(
    analysis: _ItemAnalysis,
    target: ItemTarget,
    compiled_raw: bytes,
) -> str:
    section = analysis.pbs.sections[target.assignment.section_index]
    compiled_object = list(analysis.compiled_root.values())[target.assignment.section_index]
    proof = {
        "format": COMPILED_ITEM_PROOF_FORMAT,
        "compiled_file": COMPILED_ITEM_FILE,
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
        "section_class": ITEM_CLASS,
        "section_ivars": list(ITEM_IVARS),
        "field": target.field,
        "technical_fields_sha256": _technical_sha256(section),
        "target_ivar": ITEM_TEXT_FIELDS[target.field][0],
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.compiled_value.ivars),
        "target_value_sha256": _sha256(target.compiled_value.data),
        "target_reference_count": target.compiled_reference_count,
        "compiled_path": list(target.compiled_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _ItemAnalysis,
    target: ItemTarget,
    messages_raw: bytes,
) -> str:
    bank_index = ITEM_TEXT_FIELDS[target.field][1]
    proof = {
        "format": ITEM_RUNTIME_PROOF_FORMAT,
        "runtime_file": ITEM_MESSAGES_FILE,
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
        "target_key_equals_source": target.message_key.text()
        == target.assignment.source,
        "target_value_equals_source": target.message_value.text()
        == target.assignment.source,
        "target_key_reference_count": target.runtime_key_reference_count,
        "target_value_reference_count": target.runtime_value_reference_count,
        "source_usage_count": target.source_usage_count,
        "runtime_path": list(target.message_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def build_item_text_proofs(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> dict[tuple[str, str, int], ItemEntryProof]:
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    return {
        key: ItemEntryProof(
            source=target.assignment.source,
            pbs_structure=_pbs_proof(analysis, target),
            compiled_path=json.dumps(list(target.compiled_path), separators=(",", ":")),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(list(target.message_path), separators=(",", ":")),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
        for key, target in analysis.targets.items()
    }


def rebuild_item_description_payloads(
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
        raise ItemIntegrityError("La traduction d'Item ne tient pas sur une ligne PBS.")
    translated = translation.encode("utf-8")
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    target_key = (section, "Description", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise ItemIntegrityError("La description d'Item ne correspond plus à la source.")
    expected = build_item_text_proofs(pbs_raw, compiled_raw, messages_raw)[target_key]
    if (
        expected.pbs_structure != pbs_structure
        or expected.compiled_path != compiled_path
        or expected.compiled_structure != compiled_structure
        or expected.runtime_path != runtime_path
        or expected.runtime_structure != runtime_structure
    ):
        raise ItemIntegrityError("La preuve d'Item ne correspond plus aux trois sources.")
    if target.source_usage_count != 1:
        raise ItemIntegrityError(
            "Cette description est partagée par plusieurs Items et reste bloquée."
        )
    if target.compiled_reference_count != 1:
        raise ItemIntegrityError("Cette description partage un objet items.dat et reste bloquée.")
    if target.runtime_key_reference_count != 1 or target.runtime_value_reference_count != 1:
        raise ItemIntegrityError("Cette description partage un objet de banque et reste bloquée.")
    runtime_proof = json.loads(runtime_structure)
    if (
        runtime_proof.get("target_key_equals_source") is not True
        or runtime_proof.get("target_value_equals_source") is not True
    ):
        raise ItemIntegrityError("La banque possède déjà une traduction différente.")
    bank_index = ITEM_DESCRIPTION_MESSAGES_INDEX
    if translation in {
        item.text()
        for item in analysis.messages_root[bank_index]
        if item is not target.message_key
    }:
        raise ItemIntegrityError("La traduction entrerait en collision avec une autre clé.")

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
        raise ItemIntegrityError("La mutation modifierait items.dat hors description cible.")
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
        raise ItemIntegrityError("La mutation modifierait messages_core.dat hors cible.")
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
        raise ItemIntegrityError("La relecture ne retrouve pas la description exacte.")
    return {
        ITEM_PBS_FILE: rebuilt_pbs,
        COMPILED_ITEM_FILE: rebuilt_compiled,
        ITEM_MESSAGES_FILE: rebuilt_messages,
    }


def extract_item_description_texts(
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
        raise ItemIntegrityError("La description d'Item est introuvable.")
    return (
        target.assignment.source,
        target.compiled_value.text(),
        target.message_value.text(),
    )
