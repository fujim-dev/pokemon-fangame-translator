# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preuve privée d'une catégorie Pokédex d'espèce de base v21.1.

La clé ``Category`` de ``PBS/pokemon.txt`` est un texte visible, contrairement
à la clé homonyme et technique de ``PBS/moves.txt``. Ce module ne lance jamais
Ruby. Il corrèle exactement le PBS de base, les éventuels héritages de
``pokemon_forms.txt``, ``Data/species.dat`` et la banque SPECIES_CATEGORIES de
``Data/messages_core.dat``. Une valeur partagée ou héritée reste extractible,
mais sa reconstruction privée est refusée.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import re

from essentials_phone import graph_sha256
from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_CLASS,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_IVARS,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SpeciesEntryProof,
    SpeciesIntegrityError,
    SpeciesPbsAssignment,
    _decode_pbs,
    _load_marshal,
    _parse_assignment,
    _reference_counts,
    _ruby_text,
    _sha256,
)
from ruby_marshal_reader import RubyObject, RubyString
from ruby_marshal_writer import dumps


SPECIES_CATEGORY_MESSAGES_INDEX = 2
SPECIES_CATEGORY_PBS_PROOF_FORMAT = "pft_v21_1_species_category_pbs_v1"
COMPILED_SPECIES_CATEGORY_PROOF_FORMAT = (
    "pft_v21_1_compiled_species_category_v1"
)
SPECIES_CATEGORY_RUNTIME_PROOF_FORMAT = (
    "pft_v21_1_species_category_runtime_v1"
)


@dataclass(frozen=True)
class SpeciesCategoryBaseSection:
    identifier: str
    section_index: int
    name: str
    category: SpeciesPbsAssignment


@dataclass(frozen=True)
class SpeciesCategoryFormSection:
    identifier: str
    section_index: int
    species: str
    form: int
    category: SpeciesPbsAssignment | None


@dataclass(frozen=True)
class SpeciesCategoryPbsDocument:
    relative_file: str
    content_lines: tuple[str, ...]
    base_sections: tuple[SpeciesCategoryBaseSection, ...]
    form_sections: tuple[SpeciesCategoryFormSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class SpeciesCategoryTarget:
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


@dataclass
class _SpeciesCategoryAnalysis:
    base_pbs: SpeciesCategoryPbsDocument
    forms_pbs: SpeciesCategoryPbsDocument
    compiled_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], SpeciesCategoryTarget]
    compiled_graph_sha256: str
    messages_graph_sha256: str
    explicit_form_category_count: int
    inherited_form_category_count: int


def _assignment(
    *,
    identifier: str,
    section_index: int,
    line_index: int,
    prefix: str,
    source: str,
    trailing: str,
) -> SpeciesPbsAssignment:
    return SpeciesPbsAssignment(
        section=identifier,
        section_index=section_index,
        line_index=line_index,
        line_number=line_index + 1,
        prefix=prefix,
        source=source,
        trailing=trailing,
        newline="\r\n",
    )


def parse_species_category_base_pbs(raw: bytes) -> SpeciesCategoryPbsDocument:
    lines = _decode_pbs(raw, SPECIES_PBS_FILE)
    sections: list[SpeciesCategoryBaseSection] = []
    identifier = ""
    section_index = -1
    name: str | None = None
    category: SpeciesPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal name, category
        if section_index < 0:
            return
        if name is None or category is None:
            raise SpeciesIntegrityError(
                "Une espèce de base ne possède pas exactement Name et Category."
            )
        sections.append(
            SpeciesCategoryBaseSection(identifier, section_index, name, category)
        )
        name = None
        category = None

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
            if re.fullmatch(r"[A-Z][A-Za-z0-9_]*", identifier) is None:
                raise SpeciesIntegrityError(
                    "Identifiant d'espèce de base non canonique."
                )
            continue
        if section_index < 0:
            raise SpeciesIntegrityError("Affectation d'espèce située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise SpeciesIntegrityError("Ligne d'espèce de base non reconnue.")
        prefix, key, source, trailing = parsed
        if key == "Name":
            if name is not None or not source or source != source.strip():
                raise SpeciesIntegrityError(
                    "Name d'espèce absent, dupliqué ou ambigu."
                )
            name = source
        elif key == "Category":
            if category is not None or not source or source != source.strip():
                raise SpeciesIntegrityError(
                    "Category d'espèce absente, dupliquée ou ambiguë."
                )
            category = _assignment(
                identifier=identifier,
                section_index=section_index,
                line_index=line_index,
                prefix=prefix,
                source=source,
                trailing=trailing,
            )
    finish_section()
    identifiers = [section.identifier for section in sections]
    if not sections or len(set(identifiers)) != len(identifiers):
        raise SpeciesIntegrityError("Sections d'espèces de base absentes ou dupliquées.")
    return SpeciesCategoryPbsDocument(
        SPECIES_PBS_FILE,
        lines,
        tuple(sections),
        (),
        _sha256(raw),
    )


def parse_species_category_forms_pbs(raw: bytes) -> SpeciesCategoryPbsDocument:
    lines = _decode_pbs(raw, SPECIES_FORMS_PBS_FILE)
    sections: list[SpeciesCategoryFormSection] = []
    identifier = ""
    species = ""
    form = 0
    section_index = -1
    category: SpeciesPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal category
        if section_index < 0:
            return
        sections.append(
            SpeciesCategoryFormSection(
                identifier, section_index, species, form, category
            )
        )
        category = None

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
                raise SpeciesIntegrityError(
                    "Identifiant de forme d'espèce non canonique."
                )
            species = identity_match.group(1)
            form = int(identity_match.group(2))
            continue
        if section_index < 0:
            raise SpeciesIntegrityError("Affectation de forme située hors section.")
        parsed = _parse_assignment(body)
        if parsed is None:
            raise SpeciesIntegrityError("Ligne de forme d'espèce non reconnue.")
        prefix, key, source, trailing = parsed
        if key == "Category":
            if category is not None or not source or source != source.strip():
                raise SpeciesIntegrityError(
                    "Category de forme dupliquée, vide ou ambiguë."
                )
            category = _assignment(
                identifier=identifier,
                section_index=section_index,
                line_index=line_index,
                prefix=prefix,
                source=source,
                trailing=trailing,
            )
    finish_section()
    identifiers = [section.identifier for section in sections]
    if not sections or len(set(identifiers)) != len(identifiers):
        raise SpeciesIntegrityError("Sections de formes absentes ou dupliquées.")
    return SpeciesCategoryPbsDocument(
        SPECIES_FORMS_PBS_FILE,
        lines,
        (),
        tuple(sections),
        _sha256(raw),
    )


def _compiled_entry(
    key: object,
    value: object,
    *,
    expected_key: str,
    species: str,
    form: int,
    expected_name: str,
    expected_category: str,
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
        or _ruby_text(value.ivars["@real_name"], "Le nom compilé d'espèce").text()
        != expected_name
        or _ruby_text(
            value.ivars["@real_category"], "La catégorie compilée d'espèce"
        ).text()
        != expected_category
        or _ruby_text(
            value.ivars["@pbs_file_suffix"], "Le suffixe PBS d'espèce"
        ).text()
        != ""
    ):
        raise SpeciesIntegrityError(
            "L'identité, le nom ou la catégorie compilée de l'espèce a changé."
        )
    return value


def _analyze_sources(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _SpeciesCategoryAnalysis:
    base_pbs = parse_species_category_base_pbs(base_pbs_raw)
    forms_pbs = parse_species_category_forms_pbs(forms_pbs_raw)
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
    compiled_items = list(compiled_root.items())
    for section, (key, raw_object) in zip(
        base_pbs.base_sections,
        compiled_items[: len(base_pbs.base_sections)],
    ):
        compiled_object = _compiled_entry(
            key,
            raw_object,
            expected_key=section.identifier,
            species=section.identifier,
            form=0,
            expected_name=section.name,
            expected_category=section.category.source,
        )
        value = compiled_object.ivars["@real_category"]
        base_values[section.identifier] = value
        flattened.append((section.category, value))

    explicit_count = 0
    inherited_count = 0
    form_items = compiled_items[len(base_pbs.base_sections) :]
    for section, (key, raw_object) in zip(forms_pbs.form_sections, form_items):
        base_section = base_by_id[section.species]
        expected_category = (
            section.category.source
            if section.category is not None
            else base_section.category.source
        )
        compiled_object = _compiled_entry(
            key,
            raw_object,
            expected_key=f"{section.species}_{section.form}",
            species=section.species,
            form=section.form,
            expected_name=base_section.name,
            expected_category=expected_category,
        )
        value = compiled_object.ivars["@real_category"]
        if section.category is None:
            inherited_count += 1
            if value is not base_values[section.species]:
                raise SpeciesIntegrityError(
                    "Une forme sans Category ne partage plus exactement la valeur héritée."
                )
        else:
            explicit_count += 1
        flattened.append((section.category, value))

    messages_root = _load_marshal(messages_raw, SPECIES_MESSAGES_FILE)
    if (
        not isinstance(messages_root, list)
        or len(messages_root) <= SPECIES_CATEGORY_MESSAGES_INDEX
        or not isinstance(messages_root[SPECIES_CATEGORY_MESSAGES_INDEX], dict)
    ):
        raise SpeciesIntegrityError(
            "La banque SPECIES_CATEGORIES n'est pas à l'index v21.1 attendu."
        )

    source_counts: Counter[str] = Counter(
        value.text() for _assignment_value, value in flattened
    )
    ordered_unique = list(
        dict.fromkeys(value.text() for _assignment_value, value in flattened)
    )
    message_items = list(messages_root[SPECIES_CATEGORY_MESSAGES_INDEX].items())
    if len(message_items) != len(ordered_unique):
        raise SpeciesIntegrityError(
            "La banque SPECIES_CATEGORIES ne couvre pas exactement species.dat."
        )
    runtime_by_source: dict[str, tuple[RubyString, RubyString, int]] = {}
    for message_index, (source, (key, value)) in enumerate(
        zip(ordered_unique, message_items)
    ):
        message_key = _ruby_text(key, "La clé SPECIES_CATEGORIES")
        message_value = _ruby_text(value, "La valeur SPECIES_CATEGORIES")
        if (
            message_key is message_value
            or message_key.text() != source
            or message_value.text() != source
        ):
            raise SpeciesIntegrityError(
                "L'ordre ou les objets de la banque SPECIES_CATEGORIES ont changé."
            )
        runtime_by_source[source] = (message_key, message_value, message_index)

    compiled_references = _reference_counts(compiled_root)
    runtime_references = _reference_counts(messages_root)
    targets: dict[tuple[str, str, int], SpeciesCategoryTarget] = {}
    for section, (_key, compiled_object) in zip(
        base_pbs.base_sections,
        compiled_items[: len(base_pbs.base_sections)],
    ):
        compiled_value = compiled_object.ivars["@real_category"]
        message_key, message_value, message_index = runtime_by_source[
            section.category.source
        ]
        targets[(section.identifier, "Category", 1)] = SpeciesCategoryTarget(
            assignment=section.category,
            compiled_value=compiled_value,
            compiled_path=(section.section_index, "@real_category"),
            compiled_reference_count=compiled_references[id(compiled_value)],
            message_key=message_key,
            message_value=message_value,
            message_path=(SPECIES_CATEGORY_MESSAGES_INDEX, "entry", message_index),
            source_usage_count=source_counts[section.category.source],
            runtime_key_reference_count=runtime_references[id(message_key)],
            runtime_value_reference_count=runtime_references[id(message_value)],
        )

    return _SpeciesCategoryAnalysis(
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


def _pbs_proof(
    analysis: _SpeciesCategoryAnalysis,
    target: SpeciesCategoryTarget,
) -> str:
    assignment = target.assignment
    proof = {
        "format": SPECIES_CATEGORY_PBS_PROOF_FORMAT,
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
        "explicit_form_category_count": analysis.explicit_form_category_count,
        "inherited_form_category_count": analysis.inherited_form_category_count,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "key": "Category",
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
    analysis: _SpeciesCategoryAnalysis,
    target: SpeciesCategoryTarget,
    compiled_raw: bytes,
) -> str:
    proof = {
        "format": COMPILED_SPECIES_CATEGORY_PROOF_FORMAT,
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
        "field": "@real_category",
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.compiled_value.ivars),
        "target_value_sha256": _sha256(target.compiled_value.data),
        "target_reference_count": target.compiled_reference_count,
        "compiled_path": list(target.compiled_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _SpeciesCategoryAnalysis,
    target: SpeciesCategoryTarget,
    messages_raw: bytes,
) -> str:
    proof = {
        "format": SPECIES_CATEGORY_RUNTIME_PROOF_FORMAT,
        "runtime_file": SPECIES_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "message_type_index": SPECIES_CATEGORY_MESSAGES_INDEX,
        "message_count": len(
            analysis.messages_root[SPECIES_CATEGORY_MESSAGES_INDEX]
        ),
        "root_graph_sha256": analysis.messages_graph_sha256,
        "target_key_sha256": _sha256(target.message_key.data),
        "target_value_sha256": _sha256(target.message_value.data),
        "target_value_equals_source": (
            target.message_value.text() == target.assignment.source
        ),
        "target_key_reference_count": target.runtime_key_reference_count,
        "target_value_reference_count": target.runtime_value_reference_count,
        "source_usage_count": target.source_usage_count,
        "runtime_path": list(target.message_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def build_species_category_proofs(
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
            compiled_path=json.dumps(
                list(target.compiled_path), separators=(",", ":")
            ),
            compiled_structure=_compiled_proof(analysis, target, compiled_raw),
            runtime_path=json.dumps(
                list(target.message_path), separators=(",", ":")
            ),
            runtime_structure=_runtime_proof(analysis, target, messages_raw),
        )
        for key, target in analysis.targets.items()
    }


def rebuild_species_category_payloads(
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
        raise SpeciesIntegrityError(
            "La traduction de catégorie Species ne tient pas sur une ligne PBS."
        )
    translated = translation.encode("utf-8")
    analysis = _analyze_sources(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    )
    target_key = (section, "Category", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise SpeciesIntegrityError(
            "La catégorie Species ne correspond plus à la source."
        )
    expected = build_species_category_proofs(
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
            "La preuve Category Species ne correspond plus aux quatre sources."
        )
    if target.source_usage_count != 1 or target.compiled_reference_count != 1:
        raise SpeciesIntegrityError(
            "Cette catégorie Species est partagée ou héritée et reste bloquée."
        )
    if (
        target.runtime_key_reference_count != 1
        or target.runtime_value_reference_count != 1
    ):
        raise SpeciesIntegrityError(
            "Cette catégorie Species partage un objet Marshal et reste bloquée."
        )
    if json.loads(runtime_structure).get("target_value_equals_source") is not True:
        raise SpeciesIntegrityError("La banque possède déjà une traduction différente.")
    if translation in {
        item.text()
        for item in analysis.messages_root[SPECIES_CATEGORY_MESSAGES_INDEX]
        if item is not target.message_key
    }:
        raise SpeciesIntegrityError(
            "La traduction entrerait en collision avec une autre catégorie Species."
        )

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
    compiled_object.ivars["@real_category"] = RubyString(
        translated, dict(target.compiled_value.ivars)
    )
    rebuilt_compiled_target = compiled_object.ivars["@real_category"]
    if (
        graph_sha256(
            analysis.compiled_root, masked=(rebuilt_compiled_target,)
        )
        != compiled_before
    ):
        raise SpeciesIntegrityError("La mutation modifierait species.dat hors cible.")
    rebuilt_compiled = dumps(analysis.compiled_root)

    runtime_before = graph_sha256(
        analysis.messages_root, masked=(target.message_key, target.message_value)
    )
    replacement_hash: dict = {}
    old_hash = analysis.messages_root[SPECIES_CATEGORY_MESSAGES_INDEX]
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_hash[RubyString(translated, dict(old_key.ivars))] = RubyString(
                translated, dict(old_value.ivars)
            )
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[SPECIES_CATEGORY_MESSAGES_INDEX] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[
        target.message_path[-1]
    ]
    if (
        graph_sha256(
            analysis.messages_root, masked=(rebuilt_key, rebuilt_value)
        )
        != runtime_before
    ):
        raise SpeciesIntegrityError(
            "La mutation modifierait la banque core hors cible."
        )
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
        raise SpeciesIntegrityError(
            "La relecture ne retrouve pas la catégorie Species exacte."
        )
    return {
        SPECIES_PBS_FILE: rebuilt_pbs,
        COMPILED_SPECIES_FILE: rebuilt_compiled,
        SPECIES_MESSAGES_FILE: rebuilt_messages,
    }


def extract_species_category_texts(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
) -> tuple[str, str, str]:
    target = _analyze_sources(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    ).targets.get((section, "Category", 1))
    if target is None:
        raise SpeciesIntegrityError("La catégorie Species est introuvable.")
    return (
        target.assignment.source,
        target.compiled_value.text(),
        target.message_value.text(),
    )
