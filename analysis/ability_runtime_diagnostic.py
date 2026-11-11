"""Diagnostic statique des banques Ability Essentials v21.1.

Ce contrôle est informatif et ne fournit jamais de preuve autorisant la
reconstruction. Aucun texte de jeu ni identifiant n'est inclus au rapport.
"""
from __future__ import annotations

from pathlib import Path

from essentials_ability import (
    ABILITY_MESSAGES_FILE,
    ABILITY_NAME_MESSAGES_INDEX,
    ABILITY_DESCRIPTION_MESSAGES_INDEX,
    ABILITY_PBS_FILE,
    COMPILED_ABILITY_FILE,
    _load_marshal,
    parse_ability_pbs,
)
from ruby_marshal_reader import RubyString


def inspect_ability_runtime_banks(root: Path) -> dict[str, object] | None:
    """Compare les banques runtime et le PBS sur des sources statiques.

    None signifie que les trois fichiers ne sont pas tous disponibles.
    Un rapport « incomplete » interdit toute conclusion de compatibilité.
    """
    paths = (
        root / ABILITY_PBS_FILE,
        root / COMPILED_ABILITY_FILE,
        root / ABILITY_MESSAGES_FILE,
    )
    for path in (root, root / "PBS", root / "Data", *paths):
        if path.is_symlink() or (
            getattr(path, "is_junction", None) is not None and path.is_junction()
        ):
            raise ValueError("Lien ou jonction refusé pour le diagnostic Ability.")
    if not all(path.is_file() for path in paths):
        return None
    pbs = parse_ability_pbs(paths[0].read_bytes())
    compiled = _load_marshal(paths[1].read_bytes(), COMPILED_ABILITY_FILE)
    runtime = _load_marshal(paths[2].read_bytes(), ABILITY_MESSAGES_FILE)
    if not isinstance(compiled, dict) or len(compiled) != len(pbs.sections):
        raise ValueError("Nombre de capacités PBS et compilées incohérent.")
    if not isinstance(runtime, list):
        raise ValueError("La racine des banques de messages n'est pas un tableau.")
    fields: dict[str, dict[str, object]] = {}
    for label, index in (
        ("Name", ABILITY_NAME_MESSAGES_INDEX),
        ("Description", ABILITY_DESCRIPTION_MESSAGES_INDEX),
    ):
        if index >= len(runtime) or not isinstance(runtime[index], dict):
            raise ValueError("Banque Ability absente ou de type inattendu.")
        sources = list(dict.fromkeys(
            (section.name if label == "Name" else section.description).source
            for section in pbs.sections
        ))
        pairs = list(runtime[index].items())
        if any(
            not isinstance(key, RubyString)
            or not isinstance(value, RubyString)
            for key, value in pairs
        ):
            raise ValueError("Banque Ability avec chaînes non reconnues.")
        keys = [key.text() for key, _value in pairs]
        values = [value.text() for _key, value in pairs]
        exact_prefix = keys == sources[:len(keys)] and keys == values
        fields[label] = {
            "expected_unique": len(sources),
            "runtime_entries": len(keys),
            "missing_unique": max(0, len(sources) - len(keys)),
            "exact_prefix": exact_prefix,
            "complete": len(keys) == len(sources) and exact_prefix,
        }
    return {
        "sections": len(pbs.sections),
        "fields": fields,
        "complete": all(field["complete"] for field in fields.values()),
    }
