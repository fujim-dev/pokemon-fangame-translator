# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrélation statique et mutation bornée des Point Essentials v21.1.

Ce module ne lance jamais Ruby. Il accepte uniquement la structure Marshal
observée et prouvée pour ``GameData::TownMap`` et refuse toute approximation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from ruby_marshal_reader import MarshalReader, RubyObject, RubyString
from ruby_marshal_writer import dumps


COMPILED_TOWN_MAP_FILE = "Data/town_map.dat"
COMPILED_POINT_PROOF_FORMAT = "pft_v21_1_compiled_point_v1"
TOWN_MAP_CLASS = "GameData::TownMap"
TOWN_MAP_IVARS = (
    "@id",
    "@real_name",
    "@filename",
    "@point",
    "@flags",
    "@pbs_file_suffix",
)


class TownMapIntegrityError(ValueError):
    """Le lien entre le PBS et la donnée compilée n'est pas démontrable."""


@dataclass(frozen=True)
class CompiledPointTarget:
    root: dict
    section: RubyObject
    point: list
    value: RubyString
    section_id: int
    point_index: int
    field_index: int


def load_town_map_bytes(raw: bytes) -> object:
    if not raw.startswith(b"\x04\x08"):
        raise TownMapIntegrityError("Data/town_map.dat n'est pas un Marshal Ruby 4.8.")
    try:
        reader = MarshalReader(raw)
        reader.pos = 2
        root = reader.read_object()
    except Exception as exc:
        raise TownMapIntegrityError("Data/town_map.dat est illisible sans exécuter Ruby.") from exc
    if reader.pos != len(raw):
        raise TownMapIntegrityError("Data/town_map.dat contient des octets finaux inattendus.")
    return root


def _canonical_integer(value: str, label: str) -> int:
    if not re.fullmatch(r"0|[1-9]\d*", value):
        raise TownMapIntegrityError(f"{label} Point n'est pas un entier non négatif canonique.")
    return int(value)


def _expected_point(fields: list[str]) -> list[object]:
    if len(fields) not in {3, 4, 7, 8}:
        raise TownMapIntegrityError("Nombre de sous-champs Point non reconnu.")
    if not fields[2]:
        raise TownMapIntegrityError("Le nom du Point est vide.")
    expected: list[object] = [
        _canonical_integer(fields[0], "Coordonnée X"),
        _canonical_integer(fields[1], "Coordonnée Y"),
        fields[2],
        (fields[3] or None) if len(fields) >= 4 else None,
    ]
    for index, value in enumerate(fields[4:], start=4):
        expected.append(
            None if value == "" else _canonical_integer(value, f"Sous-champ {index}")
        )
    expected.extend([None] * (8 - len(expected)))
    return expected


def _validate_ruby_text(value: object, label: str) -> RubyString:
    if not isinstance(value, RubyString) or value.ivars != {"E": True}:
        raise TownMapIntegrityError(
            f"{label} doit être une RubyString UTF-8 portant uniquement E=true."
        )
    return value


def _validate_root_shape(root: object) -> dict:
    if not isinstance(root, dict) or not root:
        raise TownMapIntegrityError("La racine compilée TownMap doit être un Hash non vide.")
    for key, section in root.items():
        if type(key) is not int or key < 0:
            raise TownMapIntegrityError("Une clé de section TownMap n'est pas un Integer valide.")
        if not isinstance(section, RubyObject) or section.class_name != TOWN_MAP_CLASS:
            raise TownMapIntegrityError("Une section compilée n'est pas GameData::TownMap.")
        if tuple(section.ivars) != TOWN_MAP_IVARS:
            raise TownMapIntegrityError("Les attributs d'une section TownMap ont changé.")
        if type(section.ivars["@id"]) is not int or section.ivars["@id"] != key:
            raise TownMapIntegrityError("L'identifiant compilé d'une section TownMap a changé.")
        _validate_ruby_text(section.ivars["@real_name"], "Le nom de région")
        _validate_ruby_text(section.ivars["@filename"], "Le fichier de région")
        _validate_ruby_text(section.ivars["@pbs_file_suffix"], "Le suffixe PBS")
        if not isinstance(section.ivars["@flags"], list):
            raise TownMapIntegrityError("Les flags TownMap ne sont plus un Array.")
        points = section.ivars["@point"]
        if not isinstance(points, list):
            raise TownMapIntegrityError("Les Point compilés ne sont plus un Array.")
        for point in points:
            if not isinstance(point, list) or len(point) != 8:
                raise TownMapIntegrityError("Un Point compilé n'a plus exactement huit positions.")
            if type(point[0]) is not int or type(point[1]) is not int:
                raise TownMapIntegrityError("Les coordonnées compilées d'un Point ont changé de type.")
            _validate_ruby_text(point[2], "Le nom compilé du Point")
            if point[3] is not None:
                _validate_ruby_text(point[3], "La description compilée du Point")
            if any(value is not None and type(value) is not int for value in point[4:]):
                raise TownMapIntegrityError("Un paramètre compilé non textuel a changé de type.")
    return root


def _plain_point_value(value: object) -> object:
    return value.text() if isinstance(value, RubyString) else value


def _reference_count(value: object, target: object) -> int:
    seen: set[int] = set()

    def visit(current: object) -> int:
        count = int(current is target)
        if isinstance(current, (RubyString, RubyObject, list, dict)):
            identity = id(current)
            if identity in seen:
                return count
            seen.add(identity)
        if isinstance(current, RubyString):
            return count + sum(visit(key) + visit(item) for key, item in current.ivars.items())
        if isinstance(current, RubyObject):
            return count + sum(visit(key) + visit(item) for key, item in current.ivars.items())
        if isinstance(current, list):
            return count + sum(visit(item) for item in current)
        if isinstance(current, dict):
            return count + sum(visit(key) + visit(item) for key, item in current.items())
        return count

    return visit(value)


def _graph_payload(value: object, *, masked_string: RubyString | None = None) -> bytes:
    identifiers: dict[int, int] = {}

    def encode(current: object) -> object:
        if isinstance(current, bool):
            return ["bool", current]
        if current is None:
            return ["nil"]
        if type(current) is int:
            return ["int", current]
        if isinstance(current, str):
            return ["symbol", current]
        if isinstance(current, (RubyString, RubyObject, list, dict)):
            identity = id(current)
            previous = identifiers.get(identity)
            if previous is not None:
                return ["ref", previous]
            object_id = len(identifiers)
            identifiers[identity] = object_id
            if isinstance(current, RubyString):
                digest = (
                    "TARGET"
                    if current is masked_string
                    else hashlib.sha256(current.data).hexdigest()
                )
                return [
                    "RubyString",
                    object_id,
                    digest,
                    [[encode(key), encode(item)] for key, item in current.ivars.items()],
                ]
            if isinstance(current, RubyObject):
                return [
                    "RubyObject",
                    object_id,
                    current.class_name,
                    [[encode(key), encode(item)] for key, item in current.ivars.items()],
                ]
            if isinstance(current, list):
                return ["Array", object_id, [encode(item) for item in current]]
            return [
                "Hash",
                object_id,
                [[encode(key), encode(item)] for key, item in current.items()],
            ]
        raise TownMapIntegrityError(
            f"Type Marshal TownMap non pris en charge : {type(current).__name__}."
        )

    return json.dumps(
        encode(value), ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def graph_sha256(value: object, *, masked_string: RubyString | None = None) -> str:
    return hashlib.sha256(_graph_payload(value, masked_string=masked_string)).hexdigest()


def validate_compiled_town_map_sections(
    raw: bytes,
    *,
    pbs_sections: dict[int, tuple[str, str, int]],
) -> None:
    """Prouve l'alignement global des sections PBS et compilées.

    Une correspondance locale par occurrence ne suffit pas : une section
    compilée ajoutée, manquante ou décalée rendrait les identifiants
    positionnels trompeurs. Le nom, le fichier graphique et le nombre total
    de Point doivent donc être identiques avant de lier la moindre ligne.
    """
    root = _validate_root_shape(load_town_map_bytes(raw))
    if set(root) != set(pbs_sections):
        raise TownMapIntegrityError(
            "Les sections PBS et compilées de TownMap ne correspondent pas."
        )
    for section_id, (name, filename, point_count) in pbs_sections.items():
        section = root[section_id]
        if not isinstance(section, RubyObject):  # déjà prouvé par _validate_root_shape
            raise TownMapIntegrityError("Une section compilée TownMap est invalide.")
        if section.ivars["@real_name"].text() != name:
            raise TownMapIntegrityError("Le nom d'une section TownMap ne correspond plus au PBS.")
        if section.ivars["@filename"].text() != filename:
            raise TownMapIntegrityError(
                "Le fichier graphique d'une section TownMap ne correspond plus au PBS."
            )
        if len(section.ivars["@point"]) != point_count:
            raise TownMapIntegrityError(
                "Le nombre de Point d'une section TownMap ne correspond plus au PBS."
            )


def locate_compiled_point(
    root: object,
    *,
    section: str,
    occurrence: int,
    field_index: int,
    pbs_fields: list[str],
) -> CompiledPointTarget:
    validated = _validate_root_shape(root)
    section_id = _canonical_integer(section, "Section")
    if occurrence <= 0:
        raise TownMapIntegrityError("Occurrence Point invalide.")
    if field_index not in {2, 3}:
        raise TownMapIntegrityError("Seuls les sous-champs textuels Point sont corrélables.")
    section_object = validated.get(section_id)
    if not isinstance(section_object, RubyObject):
        raise TownMapIntegrityError("La section PBS est absente du Hash compilé.")
    points = section_object.ivars["@point"]
    point_index = occurrence - 1
    if not (0 <= point_index < len(points)):
        raise TownMapIntegrityError("L'occurrence Point est absente de la section compilée.")
    point = points[point_index]
    expected = _expected_point(pbs_fields)
    actual = [_plain_point_value(value) for value in point]
    if actual != expected:
        raise TownMapIntegrityError(
            "Les sous-champs PBS ne correspondent pas exactement au Point compilé."
        )
    target = _validate_ruby_text(point[field_index], "Le sous-champ Point ciblé")
    if _reference_count(validated, target) != 1:
        raise TownMapIntegrityError(
            "La chaîne compilée ciblée est partagée par plusieurs emplacements."
        )
    return CompiledPointTarget(
        root=validated,
        section=section_object,
        point=point,
        value=target,
        section_id=section_id,
        point_index=point_index,
        field_index=field_index,
    )


def build_compiled_point_proof(
    raw: bytes,
    *,
    section: str,
    occurrence: int,
    field_index: int,
    pbs_fields: list[str],
) -> str:
    root = load_town_map_bytes(raw)
    if dumps(root) != raw:
        raise TownMapIntegrityError(
            "Le lecteur/écrivain Marshal ne reproduit pas exactement Data/town_map.dat."
        )
    target = locate_compiled_point(
        root,
        section=section,
        occurrence=occurrence,
        field_index=field_index,
        pbs_fields=pbs_fields,
    )
    proof = {
        "format": COMPILED_POINT_PROOF_FORMAT,
        "compiled_file": COMPILED_TOWN_MAP_FILE,
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "root_type": "Hash",
        "root_size": len(target.root),
        "root_graph_sha256": graph_sha256(target.root),
        "non_target_graph_sha256": graph_sha256(
            target.root, masked_string=target.value
        ),
        "section": target.section_id,
        "section_key_type": "Integer",
        "section_class": target.section.class_name,
        "section_ivars": list(target.section.ivars),
        "section_point_count": len(target.section.ivars["@point"]),
        "occurrence": occurrence,
        "point_index": target.point_index,
        "point_length": len(target.point),
        "point_types": [type(value).__name__ for value in target.point],
        "field_index": field_index,
        "target_type": "RubyString",
        "target_ivars_sha256": graph_sha256(target.value.ivars),
        "target_value_sha256": hashlib.sha256(target.value.data).hexdigest(),
        "target_reference_count": 1,
        "compiled_path": [target.section_id, "@point", target.point_index, field_index],
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def rebuild_compiled_point(
    raw: bytes,
    *,
    proof_json: str,
    section: str,
    occurrence: int,
    field_index: int,
    pbs_fields: list[str],
    source: str,
    translation: str,
) -> bytes:
    expected_proof = build_compiled_point_proof(
        raw,
        section=section,
        occurrence=occurrence,
        field_index=field_index,
        pbs_fields=pbs_fields,
    )
    if expected_proof != proof_json:
        raise TownMapIntegrityError("La preuve compilée Point ne correspond plus à la source.")
    root = load_town_map_bytes(raw)
    target = locate_compiled_point(
        root,
        section=section,
        occurrence=occurrence,
        field_index=field_index,
        pbs_fields=pbs_fields,
    )
    if target.value.text() != source:
        raise TownMapIntegrityError("La valeur compilée originale du Point a changé.")
    try:
        translated = translation.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TownMapIntegrityError("La traduction Point n'est pas encodable en UTF-8.") from exc
    before_without_target = graph_sha256(root, masked_string=target.value)
    replacement = RubyString(translated, dict(target.value.ivars))
    target.point[field_index] = replacement
    after_without_target = graph_sha256(root, masked_string=replacement)
    if before_without_target != after_without_target:
        raise TownMapIntegrityError("La mutation modifierait la structure compilée hors cible.")
    payload = dumps(root)
    rebuilt_root = load_town_map_bytes(payload)
    translated_fields = list(pbs_fields)
    translated_fields[field_index] = translation
    rebuilt_target = locate_compiled_point(
        rebuilt_root,
        section=section,
        occurrence=occurrence,
        field_index=field_index,
        pbs_fields=translated_fields,
    )
    if rebuilt_target.value.text() != translation:
        raise TownMapIntegrityError("La relecture compilée ne retrouve pas la traduction Point.")
    if graph_sha256(rebuilt_root, masked_string=rebuilt_target.value) != before_without_target:
        raise TownMapIntegrityError("La structure compilée relue diffère hors sous-champ ciblé.")
    return payload


def extract_compiled_point_text(
    raw: bytes,
    *,
    section: str,
    occurrence: int,
    field_index: int,
    pbs_fields: list[str],
) -> str:
    target = locate_compiled_point(
        load_town_map_bytes(raw),
        section=section,
        occurrence=occurrence,
        field_index=field_index,
        pbs_fields=pbs_fields,
    )
    return target.value.text()
