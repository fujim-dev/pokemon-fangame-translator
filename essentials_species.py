# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée d'une entrée Pokédex v21.1.

Le module ne lance jamais Ruby. Il relie les espèces de base de
``PBS/pokemon.txt`` et les formes de ``PBS/pokemon_forms.txt`` à
``Data/species.dat``, puis à la banque ``POKEDEX_ENTRIES`` de
``Data/messages_core.dat``. La porte privée n'autorise qu'une entrée de base
non partagée ; les formes restent un contexte intégralement validé et immuable.
"""
from __future__ import annotations

from collections import Counter
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


SPECIES_PBS_FILE = "PBS/pokemon.txt"
SPECIES_FORMS_PBS_FILE = "PBS/pokemon_forms.txt"
COMPILED_SPECIES_FILE = "Data/species.dat"
SPECIES_MESSAGES_FILE = "Data/messages_core.dat"
POKEDEX_ENTRIES_MESSAGES_INDEX = 3
SPECIES_CLASS = "GameData::Species"
SPECIES_PBS_PROOF_FORMAT = "pft_v21_1_species_pokedex_pbs_v1"
COMPILED_SPECIES_PROOF_FORMAT = "pft_v21_1_compiled_species_pokedex_v1"
SPECIES_RUNTIME_PROOF_FORMAT = "pft_v21_1_species_pokedex_runtime_v1"
SPECIES_IVARS = (
    "@id",
    "@species",
    "@form",
    "@real_name",
    "@real_form_name",
    "@real_category",
    "@real_pokedex_entry",
    "@pokedex_form",
    "@types",
    "@base_stats",
    "@evs",
    "@base_exp",
    "@growth_rate",
    "@gender_ratio",
    "@catch_rate",
    "@happiness",
    "@moves",
    "@tutor_moves",
    "@egg_moves",
    "@abilities",
    "@hidden_abilities",
    "@wild_item_common",
    "@wild_item_uncommon",
    "@wild_item_rare",
    "@egg_groups",
    "@hatch_steps",
    "@incense",
    "@offspring",
    "@evolutions",
    "@height",
    "@weight",
    "@color",
    "@shape",
    "@habitat",
    "@generation",
    "@flags",
    "@mega_stone",
    "@mega_move",
    "@unmega_form",
    "@mega_message",
    "@pbs_file_suffix",
)


class SpeciesIntegrityError(ValueError):
    """La quadruple correspondance d'une entrée Pokédex n'est pas démontrable."""


@dataclass(frozen=True)
class SpeciesPbsAssignment:
    section: str
    section_index: int
    line_index: int
    line_number: int
    prefix: str
    source: str
    trailing: str
    newline: str


@dataclass(frozen=True)
class SpeciesBaseSection:
    identifier: str
    section_index: int
    name: str
    pokedex: SpeciesPbsAssignment


@dataclass(frozen=True)
class SpeciesFormSection:
    identifier: str
    section_index: int
    species: str
    form: int
    pokedex: SpeciesPbsAssignment | None


@dataclass(frozen=True)
class SpeciesPbsDocument:
    relative_file: str
    content_lines: tuple[str, ...]
    base_sections: tuple[SpeciesBaseSection, ...]
    form_sections: tuple[SpeciesFormSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class SpeciesTarget:
    assignment: SpeciesPbsAssignment
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
class SpeciesEntryProof:
    source: str
    pbs_structure: str
    compiled_path: str
    compiled_structure: str
    runtime_path: str
    runtime_structure: str


@dataclass
class _SpeciesAnalysis:
    base_pbs: SpeciesPbsDocument
    forms_pbs: SpeciesPbsDocument
    compiled_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], SpeciesTarget]
    compiled_graph_sha256: str
    messages_graph_sha256: str
    explicit_form_pokedex_count: int
    inherited_form_pokedex_count: int


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_marshal(raw: bytes, label: str) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise SpeciesIntegrityError(f"{label} n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise SpeciesIntegrityError(
            f"{label} est illisible sans exécuter Ruby."
        ) from exc
    if reader.pos != len(raw) or dumps(root) != raw:
        raise SpeciesIntegrityError(
            f"Le lecteur/écrivain Marshal ne reproduit pas exactement {label}."
        )
    return root


def _ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise SpeciesIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpeciesIntegrityError(f"{label} n'est pas une chaîne UTF-8 valide.") from exc
    return value


def _reference_counts(root: object) -> Counter[int]:
    """Compte en un passage les références d'objets du graphe Marshal."""
    counts: Counter[int] = Counter()
    expanded: set[int] = set()

    def visit(value: object) -> None:
        if isinstance(value, RubyHashKey):
            visit(value.value)
            return
        if not isinstance(value, (RubyString, RubyUserDefined, RubyObject, list, dict)):
            return
        identity = id(value)
        counts[identity] += 1
        if identity in expanded:
            return
        expanded.add(identity)
        if isinstance(value, (RubyString, RubyUserDefined, RubyObject)):
            for key, child in value.ivars.items():
                visit(key)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        else:
            for key, child in value.items():
                visit(key)
                visit(child)

    visit(root)
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


def _decode_pbs(raw: bytes, relative_file: str) -> tuple[str, ...]:
    if not raw.startswith(b"\xef\xbb\xbf"):
        raise SpeciesIntegrityError(f"{relative_file} doit conserver son BOM UTF-8.")
    payload = raw[3:]
    if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(
        b"\r\n", b""
    ):
        raise SpeciesIntegrityError(f"{relative_file} doit conserver exclusivement ses CRLF.")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SpeciesIntegrityError(f"{relative_file} n'est pas un UTF-8 valide.") from exc
    lines = tuple(content.splitlines(keepends=True))
    if not lines or any(not line.endswith("\r\n") for line in lines):
        raise SpeciesIntegrityError(
            f"Chaque ligne de {relative_file} doit conserver son CRLF."
        )
    return lines


def parse_species_base_pbs(raw: bytes) -> SpeciesPbsDocument:
    lines = _decode_pbs(raw, SPECIES_PBS_FILE)
    sections: list[SpeciesBaseSection] = []
    identifier = ""
    section_index = -1
    name: str | None = None
    pokedex: SpeciesPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal name, pokedex
        if section_index < 0:
            return
        if name is None or pokedex is None:
            raise SpeciesIntegrityError(
                "Une espèce de base ne possède pas exactement Name et Pokedex."
            )
        sections.append(SpeciesBaseSection(identifier, section_index, name, pokedex))
        name = None
        pokedex = None

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
            if not re.fullmatch(r"[A-Z][A-Za-z0-9_]*", identifier):
                raise SpeciesIntegrityError("Identifiant d'espèce de base non canonique.")
            continue
        if section_index < 0:
            raise SpeciesIntegrityError("Affectation d'espèce située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise SpeciesIntegrityError("Ligne d'espèce de base non reconnue.")
        prefix, key, source, trailing = parsed
        if key == "Name":
            if name is not None or not source or source != source.strip():
                raise SpeciesIntegrityError("Name d'espèce absent, dupliqué ou ambigu.")
            name = source
        elif key == "Pokedex":
            if pokedex is not None or not source or source != source.strip():
                raise SpeciesIntegrityError("Pokedex d'espèce absent, dupliqué ou ambigu.")
            pokedex = SpeciesPbsAssignment(
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
        raise SpeciesIntegrityError("Sections d'espèces de base absentes ou dupliquées.")
    return SpeciesPbsDocument(
        SPECIES_PBS_FILE,
        lines,
        tuple(sections),
        (),
        _sha256(raw),
    )


def parse_species_forms_pbs(raw: bytes) -> SpeciesPbsDocument:
    lines = _decode_pbs(raw, SPECIES_FORMS_PBS_FILE)
    sections: list[SpeciesFormSection] = []
    identifier = ""
    species = ""
    form = 0
    section_index = -1
    pokedex: SpeciesPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal pokedex
        if section_index < 0:
            return
        sections.append(
            SpeciesFormSection(identifier, section_index, species, form, pokedex)
        )
        pokedex = None

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
            identity_match = re.fullmatch(
                r"([A-Z][A-Za-z0-9_]*),([1-9][0-9]*)", identifier
            )
            if identity_match is None:
                raise SpeciesIntegrityError("Identifiant de forme d'espèce non canonique.")
            species = identity_match.group(1)
            form = int(identity_match.group(2))
            continue
        if section_index < 0:
            raise SpeciesIntegrityError("Affectation de forme située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise SpeciesIntegrityError("Ligne de forme d'espèce non reconnue.")
        prefix, key, source, trailing = parsed
        if key == "Pokedex":
            if pokedex is not None or not source or source != source.strip():
                raise SpeciesIntegrityError(
                    "Pokedex de forme dupliqué, vide ou ambigu."
                )
            pokedex = SpeciesPbsAssignment(
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
        raise SpeciesIntegrityError("Sections de formes absentes ou dupliquées.")
    return SpeciesPbsDocument(
        SPECIES_FORMS_PBS_FILE,
        lines,
        (),
        tuple(sections),
        _sha256(raw),
    )


def _validate_compiled_entry(
    key: object,
    value: object,
    *,
    expected_key: str,
    species: str,
    form: int,
    expected_pokedex: str,
    expected_name: str | None,
) -> RubyObject:
    if not isinstance(key, str) or key != expected_key:
        raise SpeciesIntegrityError("La clé compilée d'espèce a changé.")
    if not isinstance(value, RubyObject) or value.class_name != SPECIES_CLASS:
        raise SpeciesIntegrityError("Une entrée compilée n'est pas GameData::Species.")
    if tuple(value.ivars) != SPECIES_IVARS:
        raise SpeciesIntegrityError("La structure compilée d'une espèce a changé.")
    if (
        value.ivars["@id"] != expected_key
        or value.ivars["@species"] != species
        or value.ivars["@form"] != form
        or _ruby_text(
            value.ivars["@real_pokedex_entry"], "L'entrée Pokédex compilée"
        ).text()
        != expected_pokedex
        or _ruby_text(value.ivars["@pbs_file_suffix"], "Le suffixe PBS d'espèce").text()
        != ""
    ):
        raise SpeciesIntegrityError("L'identité ou le texte compilé de l'espèce a changé.")
    if expected_name is not None and _ruby_text(
        value.ivars["@real_name"], "Le nom compilé d'espèce"
    ).text() != expected_name:
        raise SpeciesIntegrityError("Le nom compilé de l'espèce ne correspond plus au PBS.")
    return value


def _analyze_sources(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _SpeciesAnalysis:
    base_pbs = parse_species_base_pbs(base_pbs_raw)
    forms_pbs = parse_species_forms_pbs(forms_pbs_raw)
    base_by_id = {section.identifier: section for section in base_pbs.base_sections}
    for form_section in forms_pbs.form_sections:
        if form_section.species not in base_by_id:
            raise SpeciesIntegrityError("Une forme référence une espèce de base absente.")

    expected_keys = [section.identifier for section in base_pbs.base_sections]
    expected_keys.extend(
        f"{section.species}_{section.form}" for section in forms_pbs.form_sections
    )
    if len(set(expected_keys)) != len(expected_keys):
        raise SpeciesIntegrityError("Les identités compilées d'espèce sont ambiguës.")
    compiled_root = _load_marshal(compiled_raw, COMPILED_SPECIES_FILE)
    if (
        not isinstance(compiled_root, dict)
        or len(compiled_root) != len(expected_keys)
        or list(compiled_root) != expected_keys
    ):
        raise SpeciesIntegrityError(
            "La racine species.dat ne correspond pas exactement aux deux PBS."
        )

    flattened: list[tuple[SpeciesPbsAssignment | None, RubyString]] = []
    base_values: dict[str, RubyString] = {}
    for section, (key, raw_object) in zip(
        base_pbs.base_sections,
        list(compiled_root.items())[: len(base_pbs.base_sections)],
    ):
        compiled_object = _validate_compiled_entry(
            key,
            raw_object,
            expected_key=section.identifier,
            species=section.identifier,
            form=0,
            expected_pokedex=section.pokedex.source,
            expected_name=section.name,
        )
        value = compiled_object.ivars["@real_pokedex_entry"]
        base_values[section.identifier] = value
        flattened.append((section.pokedex, value))

    explicit_count = 0
    inherited_count = 0
    form_items = list(compiled_root.items())[len(base_pbs.base_sections) :]
    for section, (key, raw_object) in zip(forms_pbs.form_sections, form_items):
        expected_pokedex = (
            section.pokedex.source
            if section.pokedex is not None
            else base_by_id[section.species].pokedex.source
        )
        compiled_object = _validate_compiled_entry(
            key,
            raw_object,
            expected_key=f"{section.species}_{section.form}",
            species=section.species,
            form=section.form,
            expected_pokedex=expected_pokedex,
            expected_name=None,
        )
        value = compiled_object.ivars["@real_pokedex_entry"]
        if section.pokedex is None:
            inherited_count += 1
            if value is not base_values[section.species]:
                raise SpeciesIntegrityError(
                    "Une forme sans Pokedex ne partage plus exactement la valeur héritée."
                )
        else:
            explicit_count += 1
        flattened.append((section.pokedex, value))

    messages_root = _load_marshal(messages_raw, SPECIES_MESSAGES_FILE)
    if (
        not isinstance(messages_root, list)
        or len(messages_root) <= POKEDEX_ENTRIES_MESSAGES_INDEX
        or not isinstance(messages_root[POKEDEX_ENTRIES_MESSAGES_INDEX], dict)
    ):
        raise SpeciesIntegrityError(
            "La banque POKEDEX_ENTRIES n'est pas à l'index v21.1 attendu."
        )

    source_counts: Counter[str] = Counter(value.text() for _assignment, value in flattened)
    ordered_unique: list[str] = []
    seen_sources: set[str] = set()
    for _assignment, value in flattened:
        source = value.text()
        if source not in seen_sources:
            seen_sources.add(source)
            ordered_unique.append(source)
    message_items = list(messages_root[POKEDEX_ENTRIES_MESSAGES_INDEX].items())
    if len(message_items) != len(ordered_unique):
        raise SpeciesIntegrityError(
            "La banque POKEDEX_ENTRIES ne couvre pas exactement species.dat."
        )

    runtime_by_source: dict[str, tuple[RubyString, RubyString, int]] = {}
    for message_index, (source, (key, value)) in enumerate(zip(ordered_unique, message_items)):
        message_key = _ruby_text(key, "La clé POKEDEX_ENTRIES")
        message_value = _ruby_text(value, "La valeur POKEDEX_ENTRIES")
        if (
            message_key is message_value
            or message_key.text() != source
            or message_value.text() != source
        ):
            raise SpeciesIntegrityError(
                "L'ordre ou les objets de la banque POKEDEX_ENTRIES ont changé."
            )
        runtime_by_source[source] = (message_key, message_value, message_index)

    compiled_references = _reference_counts(compiled_root)
    runtime_references = _reference_counts(messages_root)
    targets: dict[tuple[str, str, int], SpeciesTarget] = {}
    for section, (_key, compiled_object) in zip(
        base_pbs.base_sections,
        list(compiled_root.items())[: len(base_pbs.base_sections)],
    ):
        compiled_value = compiled_object.ivars["@real_pokedex_entry"]
        message_key, message_value, message_index = runtime_by_source[
            section.pokedex.source
        ]
        targets[(section.identifier, "Pokedex", 1)] = SpeciesTarget(
            assignment=section.pokedex,
            compiled_value=compiled_value,
            compiled_path=(section.section_index, "@real_pokedex_entry"),
            compiled_reference_count=compiled_references[id(compiled_value)],
            message_key=message_key,
            message_value=message_value,
            message_path=(POKEDEX_ENTRIES_MESSAGES_INDEX, "entry", message_index),
            source_usage_count=source_counts[section.pokedex.source],
            runtime_key_reference_count=runtime_references[id(message_key)],
            runtime_value_reference_count=runtime_references[id(message_value)],
        )

    return _SpeciesAnalysis(
        base_pbs,
        forms_pbs,
        compiled_root,
        messages_root,
        targets,
        graph_sha256(compiled_root),
        graph_sha256(messages_root),
        explicit_count,
        inherited_count,
    )


def _pbs_proof(analysis: _SpeciesAnalysis, target: SpeciesTarget) -> str:
    assignment = target.assignment
    proof = {
        "format": SPECIES_PBS_PROOF_FORMAT,
        "pbs_file": SPECIES_PBS_FILE,
        "forms_pbs_file": SPECIES_FORMS_PBS_FILE,
        "file_sha256": analysis.base_pbs.file_sha256,
        "forms_file_sha256": analysis.forms_pbs.file_sha256,
        "encoding": "utf-8-sig",
        "bom": "utf-8",
        "newline": "CRLF",
        "line_number": assignment.line_number,
        "section_index": assignment.section_index,
        "base_section_count": len(analysis.base_pbs.base_sections),
        "form_section_count": len(analysis.forms_pbs.form_sections),
        "explicit_form_pokedex_count": analysis.explicit_form_pokedex_count,
        "inherited_form_pokedex_count": analysis.inherited_form_pokedex_count,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "key": "Pokedex",
        "key_occurrence": 1,
        "prefix_sha256": _sha256(assignment.prefix.encode("utf-8")),
        "trailing_sha256": _sha256(assignment.trailing.encode("utf-8")),
        "line_sha256": _sha256(
            analysis.base_pbs.content_lines[assignment.line_index].encode("utf-8")
        ),
        "source_sha256": _sha256(assignment.source.encode("utf-8")),
        "source_usage_count": target.source_usage_count,
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _compiled_proof(
    analysis: _SpeciesAnalysis,
    target: SpeciesTarget,
    compiled_raw: bytes,
) -> str:
    proof = {
        "format": COMPILED_SPECIES_PROOF_FORMAT,
        "compiled_file": COMPILED_SPECIES_FILE,
        "file_sha256": _sha256(compiled_raw),
        "root_type": "Hash",
        "root_size": len(analysis.compiled_root),
        "base_species_count": len(analysis.base_pbs.base_sections),
        "form_count": len(analysis.forms_pbs.form_sections),
        "root_graph_sha256": analysis.compiled_graph_sha256,
        "non_target_section_graph_sha256": graph_sha256(
            list(analysis.compiled_root.values())[target.assignment.section_index],
            masked=(target.compiled_value,),
        ),
        "section_index": target.assignment.section_index,
        "section_sha256": _sha256(target.assignment.section.encode("utf-8")),
        "section_class": SPECIES_CLASS,
        "section_ivars": list(SPECIES_IVARS),
        "species": target.assignment.section,
        "form": 0,
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.compiled_value.ivars),
        "target_value_sha256": _sha256(target.compiled_value.data),
        "target_reference_count": target.compiled_reference_count,
        "compiled_path": list(target.compiled_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _SpeciesAnalysis,
    target: SpeciesTarget,
    messages_raw: bytes,
) -> str:
    proof = {
        "format": SPECIES_RUNTIME_PROOF_FORMAT,
        "runtime_file": SPECIES_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "message_type_index": POKEDEX_ENTRIES_MESSAGES_INDEX,
        "message_count": len(
            analysis.messages_root[POKEDEX_ENTRIES_MESSAGES_INDEX]
        ),
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


def build_species_pokedex_proofs(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> dict[tuple[str, str, int], SpeciesEntryProof]:
    analysis = _analyze_sources(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    )
    return {
        key: SpeciesEntryProof(
            source=target.assignment.source,
            pbs_structure=_pbs_proof(analysis, target),
            compiled_path=json.dumps(list(target.compiled_path), separators=(",", ":")),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(list(target.message_path), separators=(",", ":")),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
        for key, target in analysis.targets.items()
    }


def rebuild_species_pokedex_payloads(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
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
        raise SpeciesIntegrityError("La traduction Pokédex ne tient pas sur une ligne PBS.")
    translated = translation.encode("utf-8")
    analysis = _analyze_sources(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    )
    target_key = (section, "Pokedex", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise SpeciesIntegrityError("L'entrée Pokédex ne correspond plus à la source.")
    expected = build_species_pokedex_proofs(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    )[target_key]
    if (
        expected.pbs_structure != pbs_structure
        or expected.compiled_path != compiled_path
        or expected.compiled_structure != compiled_structure
        or expected.runtime_path != runtime_path
        or expected.runtime_structure != runtime_structure
    ):
        raise SpeciesIntegrityError(
            "La preuve Pokédex ne correspond plus aux quatre sources."
        )
    if target.source_usage_count != 1 or target.compiled_reference_count != 1:
        raise SpeciesIntegrityError(
            "Cette entrée Pokédex est partagée ou héritée et reste bloquée."
        )
    if target.runtime_key_reference_count != 1 or target.runtime_value_reference_count != 1:
        raise SpeciesIntegrityError(
            "Cette entrée Pokédex partage un objet Marshal et reste bloquée."
        )
    if json.loads(runtime_structure).get("target_value_equals_source") is not True:
        raise SpeciesIntegrityError("La banque possède déjà une traduction différente.")
    if translation in {
        item.text()
        for item in analysis.messages_root[POKEDEX_ENTRIES_MESSAGES_INDEX]
        if item is not target.message_key
    }:
        raise SpeciesIntegrityError("La traduction entrerait en collision avec une autre clé.")

    assignment = target.assignment
    pbs_lines = list(analysis.base_pbs.content_lines)
    pbs_lines[assignment.line_index] = (
        assignment.prefix + translation + assignment.trailing + assignment.newline
    )
    rebuilt_pbs = b"\xef\xbb\xbf" + "".join(pbs_lines).encode("utf-8")

    compiled_before = graph_sha256(
        analysis.compiled_root, masked=(target.compiled_value,)
    )
    compiled_object = list(analysis.compiled_root.values())[assignment.section_index]
    compiled_object.ivars["@real_pokedex_entry"] = RubyString(
        translated, dict(target.compiled_value.ivars)
    )
    rebuilt_compiled_target = compiled_object.ivars["@real_pokedex_entry"]
    if graph_sha256(
        analysis.compiled_root, masked=(rebuilt_compiled_target,)
    ) != compiled_before:
        raise SpeciesIntegrityError("La mutation modifierait species.dat hors cible.")
    rebuilt_compiled = dumps(analysis.compiled_root)

    runtime_before = graph_sha256(
        analysis.messages_root, masked=(target.message_key, target.message_value)
    )
    replacement_hash: dict = {}
    old_hash = analysis.messages_root[POKEDEX_ENTRIES_MESSAGES_INDEX]
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_hash[RubyString(translated, dict(old_key.ivars))] = RubyString(
                translated, dict(old_value.ivars)
            )
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[POKEDEX_ENTRIES_MESSAGES_INDEX] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[target.message_path[-1]]
    if graph_sha256(
        analysis.messages_root, masked=(rebuilt_key, rebuilt_value)
    ) != runtime_before:
        raise SpeciesIntegrityError("La mutation modifierait la banque core hors cible.")
    rebuilt_messages = dumps(analysis.messages_root)

    rebuilt = _analyze_sources(
        rebuilt_pbs, forms_pbs_raw, rebuilt_compiled, rebuilt_messages
    )
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
        raise SpeciesIntegrityError("La relecture ne retrouve pas l'entrée Pokédex exacte.")
    return {
        SPECIES_PBS_FILE: rebuilt_pbs,
        COMPILED_SPECIES_FILE: rebuilt_compiled,
        SPECIES_MESSAGES_FILE: rebuilt_messages,
    }


def extract_species_pokedex_texts(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
) -> tuple[str, str, str]:
    target = _analyze_sources(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    ).targets.get((section, "Pokedex", 1))
    if target is None:
        raise SpeciesIntegrityError("L'entrée Pokédex est introuvable.")
    return (
        target.assignment.source,
        target.compiled_value.text(),
        target.message_value.text(),
    )
