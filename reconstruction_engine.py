# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulation et reconstruction sécurisée d'une copie de fangame RPG Maker XP.

Périmètre v1.0.2 :
- dialogues et choix des MapXXX.rxdata ;
- valeurs des banques messages_game.dat/messages_core.dat ;
- champs textuels PBS explicitement extraits.

Scripts.rxdata, PluginScripts.rxdata et tous les fichiers non reconnus sont
volontairement exclus de l'écriture.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable

from ruby_marshal_reader import RubyObject, RubyString, load
from ruby_marshal_writer import dumps
from structured_extractor import (
    TRANSLATABLE_PBS_KEYS,
    extract_map,
    extract_message_bank,
    extract_pbs,
    looks_visible,
    stable_id,
    text_value,
)

RPG_CODE_RE = re.compile(
    r"(\\(?:[Pp][Nn]|[Ss][Hh]|[Ww][Uu]|[NnLlGgBbRr])"
    r"|\\[A-Za-z]+\[[^\]]*\]"
    r"|\\[.!|^><]"
    r"|\\[0-9]+"
    r"|<[^>]+>"
    r"|\{\d+\}"
    r"|%\d*\$?[sSdDiIfF])"
)

SUPPORTED_TYPES = {"Dialogue", "Choix", "Banque de messages"}
SAFE_STATUSES = {"Accepté", "Prêt", "Traduit", "Déjà traduit"}
REVIEW_STATUSES = {"À vérifier", "À relire"}
BLOCKED_STATUSES = {"Bloqué", "À traduire", "Ignoré", ""}


@dataclass
class PlanItem:
    id_stable: str
    type: str
    fichier: str
    source: str
    translation: str
    status: str
    map_id: str = ""
    map_name: str = ""
    event_id: str = ""
    event_name: str = ""
    page: str = ""
    command: str = ""
    sub_index: str = ""
    decision: str = "pending"  # applicable, skipped, blocked
    reason: str = ""


@dataclass
class ReconstructionPlan:
    game_root: str
    csv_path: str
    created_at: str
    mode: str
    project_rows: int = 0
    translated_rows: int = 0
    untranslated_rows: int = 0
    items: list[PlanItem] = field(default_factory=list)
    source_hashes: dict[str, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        result = Counter(item.decision for item in self.items)
        result["total"] = len(self.items)
        result["project_rows"] = self.project_rows
        result["translated_rows"] = self.translated_rows
        result["untranslated_rows"] = self.untranslated_rows
        result["files"] = len({item.fichier for item in self.items if item.decision == "applicable"})
        return dict(result)


@dataclass
class ReconstructionResult:
    target_root: str
    applied: int
    skipped: int
    blocked: int
    modified_files: list[str]
    validation_errors: list[str]
    original_unchanged: bool
    report_path: str
    manifest_path: str


class ReconstructionError(RuntimeError):
    pass


def extract_protected(text: str) -> list[str]:
    return RPG_CODE_RE.findall(text or "")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_parts(relative: str) -> tuple[str, ...]:
    """Valide un chemin de projet avant toute lecture ou écriture.

    Les CSV sont modifiables par l'utilisateur. Un chemin qu'ils contiennent ne
    doit donc jamais pouvoir devenir absolu ni remonter avec ``..``. Le contrôle
    Windows est explicite afin de rester sûr même lorsque les tests sont lancés
    sur un autre système.
    """
    raw = str(relative or "")
    normalized = raw.replace("\\", "/")
    windows_path = PureWindowsPath(raw)
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
    ):
        raise ReconstructionError("Chemin de fichier non sécurisé : chemin absolu ou vide")

    parts = tuple(normalized.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ReconstructionError("Chemin de fichier non sécurisé : segment interdit")

    invalid_windows = set('<>:"|?*')
    reserved_windows = {
        "con", "prn", "aux", "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    for part in parts:
        if (
            any(ord(character) < 32 or character in invalid_windows for character in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in reserved_windows
        ):
            raise ReconstructionError("Chemin de fichier non sécurisé : nom Windows interdit")
    return parts


def _is_link_or_junction(path: Path) -> bool:
    """Détecte les redirections de système de fichiers sans les suivre."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return False


def _assert_no_link_components(root: Path, parts: tuple[str, ...]) -> None:
    current = root
    for part in parts:
        current = current / part
        if _is_link_or_junction(current):
            raise ReconstructionError(
                "Chemin de fichier non sécurisé : lien symbolique ou jonction refusé"
            )


def _assert_tree_has_no_links(root: Path) -> None:
    """Parcourt une arborescence sans suivre de lien et refuse toute jonction."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ReconstructionError(f"Impossible d'inspecter la copie source : {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            if _is_link_or_junction(path):
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    relative = Path(path.name)
                raise ReconstructionError(
                    f"Lien symbolique ou jonction refusé dans le fangame : {relative}"
                )
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
            except OSError as exc:
                raise ReconstructionError(f"Impossible d'inspecter {path.name} : {exc}") from exc


def _resolve_contained_path(root: Path, relative: str) -> Path:
    """Retourne un chemin résolu uniquement s'il reste dans ``root``."""
    resolved_root = root.expanduser().resolve()
    parts = _safe_relative_parts(relative)
    _assert_no_link_components(resolved_root, parts)
    candidate = resolved_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ReconstructionError("Chemin de fichier non sécurisé : sortie du dossier autorisé") from exc
    return candidate


def _path_matches_item_type(relative: str, row_type: str) -> bool:
    """Limite chaque type de ligne aux emplacements produits par l'extracteur."""
    parts = _safe_relative_parts(relative)
    lowered = tuple(part.casefold() for part in parts)
    if row_type in {"Dialogue", "Choix"}:
        return (
            len(lowered) == 2
            and lowered[0] == "data"
            and re.fullmatch(r"map\d{3,4}\.rxdata", lowered[1]) is not None
        )
    if row_type == "Banque de messages":
        return lowered in {
            ("data", "messages_game.dat"),
            ("data", "messages_core.dat"),
        }
    if row_type.startswith("PBS —"):
        return len(lowered) >= 2 and lowered[0] == "pbs" and lowered[-1].endswith(".txt")
    return False


def _resolve_group_path(root: Path, relative: str, items: list[PlanItem]) -> Path:
    """Revérifie un groupe, y compris lorsqu'un plan a été modifié en mémoire."""
    path = _resolve_contained_path(root, relative)
    path_key = os.path.normcase(str(path))
    for item in items:
        item_path = _resolve_contained_path(root, item.fichier)
        if os.path.normcase(str(item_path)) != path_key:
            raise ReconstructionError("Le plan mélange plusieurs chemins de fichiers")
        if not _path_matches_item_type(item.fichier, item.type):
            raise ReconstructionError("Chemin incompatible avec le type de texte")
    return path


def _is_same_or_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _assert_plan_sources_unchanged(plan: ReconstructionPlan, source_root: Path) -> None:
    """Refuse un plan incomplet ou devenu obsolète avant son application."""
    expected_files = {
        item.fichier
        for item in plan.items
        if item.decision == "applicable"
    }
    if not expected_files.issubset(plan.source_hashes):
        raise ReconstructionError(
            "Le plan de reconstruction est incomplet. Relancez la simulation."
        )

    for relative in sorted(expected_files):
        expected_hash = plan.source_hashes[relative]
        source_path = _resolve_contained_path(source_root, relative)
        if not source_path.is_file() or sha256_file(source_path) != expected_hash:
            raise ReconstructionError(
                f"Le fichier source a changé depuis la simulation : {relative}. "
                "Relancez la simulation."
            )


def _integer(value: str, field_name: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ReconstructionError(f"{field_name} invalide : {value!r}")


def _supported_row_type(row_type: str) -> bool:
    return row_type in SUPPORTED_TYPES or row_type.startswith("PBS —")


def _row_is_eligible(row: dict[str, str], mode: str) -> tuple[bool, str]:
    translation = (row.get("traduction_fr") or "").strip()
    status = (row.get("statut") or "").strip()
    if not translation:
        return False, "Traduction vide"
    if status in {"Bloqué", "Ignoré", "À traduire"}:
        return False, f"Statut exclu : {status}"
    if mode == "accepted" and status != "Accepté":
        return False, "Seuls les textes acceptés sont inclus"
    if mode == "recommended" and status not in SAFE_STATUSES:
        return False, f"Texte encore à relire : {status or 'sans statut'}"
    if mode == "all_reviewed" and status in BLOCKED_STATUSES:
        return False, f"Statut exclu : {status or 'sans statut'}"
    return True, ""


def load_project_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        required = {"id_stable", "type", "fichier", "texte_source", "traduction_fr", "statut"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ReconstructionError("CSV incompatible, colonnes manquantes : " + ", ".join(missing))
        return list(reader)


def build_plan(game_root: Path, csv_path: Path, mode: str = "recommended") -> ReconstructionPlan:
    game_root = game_root.expanduser().resolve()
    csv_path = csv_path.expanduser().resolve()
    if not game_root.is_dir():
        raise ReconstructionError("Le dossier du fangame est introuvable.")
    if not csv_path.is_file():
        raise ReconstructionError("Le projet de traduction est introuvable.")
    if mode not in {"accepted", "recommended", "all_reviewed"}:
        raise ReconstructionError(f"Mode de reconstruction inconnu : {mode}")

    plan = ReconstructionPlan(
        game_root=str(game_root),
        csv_path=str(csv_path),
        created_at=datetime.now().isoformat(timespec="seconds"),
        mode=mode,
    )

    rows = load_project_rows(csv_path)
    plan.project_rows = len(rows)
    for row in rows:
        translation = (row.get("traduction_fr") or "").strip()
        if not translation:
            plan.untranslated_rows += 1
            continue
        plan.translated_rows += 1
        item = PlanItem(
            id_stable=row.get("id_stable", ""),
            type=row.get("type", ""),
            fichier=(row.get("fichier") or "").replace("\\", "/"),
            source=row.get("texte_source", ""),
            translation=translation,
            status=row.get("statut", ""),
            map_id=row.get("carte_id", ""),
            map_name=row.get("carte_nom", ""),
            event_id=row.get("evenement_id", ""),
            event_name=row.get("evenement_nom", ""),
            page=row.get("page", ""),
            command=row.get("commande", ""),
            sub_index=row.get("sous_index", ""),
        )

        if not _supported_row_type(item.type):
            item.decision, item.reason = "skipped", "Type non pris en charge"
        else:
            eligible, reason = _row_is_eligible(row, mode)
            if not eligible:
                item.decision, item.reason = "skipped", reason
            elif extract_protected(item.source) != extract_protected(item.translation):
                item.decision, item.reason = "blocked", "Commandes du jeu différentes"
            else:
                try:
                    source_file = _resolve_contained_path(game_root, item.fichier)
                    if not _path_matches_item_type(item.fichier, item.type):
                        raise ReconstructionError("Chemin incompatible avec le type de texte")
                    if not source_file.is_file():
                        raise ReconstructionError("Fichier source absent")
                    if source_file.name.casefold() in {"scripts.rxdata", "pluginscripts.rxdata"}:
                        raise ReconstructionError("Scripts exclus")
                except ReconstructionError as exc:
                    item.decision, item.reason = "blocked", str(exc)
                else:
                    item.decision = "applicable"
        plan.items.append(item)

    for relative in sorted({item.fichier for item in plan.items if item.decision == "applicable"}):
        plan.source_hashes[relative] = sha256_file(_resolve_contained_path(game_root, relative))
    return plan


def _ruby_string_set(value, text: str) -> RubyString:
    """Remplace une chaîne par des octets UTF-8 valides.

    Les cartes Pokémon Essentials peuvent contenir des chaînes UTF-8 sans
    indicateur Marshal ``E``. La v0.9 les réencodait parfois en CP1252, ce qui
    produisait ensuite ``invalid byte sequence in UTF-8`` dans Intl_Messages.
    On conserve donc la forme des métadonnées quand elle est déjà compatible,
    mais les nouveaux octets sont toujours de l'UTF-8 réel.
    """
    payload = text.encode("utf-8")
    # Assertion interne : une traduction reconstruite ne doit jamais contenir
    # une séquence d'octets invalide en UTF-8.
    payload.decode("utf-8")

    if isinstance(value, RubyString):
        value.data = payload

        # Un encodage explicite non UTF-8 ne doit pas rester attaché à une
        # traduction désormais stockée en UTF-8.
        if "encoding" in value.ivars:
            value.ivars.pop("encoding", None)
            value.ivars["E"] = True
        elif value.ivars.get("E") is False:
            value.ivars["E"] = True

        # Si la chaîne n'avait aucun indicateur, on le laisse absent : c'est
        # la forme utilisée par de nombreuses cartes Essentials qui stockent
        # pourtant déjà leurs textes en UTF-8.
        return value

    return RubyString(payload, {"E": True})


def _locate_map_message(root: RubyObject, item: PlanItem):
    event_id = _integer(item.event_id, "Événement")
    page_number = _integer(item.page, "Page")
    command_index = _integer(item.command, "Commande")
    events = root.ivars.get("@events", {})
    event = events.get(event_id) if isinstance(events, dict) else None
    if not isinstance(event, RubyObject):
        raise ReconstructionError("Événement introuvable")
    pages = event.ivars.get("@pages", [])
    if not isinstance(pages, list) or not (1 <= page_number <= len(pages)):
        raise ReconstructionError("Page introuvable")
    page = pages[page_number - 1]
    commands = page.ivars.get("@list", []) if isinstance(page, RubyObject) else []
    if not isinstance(commands, list) or not (0 <= command_index < len(commands)):
        raise ReconstructionError("Commande introuvable")
    return commands, command_index


def _apply_map_item(root: RubyObject, item: PlanItem) -> None:
    commands, index = _locate_map_message(root, item)
    command = commands[index]
    if not isinstance(command, RubyObject):
        raise ReconstructionError("Commande RPG invalide")
    code = command.ivars.get("@code")
    params = command.ivars.get("@parameters", [])

    if item.type == "Dialogue":
        if code != 101 or not isinstance(params, list) or not params:
            raise ReconstructionError("Dialogue 101 introuvable")
        actual_commands = [command]
        cursor = index + 1
        while cursor < len(commands):
            next_command = commands[cursor]
            if not isinstance(next_command, RubyObject) or next_command.ivars.get("@code") != 401:
                break
            actual_commands.append(next_command)
            cursor += 1
        current_pieces = []
        for event_command in actual_commands:
            event_params = event_command.ivars.get("@parameters", [])
            current_pieces.append(text_value(event_params[0]) if isinstance(event_params, list) and event_params else "")
        if "\\n".join(current_pieces).strip() != item.source.strip():
            raise ReconstructionError("Le dialogue original ne correspond plus au projet")
        translated_pieces = item.translation.split("\\n")
        if len(translated_pieces) != len(actual_commands):
            raise ReconstructionError(
                f"Retours de ligne incompatibles : {len(actual_commands)} attendu(s), {len(translated_pieces)} trouvé(s)"
            )
        for event_command, translated_piece in zip(actual_commands, translated_pieces):
            event_params = event_command.ivars.get("@parameters", [])
            if not isinstance(event_params, list) or not event_params:
                raise ReconstructionError("Paramètre de dialogue invalide")
            event_params[0] = _ruby_string_set(event_params[0], translated_piece)
            _assert_utf8_translation_bytes(
                event_params[0],
                f"{item.fichier} — dialogue {item.id_stable}",
            )
        return

    if item.type == "Choix":
        if code != 102 or not isinstance(params, list) or not params or not isinstance(params[0], list):
            raise ReconstructionError("Liste de choix introuvable")
        choice_index = _integer(item.sub_index, "Index de choix")
        if not (0 <= choice_index < len(params[0])):
            raise ReconstructionError("Choix introuvable")
        current = text_value(params[0][choice_index]).strip()
        if current != item.source.strip():
            raise ReconstructionError("Le choix original ne correspond plus au projet")
        params[0][choice_index] = _ruby_string_set(params[0][choice_index], item.translation)
        _assert_utf8_translation_bytes(
            params[0][choice_index],
            f"{item.fichier} — choix {item.id_stable}",
        )
        return

    raise ReconstructionError(f"Type de carte non pris en charge : {item.type}")


def _walk_message_bank_refs(value, path=()):
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_message_bank_refs(child, path + (index,))
    elif isinstance(value, dict):
        entry_index = 0
        for key, child in list(value.items()):
            if key == "__default__":
                continue
            key_text = text_value(key).strip()
            value_text = text_value(child).strip()
            if looks_visible(key_text):
                yield path + ("entry", entry_index), value, key, child, key_text, value_text
                entry_index += 1
            elif isinstance(child, (list, dict)):
                yield from _walk_message_bank_refs(child, path + ("value", entry_index))
                entry_index += 1


def _apply_bank_items(root, relative: str, items: list[PlanItem]) -> None:
    by_id = {item.id_stable: item for item in items}
    found: set[str] = set()
    for location, parent, key, current_value, source, _current in _walk_message_bank_refs(root):
        location_text = "/".join(map(str, location))
        row_id = stable_id("bank", relative, location_text, source)
        item = by_id.get(row_id)
        if not item:
            continue
        if source != item.source.strip():
            raise ReconstructionError(f"Banque modifiée depuis l'extraction : {row_id}")
        parent[key] = _ruby_string_set(current_value, item.translation)
        _assert_utf8_translation_bytes(
            parent[key],
            f"{relative} — banque {row_id}",
        )
        found.add(row_id)
    missing = sorted(set(by_id) - found)
    if missing:
        raise ReconstructionError(f"{len(missing)} entrée(s) de banque introuvable(s)")


def _detect_text_encoding(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252"


def _apply_pbs_items(path: Path, relative: str, items: list[PlanItem]) -> None:
    content, encoding = _detect_text_encoding(path)
    lines = content.splitlines(keepends=True)
    by_id = {item.id_stable: item for item in items}
    found: set[str] = set()
    section = "GLOBAL"
    occurrence: Counter[tuple[str, str]] = Counter()

    for index, raw_line in enumerate(lines):
        newline = "\r\n" if raw_line.endswith("\r\n") else ("\n" if raw_line.endswith("\n") else "")
        body = raw_line[:-len(newline)] if newline else raw_line
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        section_match = re.match(r"^\[([^\]]+)\]", stripped)
        if section_match:
            section = section_match.group(1).strip()
            continue
        match = re.match(r"^(\s*([^=]+?)\s*=\s*)(.*?)(\s*)$", body)
        if not match:
            continue
        prefix, raw_key, value, trailing = match.groups()
        key = raw_key.strip()
        if key not in TRANSLATABLE_PBS_KEYS or not looks_visible(value):
            continue
        occurrence[(section, key)] += 1
        sub_index = occurrence[(section, key)]
        row_id = stable_id("pbs", relative, section, key, sub_index)
        item = by_id.get(row_id)
        if not item:
            continue
        if value.strip() != item.source.strip():
            raise ReconstructionError(f"Champ PBS modifié depuis l'extraction : {row_id}")
        lines[index] = f"{prefix}{item.translation}{trailing}{newline}"
        found.add(row_id)

    missing = sorted(set(by_id) - found)
    if missing:
        raise ReconstructionError(f"{len(missing)} champ(s) PBS introuvable(s)")

    rebuilt = "".join(lines)
    temp = path.with_suffix(path.suffix + ".pfttmp")
    try:
        temp.write_text(rebuilt, encoding=encoding, newline="")
    except UnicodeEncodeError as exc:
        raise ReconstructionError(f"Caractère incompatible avec l'encodage {encoding}: {exc}")
    temp.replace(path)


def _assert_utf8_translation_bytes(value, context: str) -> None:
    """Vérifie les octets d'une chaîne que le moteur vient de remplacer."""
    if not isinstance(value, RubyString):
        raise ReconstructionError(f"{context} : chaîne Ruby attendue")
    try:
        value.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReconstructionError(
            f"{context} : traduction non valide en UTF-8 ({exc})"
        ) from exc


def _atomic_write_marshal(path: Path, root) -> None:
    payload = dumps(root)
    temp = path.with_suffix(path.suffix + ".pfttmp")
    temp.write_bytes(payload)
    # Relire avant le remplacement pour détecter immédiatement une écriture invalide.
    load(temp)
    temp.replace(path)


def _apply_file(target_root: Path, relative: str, items: list[PlanItem]) -> None:
    path = _resolve_group_path(target_root, relative, items)
    if relative.lower().endswith(".rxdata"):
        root = load(path)
        if not isinstance(root, RubyObject) or root.class_name != "RPG::Map":
            raise ReconstructionError("Seules les cartes RPG::Map sont modifiées en v1.0.2")
        for item in items:
            _apply_map_item(root, item)
        _atomic_write_marshal(path, root)
        return
    if relative.lower().endswith(".dat"):
        root = load(path)
        _apply_bank_items(root, relative, items)
        _atomic_write_marshal(path, root)
        return
    if relative.lower().startswith("pbs/") and relative.lower().endswith(".txt"):
        _apply_pbs_items(path, relative, items)
        return
    raise ReconstructionError("Format de fichier non pris en charge")


def _validate_file(target_root: Path, relative: str, items: list[PlanItem]) -> list[str]:
    path = _resolve_group_path(target_root, relative, items)
    expected = {item.id_stable: item.translation for item in items}
    if relative.lower().endswith(".rxdata"):
        map_name = items[0].map_name if items else ""
        extracted = extract_map(path, relative, map_name)
        actual = {row["id_stable"]: row["texte_source"] for row in extracted}
    elif relative.lower().endswith(".dat"):
        extracted = extract_message_bank(path, relative)
        actual = {row["id_stable"]: row["traduction_fr"] for row in extracted}
    else:
        extracted = extract_pbs(path, relative)
        actual = {row["id_stable"]: row["texte_source"] for row in extracted}

    errors = []
    for row_id, translated in expected.items():
        if actual.get(row_id) != translated:
            errors.append(f"{relative} — {row_id} : validation différente ou introuvable")
    return errors


def simulate_plan(plan: ReconstructionPlan) -> ReconstructionPlan:
    """Vérifie chaque ligne contre les données originales sans rien écrire."""
    game_root = Path(plan.game_root)
    by_file: dict[str, list[PlanItem]] = defaultdict(list)
    for item in plan.items:
        if item.decision == "applicable":
            by_file[item.fichier].append(item)

    for relative, items in by_file.items():
        try:
            path = _resolve_group_path(game_root, relative, items)
            if relative.lower().endswith(".rxdata"):
                root = load(path)
                if not isinstance(root, RubyObject) or root.class_name != "RPG::Map":
                    raise ReconstructionError("Carte RPG::Map attendue")
                for item in items:
                    # Vérification sur une copie fraîche à chaque item non nécessaire : la fonction
                    # ne modifie qu'après toutes ses validations de ligne.
                    commands, index = _locate_map_message(root, item)
                    command = commands[index]
                    code = command.ivars.get("@code") if isinstance(command, RubyObject) else None
                    if item.type == "Dialogue":
                        if code != 101:
                            raise ReconstructionError("Dialogue 101 introuvable")
                        actual_commands = [command]
                        cursor = index + 1
                        while cursor < len(commands) and isinstance(commands[cursor], RubyObject) and commands[cursor].ivars.get("@code") == 401:
                            actual_commands.append(commands[cursor]); cursor += 1
                        current = "\\n".join(
                            text_value(cmd.ivars.get("@parameters", [""])[0])
                            for cmd in actual_commands
                        ).strip()
                        if current != item.source.strip():
                            raise ReconstructionError("Texte original différent")
                        if len(item.translation.split("\\n")) != len(actual_commands):
                            raise ReconstructionError("Nombre de lignes incompatible")
                    elif item.type == "Choix":
                        params = command.ivars.get("@parameters", []) if isinstance(command, RubyObject) else []
                        choice_index = _integer(item.sub_index, "Index de choix")
                        if code != 102 or not params or not isinstance(params[0], list) or not (0 <= choice_index < len(params[0])):
                            raise ReconstructionError("Choix introuvable")
                        if text_value(params[0][choice_index]).strip() != item.source.strip():
                            raise ReconstructionError("Choix original différent")
            elif relative.lower().endswith(".dat"):
                root = load(path)
                available = {}
                for location, _parent, _key, _child, source, _current in _walk_message_bank_refs(root):
                    location_text = "/".join(map(str, location))
                    available[stable_id("bank", relative, location_text, source)] = source
                for item in items:
                    if available.get(item.id_stable) != item.source.strip():
                        raise ReconstructionError(f"Entrée de banque introuvable : {item.id_stable}")
            elif relative.lower().startswith("pbs/"):
                # Le moteur PBS complet réalise les mêmes vérifications. On l'exécute sur une copie temporaire en mémoire disque.
                import tempfile
                with tempfile.TemporaryDirectory(prefix="pft_sim_") as temp_dir:
                    temp_path = Path(temp_dir) / path.name
                    shutil.copy2(path, temp_path)
                    _apply_pbs_items(temp_path, relative, items)
            else:
                raise ReconstructionError("Format non pris en charge")
        except Exception as exc:
            for item in items:
                if item.decision == "applicable":
                    item.decision = "blocked"
                    item.reason = f"Simulation : {exc}"
    return plan


def _copy_game(source: Path, target: Path, progress: Callable[[str], None] | None = None) -> None:
    source = source.resolve()
    target = target.resolve()
    try:
        target.relative_to(source)
        raise ReconstructionError("La copie française ne peut pas être créée à l'intérieur du jeu original.")
    except ValueError:
        pass
    if target.exists():
        raise ReconstructionError("Le dossier de sortie existe déjà. Choisissez un dossier vide ou supprimez l'ancienne copie.")
    _assert_tree_has_no_links(source)
    if progress:
        progress("Copie complète du fangame…")

    def reject_new_links(directory: str, names: list[str]) -> set[str]:
        for name in names:
            path = Path(directory) / name
            if _is_link_or_junction(path):
                raise ReconstructionError(
                    f"Lien symbolique ou jonction apparu pendant la copie : {path.name}"
                )
        return set()

    def copy_regular_file(source_file: str, target_file: str):
        source_path = Path(source_file)
        if _is_link_or_junction(source_path):
            raise ReconstructionError(
                f"Lien symbolique ou jonction apparu pendant la copie : {source_path.name}"
            )
        return shutil.copy2(source_file, target_file)

    try:
        shutil.copytree(
            source,
            target,
            symlinks=True,
            ignore=reject_new_links,
            copy_function=copy_regular_file,
        )
        _assert_tree_has_no_links(target)
    except Exception:
        if target.is_dir():
            (target / "RECONSTRUCTION_INCOMPLETE.txt").write_text(
                "Cette copie est incomplète et ne doit pas être utilisée.\n"
                "Un lien symbolique, une jonction ou une erreur de copie a été détecté.\n",
                encoding="utf-8",
            )
        raise


def reconstruct_copy(
    plan: ReconstructionPlan,
    target_root: Path,
    report_dir: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> ReconstructionResult:
    source_root = Path(plan.game_root).resolve()
    target_root = target_root.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    if _is_same_or_within(report_dir, source_root):
        raise ReconstructionError("Le dossier des rapports ne peut pas être placé dans le fangame original.")
    if _is_same_or_within(report_dir, target_root):
        raise ReconstructionError("Le dossier des rapports ne peut pas être placé dans la copie française.")
    report_dir.mkdir(parents=True, exist_ok=True)

    applicable = [item for item in plan.items if item.decision == "applicable"]
    if not applicable:
        raise ReconstructionError("Aucune traduction sûre à reconstruire.")

    # Le fangame peut avoir été mis à jour ou déplacé après la simulation. Le
    # plan doit encore correspondre exactement aux fichiers qu'il va utiliser.
    _assert_plan_sources_unchanged(plan, source_root)
    _copy_game(source_root, target_root, progress=(lambda message: progress(0, 1, message) if progress else None))

    by_file: dict[str, list[PlanItem]] = defaultdict(list)
    for item in applicable:
        by_file[item.fichier].append(item)

    modified_files: list[str] = []
    validation_errors: list[str] = []
    applied = 0
    try:
        total_files = len(by_file)
        for file_index, (relative, items) in enumerate(sorted(by_file.items()), start=1):
            if progress:
                progress(file_index, total_files, f"Réinjection : {relative}")
            _apply_file(target_root, relative, items)
            errors = _validate_file(target_root, relative, items)
            if errors:
                validation_errors.extend(errors)
                raise ReconstructionError(errors[0])
            modified_files.append(relative)
            applied += len(items)
        # Une seconde vérification détecte toute modification des fichiers
        # originaux pendant la reconstruction avant d'annoncer un succès.
        _assert_plan_sources_unchanged(plan, source_root)
    except Exception:
        # Une copie incomplète ne doit jamais sembler utilisable.
        marker = target_root / "RECONSTRUCTION_INCOMPLETE.txt"
        marker.write_text(
            "Cette copie est incomplète et ne doit pas être utilisée.\n"
            "Consultez le rapport du projet puis supprimez ce dossier.\n",
            encoding="utf-8",
        )
        raise

    original_unchanged = True

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = report_dir / f"MANIFESTE_RECONSTRUCTION_{timestamp}.json"
    manifest = {
        "version": "1.0",
        "date": datetime.now().isoformat(timespec="seconds"),
        "jeu_original": str(source_root),
        "copie_francaise": str(target_root),
        "csv": plan.csv_path,
        "mode": plan.mode,
        "original_inchange": original_unchanged,
        "fichiers_modifies": modified_files,
        "hachages_originaux": plan.source_hashes,
        "hachages_copie": {
            relative: sha256_file(_resolve_contained_path(target_root, relative))
            for relative in modified_files
        },
        "traductions_appliquees": applied,
        "validation_erreurs": validation_errors,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = report_dir / f"RAPPORT_RECONSTRUCTION_{timestamp}.txt"
    counts = plan.counts()
    report_path.write_text("\n".join([
        "POKÉMON FANGAME TRANSLATOR v1.0.2 — RAPPORT DE RECONSTRUCTION",
        "=" * 82,
        f"Jeu original : {source_root}",
        f"Copie française : {target_root}",
        f"Mode : {plan.mode}",
        "",
        f"Traductions appliquées : {applied}",
        f"Traductions présentes dans le projet : {counts.get('translated_rows', 0)}",
        f"Textes encore non traduits : {counts.get('untranslated_rows', 0)}",
        f"Traductions ignorées : {counts.get('skipped', 0)}",
        f"Traductions bloquées : {counts.get('blocked', 0)}",
        f"Fichiers modifiés : {len(modified_files)}",
        f"Original inchangé : {'OUI' if original_unchanged else 'NON'}",
        f"Erreurs de validation : {len(validation_errors)}",
        "",
        "FICHIERS MODIFIÉS DANS LA COPIE",
        "-" * 82,
        *(modified_files or ["Aucun"]),
        "",
        "IMPORTANT",
        "-" * 82,
        "Scripts.rxdata et PluginScripts.rxdata n'ont jamais été modifiés.",
        "Testez cette copie avant toute diffusion.",
    ]), encoding="utf-8")

    (target_root / "PFT_RECONSTRUCTION_V1.0.txt").write_text(
        "Cette copie a été créée par Pokémon Fangame Translator v1.0.2.\n"
        f"Traductions appliquées : {applied}\n"
        f"Rapport : {report_path}\n"
        "Le dossier original n'a pas été modifié.\n",
        encoding="utf-8",
    )


    (target_root / "LIRE_AVANT_DE_JOUER.txt").write_text(
        "VERSION FRANÇAISE SÉPARÉE\n"
        "===========================\n\n"
        "Ce dossier est une copie jouable du fangame original.\n"
        "Pour jouer, lancez Game.exe ou LANCER_VERSION_FR.bat.\n\n"
        "IMPORTANT\n"
        "- Conservez le dossier original comme sauvegarde propre.\n"
        "- Ne mélangez pas les fichiers de la version FR et de l'original.\n"
        "- Certains textes peuvent rester en anglais s'ils n'ont pas été traduits\n"
        "  ou s'ils ont été ignorés par sécurité.\n"
        "- Une nouvelle reconstruction doit être créée dans un nouveau dossier.\n\n"
        f"Traductions intégrées : {applied}\n"
        f"Textes laissés de côté par sécurité : {counts.get('blocked', 0) + counts.get('skipped', 0)}\n",
        encoding="utf-8",
    )

    (target_root / "LANCER_VERSION_FR.bat").write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "if not exist \"Game.exe\" (\r\n"
        "  echo Game.exe est introuvable dans ce dossier.\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "start \"\" \"Game.exe\"\r\n",
        encoding="utf-8",
    )

    return ReconstructionResult(
        target_root=str(target_root),
        applied=applied,
        skipped=counts.get("skipped", 0),
        blocked=counts.get("blocked", 0),
        modified_files=modified_files,
        validation_errors=validation_errors,
        original_unchanged=original_unchanged,
        report_path=str(report_path),
        manifest_path=str(manifest_path),
    )


def save_plan(plan: ReconstructionPlan, path: Path) -> None:
    payload = asdict(plan)
    payload["counts"] = plan.counts()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
