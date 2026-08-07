# SPDX-License-Identifier: GPL-3.0-or-later
"""Corrections déterministes des commandes techniques protégées."""
from __future__ import annotations

import re
from collections import Counter


PROTECTED_RE = re.compile(
    r"(\\(?:[Pp][Nn]|[Ss][Hh]|[Ww][Uu]|[NnLlGgBbRr])"
    r"|\\[A-Za-z]+\[[^\]]*\]"
    r"|\\[.!|^><]"
    r"|\\[0-9]+"
    r"|<[^>]+>"
    r"|\{\d+\}"
    r"|%\d*\$?[sSdDiIfF])"
)


def extract_protected(text: str) -> list[str]:
    return PROTECTED_RE.findall(text or "")


def split_protected(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    position = 0
    for match in PROTECTED_RE.finditer(text or ""):
        if match.start() > position:
            parts.append(("text", text[position:match.start()]))
        parts.append(("code", match.group(0)))
        position = match.end()
    if position < len(text or ""):
        parts.append(("text", text[position:]))
    return parts


def protected_command_diff(
    source: str,
    translation: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    expected = extract_protected(source)
    found = extract_protected(translation)
    expected_counts = Counter(expected)
    found_counts = Counter(found)
    missing: list[str] = []
    extra: list[str] = []
    for command, count in expected_counts.items():
        missing.extend([command] * max(0, count - found_counts.get(command, 0)))
    for command, count in found_counts.items():
        extra.extend([command] * max(0, count - expected_counts.get(command, 0)))
    return expected, found, missing, extra


def restore_simple_commands(source: str, translation: str) -> tuple[str, list[str], bool]:
    """Répare les retours et commandes de bordure, sans deviner une position interne."""
    result = (translation or "").replace("\r\n", "\n").replace("\r", "\n")
    actions: list[str] = []
    expected, found, _missing, _extra = protected_command_diff(source, result)
    if expected == found:
        return result, ["Aucune commande manquante."], True

    expected_newlines = expected.count("\\n")
    found_newlines = found.count("\\n")
    needed_newlines = max(0, expected_newlines - found_newlines)
    if needed_newlines and "\n" in result:
        chunks = result.split("\n")
        replacements = min(needed_newlines, len(chunks) - 1)
        rebuilt = chunks[0]
        for index, chunk in enumerate(chunks[1:], start=1):
            rebuilt += ("\\n" if index <= replacements else "\n") + chunk
        result = rebuilt
        actions.append(f"{replacements} retour(s) à la ligne converti(s) en \\n.")

    _expected, _found, missing, _extra = protected_command_diff(source, result)
    source_parts = split_protected(source)
    leading: list[str] = []
    trailing: list[str] = []
    seen_text = False
    for kind, value in source_parts:
        if kind == "text" and value:
            seen_text = True
        elif kind == "code" and not seen_text:
            leading.append(value)
    seen_text = False
    for kind, value in reversed(source_parts):
        if kind == "text" and value:
            seen_text = True
        elif kind == "code" and not seen_text:
            trailing.insert(0, value)

    missing_counts = Counter(missing)
    for command in leading:
        if missing_counts.get(command, 0) > 0:
            result = command + result
            missing_counts[command] -= 1
            actions.append(f"Commande de début restaurée : {command}")
    for command in reversed(trailing):
        if missing_counts.get(command, 0) > 0:
            result = result + command
            missing_counts[command] -= 1
            actions.append(f"Commande de fin restaurée : {command}")

    expected, found, missing, extra = protected_command_diff(source, result)
    success = expected == found
    if not success:
        if missing:
            actions.append("Commandes internes encore manquantes : " + ", ".join(missing))
        if extra:
            actions.append("Commandes en trop : " + ", ".join(extra))
        actions.append("Vérification humaine requise : position non déterministe.")
    return result, actions, success
