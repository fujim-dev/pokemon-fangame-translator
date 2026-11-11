"""Diagnostic hypothétique et sans écriture des banques Ability v21.1.

Une preuve construite en mémoire n'autorise JAMAIS une reconstruction réelle.
"""
from __future__ import annotations

from pathlib import Path

from essentials_ability import (
    ABILITY_TEXT_FIELDS,
    ABILITY_PBS_FILE,
    COMPILED_ABILITY_FILE,
    ABILITY_MESSAGES_FILE,
    _load_marshal,
    _validate_compiled_entry,
    build_ability_text_proofs,
    parse_ability_pbs,
)
from ruby_marshal_reader import RubyString
from ruby_marshal_writer import dumps


def probe_ability_completion_in_memory(root: Path) -> dict[str, object] | None:
    """Teste une banque virtuelle complète, sans sauvegarder les octets obtenus."""
    paths = [root / ABILITY_PBS_FILE, root / COMPILED_ABILITY_FILE, root / ABILITY_MESSAGES_FILE]
    for path in (root, root / "PBS", root / "Data", *paths):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Chemin redirigé : diagnostic refusé.")
    if not all(p.is_file() for p in paths):
        return None
    pbs_raw, compiled_raw, runtime_raw = (p.read_bytes() for p in paths)
    pbs = parse_ability_pbs(pbs_raw)
    compiled = _load_marshal(compiled_raw, COMPILED_ABILITY_FILE)
    runtime = _load_marshal(runtime_raw, ABILITY_MESSAGES_FILE)
    if not isinstance(compiled, dict) or len(compiled) != len(pbs.sections):
        raise ValueError("Correspondance PBS/compilé impossible.")
    for section, (key, entry) in zip(pbs.sections, compiled.items()):
        _validate_compiled_entry(key, section, entry)
    if not isinstance(runtime, list):
        raise ValueError("Banques runtime invalides.")
    additions: dict[str, int] = {}
    for field, (_ivar, bank_index) in ABILITY_TEXT_FIELDS.items():
        if len(runtime) <= bank_index or not isinstance(runtime[bank_index], dict):
            raise ValueError("Banque runtime absente ou invalide.")
        bank = runtime[bank_index]
        sources = list(dict.fromkeys(
            (section.name if field == "Name" else section.description).source
            for section in pbs.sections
        ))
        items = list(bank.items())
        if len(items) > len(sources) or any(
            not isinstance(key, RubyString) or not isinstance(value, RubyString)
            or key.text() != source or value.text() != source
            for source, (key, value) in zip(sources, items)
        ):
            return {
                "hypothetical_only": True,
                "possible": False,
                "reason": "not_exact_prefix",
                "additions": {},
                "proof_count": 0,
            }
        additions[field] = len(sources) - len(items)
        for text in sources[len(items):]:
            bank[RubyString(text.encode("utf-8"), {"E": True})] = RubyString(
                text.encode("utf-8"), {"E": True}
            )
    proofs = build_ability_text_proofs(pbs_raw, compiled_raw, dumps(runtime))
    return {
        "hypothetical_only": True,
        "possible": True,
        "reason": "in_memory_proofs_only",
        "additions": additions,
        "proof_count": len(proofs),
    }
