# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preuve privée d'un ``FormName`` explicite Pokémon Essentials v21.1.

Le module ne lance jamais Ruby. Il relie une ligne de
``PBS/pokemon_forms.txt`` à ``@real_form_name`` dans ``Data/species.dat`` et à
la banque ``SPECIES_FORM_NAMES`` de ``Data/messages_core.dat``. Un ``FormName``
absent est compilé à ``nil`` et n'est pas hérité de l'espèce de base : il reste
donc impossible à reconstruire par cette porte.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import re

from essentials_phone import graph_sha256
from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_CLASS,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_IVARS,
    SPECIES_RAID_EXTENSION_IVARS,
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


SPECIES_FORM_NAME_MESSAGES_INDEX = 4
SPECIES_FORM_NAME_PBS_PROOF_FORMAT = "pft_v21_1_species_form_name_pbs_v1"
COMPILED_SPECIES_FORM_NAME_PROOF_FORMAT = (
    "pft_v21_1_compiled_species_form_name_v1"
)
SPECIES_FORM_NAME_RUNTIME_PROOF_FORMAT = (
    "pft_v21_1_species_form_name_runtime_v1"
)


@dataclass(frozen=True)
class SpeciesFormNameBaseSection:
    identifier: str
    section_index: int
    name: str
    form_name: SpeciesPbsAssignment | None


@dataclass(frozen=True)
class SpeciesFormNameFormSection:
    identifier: str
    section_index: int
    species: str
    form: int
    form_name: SpeciesPbsAssignment | None


@dataclass(frozen=True)
class SpeciesFormNamePbsDocument:
    relative_file: str
    content_lines: tuple[str, ...]
    base_sections: tuple[SpeciesFormNameBaseSection, ...]
    form_sections: tuple[SpeciesFormNameFormSection, ...]
    file_sha256: str


@dataclass(frozen=True)
class SpeciesFormNameTarget:
    assignment: SpeciesPbsAssignment
    species: str
    form: int
    compiled_index: int
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
class _SpeciesFormNameAnalysis:
    base_pbs: SpeciesFormNamePbsDocument
    forms_pbs: SpeciesFormNamePbsDocument
    compiled_root: dict
    messages_root: list
    targets: dict[tuple[str, str, int], SpeciesFormNameTarget]
    compiled_graph_sha256: str
    messages_graph_sha256: str
    explicit_base_form_name_count: int
    explicit_form_name_count: int
    missing_form_name_count: int


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


def parse_species_form_name_base_pbs(raw: bytes) -> SpeciesFormNamePbsDocument:
    lines = _decode_pbs(raw, SPECIES_PBS_FILE)
    sections: list[SpeciesFormNameBaseSection] = []
    identifier = ""
    section_index = -1
    name: str | None = None
    form_name: SpeciesPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal name, form_name
        if section_index < 0:
            return
        if name is None:
            raise SpeciesIntegrityError("Une espèce de base ne possède pas de Name unique.")
        sections.append(
            SpeciesFormNameBaseSection(identifier, section_index, name, form_name)
        )
        name = None
        form_name = None

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
        elif key == "FormName":
            if form_name is not None or not source or source != source.strip():
                raise SpeciesIntegrityError(
                    "FormName d'espèce de base dupliqué, vide ou ambigu."
                )
            form_name = _assignment(
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
    return SpeciesFormNamePbsDocument(
        SPECIES_PBS_FILE,
        lines,
        tuple(sections),
        (),
        _sha256(raw),
    )


def parse_species_form_name_forms_pbs(raw: bytes) -> SpeciesFormNamePbsDocument:
    lines = _decode_pbs(raw, SPECIES_FORMS_PBS_FILE)
    sections: list[SpeciesFormNameFormSection] = []
    identifier = ""
    species = ""
    form = 0
    section_index = -1
    form_name: SpeciesPbsAssignment | None = None

    def finish_section() -> None:
        nonlocal form_name
        if section_index < 0:
            return
        sections.append(
            SpeciesFormNameFormSection(
                identifier, section_index, species, form, form_name
            )
        )
        form_name = None

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
        if key == "FormName":
            if form_name is not None or not source or source != source.strip():
                raise SpeciesIntegrityError(
                    "FormName de forme dupliqué, vide ou ambigu."
                )
            form_name = _assignment(
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
    return SpeciesFormNamePbsDocument(
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
    expected_name: str,
    expected_form_name: str | None,
) -> RubyObject:
    if not isinstance(key, str) or key != expected_key:
        raise SpeciesIntegrityError("La clé compilée d'espèce a changé.")
    if not isinstance(value, RubyObject) or value.class_name != SPECIES_CLASS:
        raise SpeciesIntegrityError("Une entrée compilée n'est pas GameData::Species.")
    actual_ivars = tuple(value.ivars)
    if actual_ivars == SPECIES_IVARS:
        pass
    elif actual_ivars == SPECIES_IVARS + SPECIES_RAID_EXTENSION_IVARS:
        if not all(isinstance(value.ivars[field], list) for field in SPECIES_RAID_EXTENSION_IVARS):
            raise SpeciesIntegrityError("Les données de raid doivent rester des listes.")
    else:
        raise SpeciesIntegrityError("La structure compilée d'une espèce a changé.")
    compiled_form_name = value.ivars["@real_form_name"]
    if (
        value.ivars["@id"] != expected_key
        or value.ivars["@species"] != species
        or value.ivars["@form"] != form
        or _ruby_text(value.ivars["@real_name"], "Le nom compilé d'espèce").text()
        != expected_name
        or _ruby_text(
            value.ivars["@pbs_file_suffix"], "Le suffixe PBS d'espèce"
        ).text()
        != ""
    ):
        raise SpeciesIntegrityError(
            "L'identité ou le nom compilé de l'espèce a changé."
        )
    if expected_form_name is None:
        if compiled_form_name is not None:
            raise SpeciesIntegrityError(
                "Un FormName absent doit rester nil et ne peut pas être hérité."
            )
    elif _ruby_text(compiled_form_name, "Le FormName compilé").text() != expected_form_name:
        raise SpeciesIntegrityError("Le FormName compilé ne correspond plus au PBS.")
    return value


def _analyze_sources(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
) -> _SpeciesFormNameAnalysis:
    base_pbs = parse_species_form_name_base_pbs(base_pbs_raw)
    forms_pbs = parse_species_form_name_forms_pbs(forms_pbs_raw)
    base_by_id = {section.identifier: section for section in base_pbs.base_sections}
    for section in forms_pbs.form_sections:
        if section.species not in base_by_id:
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

    compiled_items = list(compiled_root.items())
    flattened: list[tuple[SpeciesPbsAssignment | None, RubyString]] = []
    for section, (key, raw_object) in zip(
        base_pbs.base_sections,
        compiled_items[: len(base_pbs.base_sections)],
    ):
        compiled_object = _validate_compiled_entry(
            key,
            raw_object,
            expected_key=section.identifier,
            species=section.identifier,
            form=0,
            expected_name=section.name,
            expected_form_name=(
                section.form_name.source if section.form_name is not None else None
            ),
        )
        value = compiled_object.ivars["@real_form_name"]
        if isinstance(value, RubyString):
            flattened.append((section.form_name, value))

    form_items = compiled_items[len(base_pbs.base_sections) :]
    for section, (key, raw_object) in zip(forms_pbs.form_sections, form_items):
        compiled_object = _validate_compiled_entry(
            key,
            raw_object,
            expected_key=f"{section.species}_{section.form}",
            species=section.species,
            form=section.form,
            expected_name=base_by_id[section.species].name,
            expected_form_name=(
                section.form_name.source if section.form_name is not None else None
            ),
        )
        value = compiled_object.ivars["@real_form_name"]
        if isinstance(value, RubyString):
            flattened.append((section.form_name, value))

    messages_root = _load_marshal(messages_raw, SPECIES_MESSAGES_FILE)
    if (
        not isinstance(messages_root, list)
        or len(messages_root) <= SPECIES_FORM_NAME_MESSAGES_INDEX
        or not isinstance(messages_root[SPECIES_FORM_NAME_MESSAGES_INDEX], dict)
    ):
        raise SpeciesIntegrityError(
            "La banque SPECIES_FORM_NAMES n'est pas à l'index v21.1 attendu."
        )
    source_counts: Counter[str] = Counter(value.text() for _a, value in flattened)
    ordered_unique = list(dict.fromkeys(value.text() for _a, value in flattened))
    message_items = list(messages_root[SPECIES_FORM_NAME_MESSAGES_INDEX].items())
    if len(message_items) != len(ordered_unique):
        raise SpeciesIntegrityError(
            "La banque SPECIES_FORM_NAMES ne couvre pas exactement species.dat."
        )
    runtime_by_source: dict[str, tuple[RubyString, RubyString, int]] = {}
    for message_index, (source, (key, value)) in enumerate(
        zip(ordered_unique, message_items)
    ):
        message_key = _ruby_text(key, "La clé SPECIES_FORM_NAMES")
        message_value = _ruby_text(value, "La valeur SPECIES_FORM_NAMES")
        if (
            message_key is message_value
            or message_key.text() != source
            or message_value.text() != source
        ):
            raise SpeciesIntegrityError(
                "L'ordre ou les objets de SPECIES_FORM_NAMES ont changé."
            )
        runtime_by_source[source] = (message_key, message_value, message_index)

    compiled_references = _reference_counts(compiled_root)
    runtime_references = _reference_counts(messages_root)
    targets: dict[tuple[str, str, int], SpeciesFormNameTarget] = {}
    base_count = len(base_pbs.base_sections)
    for section, (_key, compiled_object) in zip(forms_pbs.form_sections, form_items):
        if section.form_name is None:
            continue
        compiled_index = base_count + section.section_index
        compiled_value = compiled_object.ivars["@real_form_name"]
        message_key, message_value, message_index = runtime_by_source[
            section.form_name.source
        ]
        targets[(section.identifier, "FormName", 1)] = SpeciesFormNameTarget(
            assignment=section.form_name,
            species=section.species,
            form=section.form,
            compiled_index=compiled_index,
            compiled_value=compiled_value,
            compiled_path=(compiled_index, "@real_form_name"),
            compiled_reference_count=compiled_references[id(compiled_value)],
            message_key=message_key,
            message_value=message_value,
            message_path=(SPECIES_FORM_NAME_MESSAGES_INDEX, "entry", message_index),
            source_usage_count=source_counts[section.form_name.source],
            runtime_key_reference_count=runtime_references[id(message_key)],
            runtime_value_reference_count=runtime_references[id(message_value)],
        )

    return _SpeciesFormNameAnalysis(
        base_pbs,
        forms_pbs,
        compiled_root,
        messages_root,
        targets,
        graph_sha256(compiled_root),
        graph_sha256(messages_root),
        sum(section.form_name is not None for section in base_pbs.base_sections),
        sum(section.form_name is not None for section in forms_pbs.form_sections),
        sum(section.form_name is None for section in forms_pbs.form_sections),
    )


def _pbs_proof(
    analysis: _SpeciesFormNameAnalysis,
    target: SpeciesFormNameTarget,
) -> str:
    assignment = target.assignment
    proof = {
        "format": SPECIES_FORM_NAME_PBS_PROOF_FORMAT,
        "pbs_file": SPECIES_FORMS_PBS_FILE,
        "base_pbs_file": SPECIES_PBS_FILE,
        "file_sha256": analysis.forms_pbs.file_sha256,
        "base_file_sha256": analysis.base_pbs.file_sha256,
        "encoding": "utf-8-sig",
        "bom": "utf-8",
        "newline": "CRLF",
        "line_number": assignment.line_number,
        "section_index": assignment.section_index,
        "compiled_section_index": target.compiled_index,
        "base_section_count": len(analysis.base_pbs.base_sections),
        "form_section_count": len(analysis.forms_pbs.form_sections),
        "explicit_base_form_name_count": analysis.explicit_base_form_name_count,
        "explicit_form_name_count": analysis.explicit_form_name_count,
        "missing_form_name_count": analysis.missing_form_name_count,
        "species": target.species,
        "form": target.form,
        "target_is_explicit": True,
        "form_name_is_inherited": False,
        "section_sha256": _sha256(assignment.section.encode("utf-8")),
        "key": "FormName",
        "key_occurrence": 1,
        "prefix_sha256": _sha256(assignment.prefix.encode("utf-8")),
        "trailing_sha256": _sha256(assignment.trailing.encode("utf-8")),
        "line_sha256": _sha256(
            analysis.forms_pbs.content_lines[assignment.line_index].encode("utf-8")
        ),
        "source_sha256": _sha256(assignment.source.encode("utf-8")),
        "source_usage_count": target.source_usage_count,
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _compiled_proof(
    analysis: _SpeciesFormNameAnalysis,
    target: SpeciesFormNameTarget,
    compiled_raw: bytes,
) -> str:
    proof = {
        "format": COMPILED_SPECIES_FORM_NAME_PROOF_FORMAT,
        "compiled_file": COMPILED_SPECIES_FILE,
        "file_sha256": _sha256(compiled_raw),
        "root_type": "Hash",
        "root_size": len(analysis.compiled_root),
        "base_species_count": len(analysis.base_pbs.base_sections),
        "form_count": len(analysis.forms_pbs.form_sections),
        "root_graph_sha256": analysis.compiled_graph_sha256,
        "non_target_section_graph_sha256": graph_sha256(
            list(analysis.compiled_root.values())[target.compiled_index],
            masked=(target.compiled_value,),
        ),
        "section_index": target.compiled_index,
        "section_sha256": _sha256(target.assignment.section.encode("utf-8")),
        "section_class": SPECIES_CLASS,
        "section_ivars": list(SPECIES_IVARS),
        "species": target.species,
        "form": target.form,
        "field": "@real_form_name",
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.compiled_value.ivars),
        "target_value_sha256": _sha256(target.compiled_value.data),
        "target_reference_count": target.compiled_reference_count,
        "compiled_path": list(target.compiled_path),
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _runtime_proof(
    analysis: _SpeciesFormNameAnalysis,
    target: SpeciesFormNameTarget,
    messages_raw: bytes,
) -> str:
    proof = {
        "format": SPECIES_FORM_NAME_RUNTIME_PROOF_FORMAT,
        "runtime_file": SPECIES_MESSAGES_FILE,
        "file_sha256": _sha256(messages_raw),
        "root_type": "Array",
        "root_size": len(analysis.messages_root),
        "message_type_index": SPECIES_FORM_NAME_MESSAGES_INDEX,
        "message_count": len(
            analysis.messages_root[SPECIES_FORM_NAME_MESSAGES_INDEX]
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


def build_species_form_name_proofs(
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


def rebuild_species_form_name_payloads(
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
        raise SpeciesIntegrityError("La traduction FormName ne tient pas sur une ligne PBS.")
    translated = translation.encode("utf-8")
    analysis = _analyze_sources(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    )
    target_key = (section, "FormName", 1)
    target = analysis.targets.get(target_key)
    if target is None or target.assignment.source != source:
        raise SpeciesIntegrityError(
            "Le FormName explicite ne correspond plus à la source ; une valeur "
            "absente ou héritée ne peut pas être reconstruite."
        )
    expected = build_species_form_name_proofs(
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
            "La preuve FormName ne correspond plus exactement aux quatre sources."
        )
    if target.source_usage_count != 1 or target.compiled_reference_count != 1:
        raise SpeciesIntegrityError(
            "Ce FormName est partagé, ambigu ou non propre à cette forme."
        )
    if target.runtime_key_reference_count != 1 or target.runtime_value_reference_count != 1:
        raise SpeciesIntegrityError("Ce FormName partage un objet Marshal et reste bloqué.")
    pbs_proof = json.loads(pbs_structure)
    if (
        pbs_proof.get("target_is_explicit") is not True
        or pbs_proof.get("form_name_is_inherited") is not False
    ):
        raise SpeciesIntegrityError("La cible n'est plus un FormName explicite de forme.")
    if json.loads(runtime_structure).get("target_value_equals_source") is not True:
        raise SpeciesIntegrityError("La banque possède déjà une traduction différente.")
    if translation in {
        item.text()
        for item in analysis.messages_root[SPECIES_FORM_NAME_MESSAGES_INDEX]
        if item is not target.message_key
    }:
        raise SpeciesIntegrityError("La traduction entrerait en collision avec un autre nom.")

    assignment = target.assignment
    forms_lines = list(analysis.forms_pbs.content_lines)
    forms_lines[assignment.line_index] = (
        assignment.prefix + translation + assignment.trailing + assignment.newline
    )
    rebuilt_forms = b"\xef\xbb\xbf" + "".join(forms_lines).encode("utf-8")

    compiled_before = graph_sha256(
        analysis.compiled_root, masked=(target.compiled_value,)
    )
    compiled_object = list(analysis.compiled_root.values())[target.compiled_index]
    compiled_object.ivars["@real_form_name"] = RubyString(
        translated, dict(target.compiled_value.ivars)
    )
    rebuilt_compiled_target = compiled_object.ivars["@real_form_name"]
    if graph_sha256(
        analysis.compiled_root, masked=(rebuilt_compiled_target,)
    ) != compiled_before:
        raise SpeciesIntegrityError("La mutation modifierait species.dat hors cible.")
    rebuilt_compiled = dumps(analysis.compiled_root)

    runtime_before = graph_sha256(
        analysis.messages_root, masked=(target.message_key, target.message_value)
    )
    replacement_hash: dict = {}
    old_hash = analysis.messages_root[SPECIES_FORM_NAME_MESSAGES_INDEX]
    for old_key, old_value in old_hash.items():
        if old_key is target.message_key:
            replacement_hash[RubyString(translated, dict(old_key.ivars))] = RubyString(
                translated, dict(old_value.ivars)
            )
        else:
            replacement_hash[old_key] = old_value
    analysis.messages_root[SPECIES_FORM_NAME_MESSAGES_INDEX] = replacement_hash
    rebuilt_key, rebuilt_value = list(replacement_hash.items())[target.message_path[-1]]
    if graph_sha256(
        analysis.messages_root, masked=(rebuilt_key, rebuilt_value)
    ) != runtime_before:
        raise SpeciesIntegrityError("La mutation modifierait la banque core hors cible.")
    rebuilt_messages = dumps(analysis.messages_root)

    rebuilt = _analyze_sources(
        base_pbs_raw, rebuilt_forms, rebuilt_compiled, rebuilt_messages
    )
    rebuilt_target = rebuilt.targets.get(target_key)
    if (
        rebuilt_target is None
        or rebuilt_target.assignment.source != translation
        or rebuilt_target.species != target.species
        or rebuilt_target.form != target.form
        or rebuilt_target.compiled_value.text() != translation
        or rebuilt_target.message_key.text() != translation
        or rebuilt_target.message_value.text() != translation
        or rebuilt_target.compiled_path != target.compiled_path
        or rebuilt_target.message_path != target.message_path
    ):
        raise SpeciesIntegrityError("La relecture ne retrouve pas le FormName exact.")
    return {
        SPECIES_FORMS_PBS_FILE: rebuilt_forms,
        COMPILED_SPECIES_FILE: rebuilt_compiled,
        SPECIES_MESSAGES_FILE: rebuilt_messages,
    }


def extract_species_form_name_texts(
    base_pbs_raw: bytes,
    forms_pbs_raw: bytes,
    compiled_raw: bytes,
    messages_raw: bytes,
    *,
    section: str,
) -> tuple[str, str, str]:
    target = _analyze_sources(
        base_pbs_raw, forms_pbs_raw, compiled_raw, messages_raw
    ).targets.get((section, "FormName", 1))
    if target is None:
        raise SpeciesIntegrityError(
            "Le FormName explicite est introuvable ; les valeurs absentes ne sont pas héritées."
        )
    return (
        target.assignment.source,
        target.compiled_value.text(),
        target.message_value.text(),
    )
