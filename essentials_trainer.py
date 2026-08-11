# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée d'un ``LoseText`` v21.1.

Le module ne lance jamais Ruby. Il relie une affectation de
``PBS/trainers.txt`` à l'objet ``GameData::Trainer`` de ``Data/trainers.dat``
puis à la banque ``TRAINER_SPEECHES_LOSE`` de ``Data/messages_game.dat``.
Une chaîne partagée par plusieurs dresseurs reste extractible, mais ne peut pas
être ciblée par la porte de reconstruction privée.
"""
from __future__ import annotations

import csv
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


TRAINER_PBS_FILE = "PBS/trainers.txt"
COMPILED_TRAINER_FILE = "Data/trainers.dat"
TRAINER_MESSAGES_FILE = "Data/messages_game.dat"
TRAINER_LOSE_MESSAGES_INDEX = 23
TRAINER_CLASS = "GameData::Trainer"
TRAINER_PBS_PROOF_FORMAT = "pft_v21_1_trainer_lose_pbs_v1"
COMPILED_TRAINER_PROOF_FORMAT = "pft_v21_1_compiled_trainer_lose_v1"
TRAINER_RUNTIME_PROOF_FORMAT = "pft_v21_1_trainer_lose_runtime_v1"
TRAINER_IVARS = (
    "@id",
    "@trainer_type",
    "@real_name",
    "@version",
    "@items",
    "@real_lose_text",
    "@pokemon",
    "@pbs_file_suffix",
)


class TrainerIntegrityError(ValueError):
    """La triple correspondance du texte de défaite n'est pas démontrable."""


@dataclass(frozen=True)
class TrainerPbsAssignment:
    section: str
    section_index: int
    line_index: int
    line_number: int
    prefix: str
    source: str
    trailing: str
    newline: str


@dataclass(frozen=True)
class TrainerPbsSection:
    name: str
    compiled_id: tuple[object, ...]
    lose_text: TrainerPbsAssignment


@dataclass(frozen=True)
class TrainerPbsDocument:
    content_lines: tuple[str, ...]
    sections: tuple[TrainerPbsSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class TrainerTarget:
    assignment: TrainerPbsAssignment
    trainer_value: RubyString
    trainer_path: tuple[object, ...]
    message_key: RubyString
    message_value: RubyString
    message_path: tuple[object, ...]
    source_usage_count: int
    runtime_key_reference_count: int
    runtime_value_reference_count: int


@dataclass(frozen=True)
class TrainerEntryProof:
    source: str
    pbs_structure: str
    compiled_path: str
    compiled_structure: str
    runtime_path: str
    runtime_structure: str


@dataclass
class _TrainerAnalysis:
    pbs: TrainerPbsDocument
    trainer_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], TrainerTarget]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_marshal(raw: bytes, label: str) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise TrainerIntegrityError(f"{label} n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise TrainerIntegrityError(f"{label} est illisible sans exécuter Ruby.") from exc
    if reader.pos != len(raw) or dumps(root) != raw:
        raise TrainerIntegrityError(
            f"Le lecteur/écrivain Marshal ne reproduit pas exactement {label}."
        )
    return root


def _ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise TrainerIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TrainerIntegrityError(f"{label} n'est pas une chaîne UTF-8 valide.") from exc
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


def _section_id(raw_name: str) -> tuple[object, ...]:
    if '"' in raw_name:
        raise TrainerIntegrityError("Une section trainers.txt citée reste bloquée.")
    try:
        fields = next(csv.reader([raw_name], skipinitialspace=False))
    except (csv.Error, StopIteration) as exc:
        raise TrainerIntegrityError("Identifiant de section dresseur illisible.") from exc
    if len(fields) not in {2, 3} or any(field != field.strip() for field in fields):
        raise TrainerIntegrityError("Identifiant de section dresseur ambigu.")
    trainer_type, real_name = fields[:2]
    if not re.fullmatch(r"[A-Z][A-Za-z0-9_]*", trainer_type) or not real_name:
        raise TrainerIntegrityError("Type ou nom de dresseur invalide.")
    version_text = fields[2] if len(fields) == 3 else "0"
    if not re.fullmatch(r"0|[1-9]\d*", version_text):
        raise TrainerIntegrityError("Version de dresseur non canonique.")
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


def parse_trainer_pbs(raw: bytes) -> TrainerPbsDocument:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise TrainerIntegrityError("PBS/trainers.txt doit conserver son BOM UTF-8.")
    payload = raw[3:]
    if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(b"\r\n", b""):
        raise TrainerIntegrityError("PBS/trainers.txt doit conserver exclusivement ses CRLF.")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TrainerIntegrityError("PBS/trainers.txt n'est pas un UTF-8 valide.") from exc
    lines = tuple(content.splitlines(keepends=True))
    if not lines or any(not line.endswith("\r\n") for line in lines):
        raise TrainerIntegrityError("Chaque ligne de PBS/trainers.txt doit conserver son CRLF.")

    pending: list[tuple[str, int, TrainerPbsAssignment | None]] = []
    section_name = ""
    section_index = -1
    lose_assignment: TrainerPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal lose_assignment
        if section_index < 0:
            return
        if lose_assignment is None:
            raise TrainerIntegrityError("Une section dresseur ne contient aucun LoseText.")
        pending.append((section_name, section_index, lose_assignment))
        lose_assignment = None

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
                raise TrainerIntegrityError("Section dresseur vide.")
            continue
        if section_index < 0:
            raise TrainerIntegrityError("Affectation dresseur située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise TrainerIntegrityError("Ligne de dresseur non reconnue.")
        prefix, key, source, trailing = parsed
        if key != "LoseText":
            continue
        if lose_assignment is not None or not source or source != source.strip():
            raise TrainerIntegrityError("LoseText absent, dupliqué ou ambigu.")
        lose_assignment = TrainerPbsAssignment(
            section=section_name,
            section_index=section_index,
            line_index=line_index,
            line_number=line_index + 1,
            prefix=prefix,
            source=source,
            trailing=trailing,
            newline="\r\n",
        )
    finish_section()
    sections = tuple(
        TrainerPbsSection(name, _section_id(name), assignment)
        for name, _index, assignment in pending
    )
    if not sections or len({section.compiled_id for section in sections}) != len(sections):
        raise TrainerIntegrityError("Sections dresseur absentes ou dupliquées.")
    return TrainerPbsDocument(lines, sections, _sha256(raw))


def _key_id(key: object) -> tuple[object, ...]:
    if not isinstance(key, RubyHashKey) or not isinstance(key.value, list) or len(key.value) != 3:
        raise TrainerIntegrityError("Clé composée de dresseur invalide.")
    trainer_type, real_name, version = key.value
    if not isinstance(trainer_type, str) or type(version) is not int:
        raise TrainerIntegrityError("Types de la clé dresseur invalides.")
    return trainer_type, _ruby_text(real_name, "Le nom dans la clé dresseur").text(), version


def _validate_trainer_object(
    key: object,
    section: TrainerPbsSection,
    value: object,
) -> RubyObject:
    if not isinstance(value, RubyObject) or value.class_name != TRAINER_CLASS:
        raise TrainerIntegrityError("Une entrée compilée n'est pas GameData::Trainer.")
    if tuple(value.ivars) != TRAINER_IVARS or _key_id(key) != section.compiled_id:
        raise TrainerIntegrityError("La structure ou l'identité compilée du dresseur a changé.")
    identifier = value.ivars["@id"]
    if not isinstance(identifier, list) or len(identifier) != 3:
        raise TrainerIntegrityError("L'identifiant compilé du dresseur a changé.")
    actual_id = (
        identifier[0],
        _ruby_text(identifier[1], "Le nom dans l'identifiant dresseur").text(),
        identifier[2],
    )
    if (
        actual_id != section.compiled_id
        or value.ivars["@trainer_type"] != section.compiled_id[0]
        or _ruby_text(value.ivars["@real_name"], "Le nom réel du dresseur").text()
        != section.compiled_id[1]
        or type(value.ivars["@version"]) is not int
        or value.ivars["@version"] != section.compiled_id[2]
        or not isinstance(value.ivars["@items"], list)
        or not isinstance(value.ivars["@pokemon"], list)
        or _ruby_text(value.ivars["@pbs_file_suffix"], "Le suffixe PBS dresseur").text()
        != ""
    ):
        raise TrainerIntegrityError("Les métadonnées compilées du dresseur ont changé.")
    lose_text = _ruby_text(value.ivars["@real_lose_text"], "Le LoseText compilé")
    if lose_text.text() != section.lose_text.source:
        raise TrainerIntegrityError("Le LoseText compilé ne correspond plus au PBS.")
    return value


def _analyze_sources(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _TrainerAnalysis:
    pbs = parse_trainer_pbs(pbs_raw)
    trainer_root = _load_marshal(compiled_raw, COMPILED_TRAINER_FILE)
    if not isinstance(trainer_root, dict) or len(trainer_root) != len(pbs.sections):
        raise TrainerIntegrityError("La racine de trainers.dat ne correspond pas aux sections PBS.")

    flattened: list[tuple[TrainerPbsSection, RubyString]] = []
    for section, (key, raw_object) in zip(pbs.sections, trainer_root.items()):
        trainer_object = _validate_trainer_object(key, section, raw_object)
        trainer_value = trainer_object.ivars["@real_lose_text"]
        if _reference_count(trainer_root, trainer_value) != 1:
            raise TrainerIntegrityError("Une chaîne trainers.dat est partagée ou ambiguë.")
        flattened.append((section, trainer_value))

    messages_root = _load_marshal(messages_raw, TRAINER_MESSAGES_FILE)
    if (
        not isinstance(messages_root, list)
        or len(messages_root) <= TRAINER_LOSE_MESSAGES_INDEX
        or not isinstance(messages_root[TRAINER_LOSE_MESSAGES_INDEX], dict)
    ):
        raise TrainerIntegrityError(
            "La banque TRAINER_SPEECHES_LOSE n'est pas à l'index v21.1 attendu."
        )
    source_counts: dict[str, int] = {}
    ordered_unique: list[str] = []
    for section, _value in flattened:
        source = section.lose_text.source
        source_counts[source] = source_counts.get(source, 0) + 1
        if source_counts[source] == 1:
            ordered_unique.append(source)
    message_items = list(messages_root[TRAINER_LOSE_MESSAGES_INDEX].items())
    if len(message_items) != len(ordered_unique):
        raise TrainerIntegrityError("La banque de défaite ne couvre pas exactement trainers.dat.")
    runtime_by_source: dict[str, tuple[RubyString, RubyString, int, int, int]] = {}
    for message_index, (source, (key, value)) in enumerate(zip(ordered_unique, message_items)):
        message_key = _ruby_text(key, "La clé TRAINER_SPEECHES_LOSE")
        message_value = _ruby_text(value, "La valeur TRAINER_SPEECHES_LOSE")
        if message_key.text() != source or message_value.text() != source:
            raise TrainerIntegrityError("L'ordre de la banque de défaite ne correspond plus.")
        key_references = _reference_count(messages_root, message_key)
        value_references = _reference_count(messages_root, message_value)
        if key_references < 1 or value_references < 1:
            raise TrainerIntegrityError("Une chaîne de la banque de défaite est introuvable.")
        runtime_by_source[source] = (
            message_key,
            message_value,
            message_index,
            key_references,
            value_references,
        )

    targets: dict[tuple[str, str, int], TrainerTarget] = {}
    for section, trainer_value in flattened:
        (
            message_key,
            message_value,
            message_index,
            key_references,
            value_references,
        ) = runtime_by_source[
            section.lose_text.source
        ]
        lookup = (section.name, "LoseText", 1)
        targets[lookup] = TrainerTarget(
            assignment=section.lose_text,
            trainer_value=trainer_value,
            trainer_path=(section.lose_text.section_index, "@real_lose_text"),
            message_key=message_key,
            message_value=message_value,
            message_path=(TRAINER_LOSE_MESSAGES_INDEX, "entry", message_index),
            source_usage_count=source_counts[section.lose_text.source],
            runtime_key_reference_count=key_references,
            runtime_value_reference_count=value_references,
        )
    return _TrainerAnalysis(pbs, trainer_root, messages_root, targets)


def _pbs_proof(analysis: _TrainerAnalysis, target: TrainerTarget) -> str:
    assignment = target.assignment
    proof = {
        "format": TRAINER_PBS_PROOF_FORMAT,
        "pbs_file": TRAINER_PBS_FILE,
        "file_sha256": analysis.pbs.file_sha256,
        "encoding": "utf-8-sig",
        "bom": "utf-8",
        "newline": "CRLF",
        "line_number": assignment.line_number,
        "section_index": assignment.section_index,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "key": "LoseText",
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
    analysis: _TrainerAnalysis,
    target: TrainerTarget,
    compiled_raw: bytes,
) -> str:
    proof = {
        "format": COMPILED_TRAINER_PROOF_FORMAT,
        "compiled_file": COMPILED_TRAINER_FILE,
        "file_sha256": _sha256(compiled_raw),
        "root_type": "Hash",
        "root_size": len(analysis.trainer_root),
        "root_graph_sha256": graph_sha256(analysis.trainer_root),
        "non_target_graph_sha256": graph_sha256(
            analysis.trainer_root, masked=(target.trainer_value,)
        ),
        "section_index": target.assignment.section_index,
        "section_sha256": _sha256(target.assignment.section.encode("utf-8")),
        "section_class": TRAINER_CLASS,
        "section_ivars": list(TRAINER_IVARS),
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.trainer_value.ivars),
        "target_value_sha256": _sha256(target.trainer_value.data),
        "target_reference_count": 1,
        "compiled_path": list(target.trainer_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _TrainerAnalysis,
    target: TrainerTarget,
    messages_raw: bytes,
) -> str:
    proof = {
        "format": TRAINER_RUNTIME_PROOF_FORMAT,
        "runtime_file": TRAINER_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "message_type_index": TRAINER_LOSE_MESSAGES_INDEX,
        "message_count": len(analysis.messages_root[TRAINER_LOSE_MESSAGES_INDEX]),
        "root_graph_sha256": graph_sha256(analysis.messages_root),
        "non_target_graph_sha256": graph_sha256(
            analysis.messages_root,
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


def build_trainer_entry_proofs(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> dict[tuple[str, str, int], TrainerEntryProof]:
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    return {
        key: TrainerEntryProof(
            source=target.assignment.source,
            pbs_structure=_pbs_proof(analysis, target),
            compiled_path=json.dumps(list(target.trainer_path), separators=(",", ":")),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(list(target.message_path), separators=(",", ":")),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
        for key, target in analysis.targets.items()
    }


def rebuild_trainer_payloads(
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
        raise TrainerIntegrityError("La traduction LoseText ne tient pas sur une ligne PBS.")
    try:
        translated = translation.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TrainerIntegrityError("La traduction LoseText n'est pas encodable en UTF-8.") from exc
    analysis = _analyze_sources(pbs_raw, compiled_raw, messages_raw)
    target_key = (section, "LoseText", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise TrainerIntegrityError("L'occurrence LoseText ne correspond plus à la source.")
    expected = build_trainer_entry_proofs(pbs_raw, compiled_raw, messages_raw)[target_key]
    if (
        expected.pbs_structure != pbs_structure
        or expected.compiled_path != compiled_path
        or expected.compiled_structure != compiled_structure
        or expected.runtime_path != runtime_path
        or expected.runtime_structure != runtime_structure
    ):
        raise TrainerIntegrityError("La preuve LoseText ne correspond plus aux trois sources.")
    if target.source_usage_count != 1:
        raise TrainerIntegrityError(
            "Ce LoseText est partagé par plusieurs dresseurs et reste volontairement bloqué."
        )
    if (
        target.runtime_key_reference_count != 1
        or target.runtime_value_reference_count != 1
    ):
        raise TrainerIntegrityError(
            "Ce LoseText partage un objet Marshal d'exécution et reste volontairement bloqué."
        )
    runtime_proof = json.loads(runtime_structure)
    if runtime_proof.get("target_value_equals_source") is not True:
        raise TrainerIntegrityError("La banque de défaite possède déjà une traduction différente.")
    if translation in {
        item.text()
        for item in analysis.messages_root[TRAINER_LOSE_MESSAGES_INDEX]
        if item is not target.message_key
    }:
        raise TrainerIntegrityError("La traduction LoseText entrerait en collision avec une autre clé.")

    pbs_lines = list(analysis.pbs.content_lines)
    assignment = target.assignment
    pbs_lines[assignment.line_index] = (
        assignment.prefix + translation + assignment.trailing + assignment.newline
    )
    rebuilt_pbs = b"\xef\xbb\xbf" + "".join(pbs_lines).encode("utf-8")

    compiled_before = graph_sha256(
        analysis.trainer_root, masked=(target.trainer_value,)
    )
    trainer_object = list(analysis.trainer_root.values())[assignment.section_index]
    trainer_object.ivars["@real_lose_text"] = RubyString(
        translated, dict(target.trainer_value.ivars)
    )
    rebuilt_compiled_target = trainer_object.ivars["@real_lose_text"]
    if graph_sha256(
        analysis.trainer_root, masked=(rebuilt_compiled_target,)
    ) != compiled_before:
        raise TrainerIntegrityError("La mutation modifierait trainers.dat hors chaîne ciblée.")
    rebuilt_compiled = dumps(analysis.trainer_root)

    runtime_before = graph_sha256(
        analysis.messages_root, masked=(target.message_key, target.message_value)
    )
    replacement_hash: dict = {}
    old_hash = analysis.messages_root[TRAINER_LOSE_MESSAGES_INDEX]
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_hash[
                RubyString(translated, dict(target.message_key.ivars))
            ] = RubyString(translated, dict(target.message_value.ivars))
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[TRAINER_LOSE_MESSAGES_INDEX] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[target.message_path[-1]]
    if graph_sha256(
        analysis.messages_root, masked=(rebuilt_key, rebuilt_value)
    ) != runtime_before:
        raise TrainerIntegrityError("La mutation modifierait la banque de défaite hors cible.")
    rebuilt_messages = dumps(analysis.messages_root)

    rebuilt = _analyze_sources(rebuilt_pbs, rebuilt_compiled, rebuilt_messages)
    rebuilt_target = rebuilt.targets.get(target_key)
    if (
        rebuilt_target is None
        or rebuilt_target.assignment.source != translation
        or rebuilt_target.trainer_value.text() != translation
        or rebuilt_target.message_key.text() != translation
        or rebuilt_target.message_value.text() != translation
        or rebuilt_target.trainer_path != target.trainer_path
        or rebuilt_target.message_path != target.message_path
    ):
        raise TrainerIntegrityError("La relecture ne retrouve pas le LoseText exact.")
    return {
        TRAINER_PBS_FILE: rebuilt_pbs,
        COMPILED_TRAINER_FILE: rebuilt_compiled,
        TRAINER_MESSAGES_FILE: rebuilt_messages,
    }


def extract_trainer_target_texts(
    pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
) -> tuple[str, str, str]:
    target = _analyze_sources(pbs_raw, compiled_raw, messages_raw).targets.get(
        (section, "LoseText", 1)
    )
    if target is None:
        raise TrainerIntegrityError("L'occurrence LoseText est introuvable.")
    return (
        target.assignment.source,
        target.trainer_value.text(),
        target.message_value.text(),
    )
