# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rpg_dialogue import validate_dialogue_command_stream
from ruby_marshal_reader import RubyObject, RubyString, load
from ruby_marshal_writer import dumps
from safe_io import atomic_copy_file

TRANSLATABLE_PBS_KEYS = {
    "Name", "NamePlural", "PortionName", "PortionNamePlural",
    "Description", "Category", "Pokedex", "FormName", "LoseText",
    "VictorySpeech", "IntroText", "EndSpeech", "Title", "DisplayName",
    "BeginSpeech", "EndSpeechWin", "EndSpeechLose", "BattleRemind",
    "BattleRequest", "Body", "End", "Intro", "IntroMorning",
    "IntroAfternoon", "IntroEvening", "MegaMessage", "StorageCreator",
}

RPG_CODE_RE = re.compile(r"\\(?:[A-Za-z]+\[[^\]]*\]|pn|sh|wu|n|l|g|b|r|[.!|^><]|[0-9]+)|<[^>]+>", re.I)


class ExtractionIntegrityError(RuntimeError):
    """L'extraction ne peut pas prouver que ses sources sont restées stables."""


@dataclass(frozen=True)
class ExtractionSource:
    kind: str
    relative_path: str
    path: Path = field(repr=False, compare=False)
    size: int
    sha256: str
    signature: tuple[int, int, int, int] = field(repr=False)

    def public_record(self) -> dict[str, str | int]:
        return {
            "kind": self.kind,
            "relative_path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ExtractionInventory:
    root: Path = field(compare=False)
    root_signature: tuple[int, int, int, int]
    tree_entries: tuple[tuple[str, str], ...]
    directory_signatures: tuple[tuple[str, tuple[int, int, int, int]], ...]
    sources: tuple[ExtractionSource, ...]
    source_manifest_sha256: str

    def operation_token(self) -> tuple[object, ...]:
        return (
            self.root_signature,
            self.tree_entries,
            self.directory_signatures,
            tuple(
                (
                    source.kind,
                    source.relative_path,
                    source.size,
                    source.sha256,
                    source.signature,
                )
                for source in self.sources
            ),
        )


@dataclass(frozen=True)
class StructuredExtractionResult:
    rows: list[dict]
    errors: list[str]
    sources: tuple[ExtractionSource, ...]
    source_manifest_sha256: str
    essentials_profile: str = ""
    declared_version: str = ""
    version_detection_method: str = ""


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x0400)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ExtractionIntegrityError(
            "Impossible de vérifier si une source Essentials est redirigée."
        ) from exc


def _source_signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _canonical_extraction_root(root: Path) -> Path:
    requested = root.expanduser()
    if not requested.is_dir() or _is_link_or_junction(requested):
        raise ExtractionIntegrityError(
            "Le dossier du fangame est absent, inaccessible ou redirigé."
        )
    canonical = requested.resolve()
    if not canonical.is_dir() or _is_link_or_junction(canonical):
        raise ExtractionIntegrityError(
            "La racine canonique du fangame est absente ou redirigée."
        )
    return canonical


def _relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ExtractionIntegrityError(
            "Une source Essentials sort du dossier canonique du fangame."
        ) from exc
    invalid_windows = set('<>:"|?*')
    reserved_windows = {
        "con", "prn", "aux", "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    for part in Path(relative).parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or any(ord(character) < 32 or character in invalid_windows for character in part)
            or part.split(".", 1)[0].casefold() in reserved_windows
        ):
            raise ExtractionIntegrityError(
                f"Chemin de source ambigu sous Windows refusé : {relative}."
            )
    return relative


def _assert_no_redirected_components(root: Path, path: Path) -> None:
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise ExtractionIntegrityError(
            "Une source Essentials sort du dossier canonique du fangame."
        ) from exc
    current = root
    if _is_link_or_junction(current):
        raise ExtractionIntegrityError("La racine du fangame est redirigée.")
    for part in parts:
        current = current / part
        if _is_link_or_junction(current):
            raise ExtractionIntegrityError(
                f"Source Essentials redirigée refusée : {_relative(current, root)}."
            )


def _scan_tree(root: Path, directory: Path) -> dict[str, tuple[Path, str]]:
    if not directory.exists():
        return {}
    if not directory.is_dir() or _is_link_or_junction(directory):
        raise ExtractionIntegrityError(
            f"Dossier critique absent ou redirigé : {_relative(directory, root)}."
        )
    pending = [directory]
    entries: dict[str, tuple[Path, str]] = {}
    folded_paths: dict[str, str] = {}
    while pending:
        current = pending.pop()
        _assert_no_redirected_components(root, current)
        try:
            current_entries = list(os.scandir(current))
        except OSError as exc:
            raise ExtractionIntegrityError(
                f"Impossible d'inventorier le dossier critique {_relative(current, root)}."
            ) from exc
        for entry in current_entries:
            path = Path(entry.path)
            relative = _relative(path, root)
            folded = relative.casefold()
            previous = folded_paths.get(folded)
            if previous is not None and previous != relative:
                raise ExtractionIntegrityError(
                    "Deux sources Essentials ont des chemins ambigus sous Windows : "
                    f"{previous} et {relative}."
                )
            folded_paths[folded] = relative
            if _is_link_or_junction(path):
                raise ExtractionIntegrityError(
                    f"Source Essentials redirigée refusée : {relative}."
                )
            try:
                if entry.is_dir(follow_symlinks=False):
                    entry_type = "directory"
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    entry_type = "file"
                else:
                    raise ExtractionIntegrityError(
                        f"Entrée spéciale non vérifiable refusée : {relative}."
                    )
            except OSError as exc:
                raise ExtractionIntegrityError(
                    f"Type de source impossible à vérifier : {relative}."
                ) from exc
            entries[relative] = (path, entry_type)
    return entries


def _hash_stable_source(root: Path, path: Path, kind: str) -> ExtractionSource:
    relative = _relative(path, root)
    _assert_no_redirected_components(root, path)
    if not path.is_file():
        raise ExtractionIntegrityError(f"Source Essentials absente : {relative}.")
    digest = hashlib.sha256()
    try:
        before = path.stat()
        with path.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            if _source_signature(before) != _source_signature(opened_before):
                raise ExtractionIntegrityError(
                    f"Source Essentials remplacée avant lecture : {relative}."
                )
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            opened_after = os.fstat(handle.fileno())
        after = path.stat()
    except ExtractionIntegrityError:
        raise
    except OSError as exc:
        raise ExtractionIntegrityError(
            f"Source Essentials impossible à lire : {relative}."
        ) from exc
    signatures = {
        _source_signature(before),
        _source_signature(opened_before),
        _source_signature(opened_after),
        _source_signature(after),
    }
    if len(signatures) != 1 or _is_link_or_junction(path):
        raise ExtractionIntegrityError(
            f"Source Essentials modifiée pendant son inventaire : {relative}."
        )
    return ExtractionSource(
        kind=kind,
        relative_path=relative,
        path=path,
        size=after.st_size,
        sha256=digest.hexdigest(),
        signature=_source_signature(after),
    )


def _source_kind(relative: str, entry_type: str) -> str | None:
    path = Path(relative)
    name = path.name.casefold()
    parent = path.parent.as_posix().casefold()
    if relative.casefold() in {"game.exe", "game.ini", "mkxp.json"}:
        return "identity"
    if parent == "data":
        if name in {"system.rxdata", "scripts.rxdata", "pluginscripts.rxdata"}:
            return "identity"
        if name == "mapinfos.rxdata":
            return "map_names"
        if name == "commonevents.rxdata":
            return "common_events"
        if re.fullmatch(r"map\d{3,4}\.rxdata", name, re.I):
            return "map"
        if name in {"messages.dat", "messages_game.dat", "messages_core.dat"}:
            return "bank"
    if relative.casefold().startswith("pbs/") and name.endswith(".txt"):
        if any("backup" in part.casefold() for part in path.parts[1:]):
            return None
        return "pbs"
    return None


def build_extraction_inventory(root: Path) -> ExtractionInventory:
    canonical = _canonical_extraction_root(root)
    try:
        root_before = canonical.stat()
    except OSError as exc:
        raise ExtractionIntegrityError("La racine du fangame est inaccessible.") from exc
    data = canonical / "Data"
    if not data.is_dir() or _is_link_or_junction(data):
        raise ExtractionIntegrityError("Le dossier Data est absent ou redirigé.")

    tree = _scan_tree(canonical, data)
    pbs = canonical / "PBS"
    if pbs.exists():
        tree.update(_scan_tree(canonical, pbs))

    for direct in (
        canonical / "Game.exe",
        canonical / "Game.ini",
        canonical / "mkxp.json",
    ):
        if direct.exists():
            if not direct.is_file() or _is_link_or_junction(direct):
                raise ExtractionIntegrityError(
                    f"Marqueur d'identité Essentials redirigé ou ambigu : {direct.name}."
                )
            tree[direct.name] = (direct, "file")

    sources: list[ExtractionSource] = []
    for relative, (path, entry_type) in sorted(
        tree.items(), key=lambda item: item[0].casefold()
    ):
        kind = _source_kind(relative, entry_type)
        if kind is None:
            continue
        if entry_type != "file":
            raise ExtractionIntegrityError(
                f"Source Essentials ambiguë : {relative} n'est pas un fichier ordinaire."
            )
        sources.append(_hash_stable_source(canonical, path, kind))

    try:
        root_after = canonical.stat()
    except OSError as exc:
        raise ExtractionIntegrityError("La racine du fangame a disparu.") from exc
    if _source_signature(root_before) != _source_signature(root_after):
        raise ExtractionIntegrityError(
            "La racine du fangame a changé pendant l'inventaire."
        )

    public_sources = [source.public_record() for source in sources]
    serialized = json.dumps(
        public_sources,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    directories = [("Data", data)]
    if pbs.exists():
        directories.append(("PBS", pbs))
    directories.extend(
        (relative, path)
        for relative, (path, entry_type) in tree.items()
        if entry_type == "directory"
    )
    directory_signatures: list[tuple[str, tuple[int, int, int, int]]] = []
    for relative, directory in sorted(
        directories,
        key=lambda item: item[0].casefold(),
    ):
        _assert_no_redirected_components(canonical, directory)
        try:
            directory_signatures.append(
                (relative, _source_signature(directory.stat()))
            )
        except OSError as exc:
            raise ExtractionIntegrityError(
                f"Dossier source disparu pendant l'inventaire : {relative}."
            ) from exc

    return ExtractionInventory(
        root=canonical,
        root_signature=_source_signature(root_after),
        tree_entries=tuple(
            sorted(
                ((relative, entry_type) for relative, (_path, entry_type) in tree.items()),
                key=lambda item: item[0].casefold(),
            )
        ),
        directory_signatures=tuple(directory_signatures),
        sources=tuple(sources),
        source_manifest_sha256=hashlib.sha256(serialized).hexdigest(),
    )


def stable_id(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def text_value(value) -> str:
    if isinstance(value, RubyString):
        return value.text()
    if isinstance(value, str):
        return value
    return ""


def looks_visible(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 3000:
        return False
    if text.startswith(("http://", "https://", "www.")):
        return False
    return sum(ch.isalpha() for ch in text) >= 1


def codes(text: str) -> str:
    return " | ".join(RPG_CODE_RE.findall(text))


def load_map_names(data_dir: Path, *, strict: bool = False) -> dict[int, str]:
    path = data_dir / "MapInfos.rxdata"
    if not path.exists():
        return {}
    root = load(path)
    names = {}
    if strict and not isinstance(root, dict):
        raise ValueError("Table MapInfos attendue")
    if isinstance(root, dict):
        for map_id, info in root.items():
            if isinstance(map_id, int) and isinstance(info, RubyObject):
                name = text_value(info.ivars.get("@name"))
                if name:
                    names[map_id] = name
    return names


def map_id_from_path(path: Path) -> int | None:
    match = re.fullmatch(r"Map(\d{3,4})\.rxdata", path.name, re.I)
    return int(match.group(1)) if match else None


def _choice_branch_metadata(
    commands: list,
    command_index: int,
    choice_index: int,
    source: str,
    indent,
) -> tuple[int | str, int | str]:
    """Localise l'unique libellé 402 correspondant à un choix 102.

    RPG Maker XP duplique le libellé d'un choix dans la commande de branche
    402. L'extraction reste possible si une structure personnalisée ne suit
    pas cette convention, mais la métadonnée demeure vide : une future
    réinjection stricte pourra alors refuser le cas au lieu de l'inventer.
    """
    matches: list[int] = []
    for branch_index in range(command_index + 1, len(commands)):
        branch = commands[branch_index]
        if not isinstance(branch, RubyObject):
            continue
        branch_code = branch.ivars.get("@code")
        branch_indent = branch.ivars.get("@indent")
        if branch_code == 404 and branch_indent == indent:
            break
        if branch_code != 402 or branch_indent != indent:
            continue
        parameters = branch.ivars.get("@parameters", [])
        if (
            isinstance(parameters, list)
            and len(parameters) >= 2
            and parameters[0] == choice_index
            and text_value(parameters[1]).strip() == source
        ):
            matches.append(branch_index)
    if len(matches) != 1:
        return "", ""
    return matches[0], 1


def extract_map(
    path: Path,
    relative: str,
    map_name: str,
    *,
    strict: bool = False,
) -> list[dict]:
    root = load(path)
    if not isinstance(root, RubyObject) or root.class_name != "RPG::Map":
        if strict:
            raise ValueError("Objet RPG::Map attendu")
        return []
    map_id = map_id_from_path(path)
    rows: list[dict] = []
    events = root.ivars.get("@events", {})
    if not isinstance(events, dict):
        return rows

    for event_id in sorted(k for k in events if isinstance(k, int)):
        event = events[event_id]
        if not isinstance(event, RubyObject):
            continue
        event_name = text_value(event.ivars.get("@name")) or f"Événement {event_id}"
        pages = event.ivars.get("@pages", [])
        if not isinstance(pages, list):
            continue
        for page_index, page in enumerate(pages, start=1):
            if not isinstance(page, RubyObject):
                continue
            commands = page.ivars.get("@list", [])
            if not isinstance(commands, list):
                continue
            dialogue_segments = {
                segment.start_index: segment
                for segment in validate_dialogue_command_stream(commands)
            }
            index = 0
            while index < len(commands):
                command = commands[index]
                if not isinstance(command, RubyObject):
                    index += 1
                    continue
                code = command.ivars.get("@code")
                params = command.ivars.get("@parameters", [])

                if code == 101:
                    segmentation = dialogue_segments[index]
                    end_index = segmentation.end_index
                    message = segmentation.source_text.strip()
                    if looks_visible(message):
                        rows.append({
                            "id_stable": stable_id("map", map_id, event_id, page_index, index, "message"),
                            "type": "Dialogue",
                            "fichier": relative,
                            "carte_id": map_id or "",
                            "carte_nom": map_name,
                            "evenement_id": event_id,
                            "evenement_nom": event_name,
                            "page": page_index,
                            "commande": index,
                            "sous_index": "",
                            "rpg_command_code": 101,
                            "rpg_command_indent": command.ivars.get("@indent", ""),
                            "rpg_parameter_index": 0,
                            "rpg_continuation_end": end_index,
                            "rpg_dialogue_segments": segmentation.metadata,
                            "texte_source": message,
                            "traduction_fr": "",
                            "codes_proteges": codes(message),
                            "statut": "À traduire",
                        })
                    index = end_index + 1
                    continue

                if code == 102 and isinstance(params, list) and params:
                    choices = params[0]
                    if isinstance(choices, list):
                        for choice_index, choice in enumerate(choices):
                            choice_text = text_value(choice).strip()
                            if looks_visible(choice_text):
                                branch_command, branch_parameter = _choice_branch_metadata(
                                    commands,
                                    index,
                                    choice_index,
                                    choice_text,
                                    command.ivars.get("@indent", ""),
                                )
                                rows.append({
                                    "id_stable": stable_id("map", map_id, event_id, page_index, index, "choice", choice_index),
                                    "type": "Choix",
                                    "fichier": relative,
                                    "carte_id": map_id or "",
                                    "carte_nom": map_name,
                                    "evenement_id": event_id,
                                    "evenement_nom": event_name,
                                    "page": page_index,
                                    "commande": index,
                                    "sous_index": choice_index,
                                    "rpg_command_code": 102,
                                    "rpg_command_indent": command.ivars.get("@indent", ""),
                                    "rpg_parameter_index": 0,
                                    "rpg_continuation_end": index,
                                    "rpg_choice_branch_command": branch_command,
                                    "rpg_choice_branch_parameter_index": branch_parameter,
                                    "texte_source": choice_text,
                                    "traduction_fr": "",
                                    "codes_proteges": codes(choice_text),
                                    "statut": "À traduire",
                                })
                index += 1
    return rows


def extract_common_events(
    path: Path,
    relative: str,
    *,
    strict: bool = False,
) -> list[dict]:
    """Extrait les commandes textuelles des événements communs sans les modifier."""
    root = load(path)
    if not isinstance(root, list):
        if strict:
            raise ValueError("Table RPG::CommonEvent attendue")
        return []
    rows: list[dict] = []
    for array_index, event in enumerate(root):
        if event is None:
            continue
        if not isinstance(event, RubyObject) or event.class_name != "RPG::CommonEvent":
            if strict:
                raise ValueError(
                    f"Entrée CommonEvents non reconnue à l'index {array_index}"
                )
            continue
        declared_id = event.ivars.get("@id")
        event_id = declared_id if isinstance(declared_id, int) else array_index
        event_name = text_value(event.ivars.get("@name")) or f"Événement commun {event_id}"
        event_sha256 = hashlib.sha256(dumps(event)).hexdigest()
        event_trigger = event.ivars.get("@trigger", "")
        event_switch_id = event.ivars.get("@switch_id", "")
        commands = event.ivars.get("@list", [])
        if not isinstance(commands, list):
            if strict:
                raise ValueError(
                    f"Liste de commandes CommonEvents invalide à l'index {array_index}"
                )
            continue
        dialogue_segments = {
            segment.start_index: segment
            for segment in validate_dialogue_command_stream(commands)
        }
        index = 0
        while index < len(commands):
            command = commands[index]
            if not isinstance(command, RubyObject):
                index += 1
                continue
            code = command.ivars.get("@code")
            params = command.ivars.get("@parameters", [])
            if code == 101:
                segmentation = dialogue_segments[index]
                end_index = segmentation.end_index
                message = segmentation.source_text.strip()
                if looks_visible(message):
                    rows.append({
                        "id_stable": stable_id(
                            "common_event", relative, array_index, event_id, index, "message"
                        ),
                        "type": "Événement commun — Dialogue",
                        "fichier": relative,
                        "carte_id": "",
                        "carte_nom": "Événements communs",
                        "evenement_id": event_id,
                        "evenement_nom": event_name,
                        "page": "",
                        "commande": index,
                        "sous_index": "",
                        "rpg_command_code": 101,
                        "rpg_command_indent": command.ivars.get("@indent", ""),
                        "rpg_parameter_index": 0,
                        "rpg_continuation_end": end_index,
                        "rpg_dialogue_segments": segmentation.metadata,
                        "rpg_common_event_array_index": array_index,
                        "rpg_common_event_trigger": event_trigger,
                        "rpg_common_event_switch_id": event_switch_id,
                        "rpg_common_event_sha256": event_sha256,
                        "texte_source": message,
                        "traduction_fr": "",
                        "codes_proteges": codes(message),
                        "statut": "À traduire",
                    })
                index = end_index + 1
                continue
            if code == 102 and isinstance(params, list) and params and isinstance(params[0], list):
                for choice_index, choice in enumerate(params[0]):
                    choice_text = text_value(choice).strip()
                    if looks_visible(choice_text):
                        rows.append({
                            "id_stable": stable_id(
                                "common_event",
                                relative,
                                array_index,
                                event_id,
                                index,
                                "choice",
                                choice_index,
                            ),
                            "type": "Événement commun — Choix",
                            "fichier": relative,
                            "carte_id": "",
                            "carte_nom": "Événements communs",
                            "evenement_id": event_id,
                            "evenement_nom": event_name,
                            "page": "",
                            "commande": index,
                            "sous_index": choice_index,
                            "rpg_command_code": 102,
                            "rpg_command_indent": command.ivars.get("@indent", ""),
                            "rpg_parameter_index": 0,
                            "rpg_continuation_end": index,
                            "rpg_common_event_array_index": array_index,
                            "rpg_common_event_trigger": event_trigger,
                            "rpg_common_event_switch_id": event_switch_id,
                            "rpg_common_event_sha256": event_sha256,
                            "texte_source": choice_text,
                            "traduction_fr": "",
                            "codes_proteges": codes(choice_text),
                            "statut": "À traduire",
                        })
            index += 1
    return rows


def walk_message_bank(value, path=()):
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_message_bank(child, path + (index,))
    elif isinstance(value, dict):
        entry_index = 0
        for key, child in value.items():
            if key == "__default__":
                continue
            key_text = text_value(key).strip()
            value_text = text_value(child).strip()
            if looks_visible(key_text):
                yield path + ("entry", entry_index), key_text, value_text
                entry_index += 1
            elif isinstance(child, (list, dict)):
                yield from walk_message_bank(child, path + ("value", entry_index))
                entry_index += 1


def extract_message_bank(path: Path, relative: str) -> list[dict]:
    root = load(path)
    if not isinstance(root, (list, dict)):
        raise ValueError("Banque de messages Array ou Hash attendue")
    rows = []
    for location, source, current in walk_message_bank(root):
        location_text = "/".join(map(str, location))
        rows.append({
            "id_stable": stable_id("bank", relative, location_text, source),
            "type": "Banque de messages",
            "fichier": relative,
            "carte_id": "",
            "carte_nom": "",
            "evenement_id": "",
            "evenement_nom": location_text,
            "page": "",
            "commande": "",
            "sous_index": "",
            "texte_source": source,
            "traduction_fr": "" if not current or current == source else current,
            "codes_proteges": codes(source),
            "statut": "Déjà traduit" if current and current != source else "À traduire",
        })
    return rows


def iter_pbs_files(pbs_dir: Path):
    if not pbs_dir.is_dir():
        return
    for path in sorted(pbs_dir.rglob("*.txt")):
        if any("backup" in part.lower() for part in path.relative_to(pbs_dir).parts):
            continue
        yield path


def is_translatable_pbs_key(key: str) -> bool:
    return key in TRANSLATABLE_PBS_KEYS or bool(re.fullmatch(r"Body\d+", key))


def _pbs_format(raw: bytes) -> tuple[str, str, str, str]:
    bom = "utf-8" if raw.startswith(b"\xef\xbb\xbf") else ""
    try:
        content = raw.decode("utf-8-sig")
        encoding = "utf-8-sig" if bom else "utf-8"
    except UnicodeDecodeError:
        content = raw.decode("cp1252")
        encoding = "cp1252"
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    newline = "CRLF" if crlf and not lf else ("LF" if lf and not crlf else "mixed")
    return content, encoding, bom, newline


def _point_fields(value: str) -> list[str]:
    try:
        return next(csv.reader([value], skipinitialspace=False))
    except (csv.Error, StopIteration):
        return []


def _pbs_value_sha256(value: str, encoding: str) -> str:
    codec = encoding.replace("-sig", "")
    return hashlib.sha256(value.encode(codec)).hexdigest()


def extract_pbs(path: Path, relative: str) -> list[dict]:
    raw = path.read_bytes()
    content, encoding, bom, newline = _pbs_format(raw)
    rows = []
    section = "GLOBAL"
    occurrence: Counter[tuple[str, str]] = Counter()
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        section_match = re.match(r"^\[([^\]]+)\]", line)
        if section_match:
            section = section_match.group(1).strip()
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key == "Point" and Path(relative).name.casefold() == "town_map.txt":
            occurrence[(section, key)] += 1
            sub_index = occurrence[(section, key)]
            fields = _point_fields(value)
            for field_index, field_name in ((2, "Name"), (3, "Description")):
                if field_index >= len(fields):
                    continue
                field_value = fields[field_index].strip()
                if not looks_visible(field_value):
                    continue
                rows.append({
                    "id_stable": stable_id(
                        "pbs_structured", relative, section, key, sub_index, field_index
                    ),
                    "type": f"PBS v21.1 — Point.{field_name}",
                    "fichier": relative,
                    "carte_id": "",
                    "carte_nom": "",
                    "evenement_id": section,
                    "evenement_nom": section,
                    "page": "",
                    "commande": key,
                    "sous_index": f"{sub_index}:field:{field_index}",
                    "texte_source": field_value,
                    "traduction_fr": "",
                    "codes_proteges": codes(field_value),
                    "statut": "À traduire",
                    "pbs_encoding": encoding,
                    "pbs_bom": bom,
                    "pbs_newline": newline,
                    "pbs_field_index": field_index,
                    "pbs_value_sha256": _pbs_value_sha256(value, encoding),
                })
            continue
        if not is_translatable_pbs_key(key) or not looks_visible(value):
            continue
        occurrence[(section, key)] += 1
        sub_index = occurrence[(section, key)]
        rows.append({
            "id_stable": stable_id("pbs", relative, section, key, sub_index),
            "type": f"PBS — {key}",
            "fichier": relative,
            "carte_id": "",
            "carte_nom": "",
            "evenement_id": section,
            "evenement_nom": section,
            "page": "",
            "commande": key,
            "sous_index": sub_index,
            "texte_source": value,
            "traduction_fr": "",
            "codes_proteges": codes(value),
            "statut": "À traduire",
            "pbs_encoding": encoding,
            "pbs_bom": bom,
            "pbs_newline": newline,
            "pbs_field_index": "",
            "pbs_value_sha256": _pbs_value_sha256(value, encoding),
        })
    return rows


def _is_same_or_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _snapshot_sources(inventory: ExtractionInventory, snapshot_root: Path) -> None:
    if _is_same_or_within(snapshot_root, inventory.root):
        raise ExtractionIntegrityError(
            "L'instantané temporaire d'extraction ne peut pas être placé dans le fangame."
        )
    for source in inventory.sources:
        destination = snapshot_root.joinpath(*Path(source.relative_path).parts)
        try:
            atomic_copy_file(
                source.path,
                destination,
                expected_sha256=source.sha256,
                replace_existing=False,
            )
            current = source.path.stat()
        except OSError as exc:
            raise ExtractionIntegrityError(
                f"La source {source.relative_path} a changé avant la création de l'instantané."
            ) from exc
        _assert_no_redirected_components(inventory.root, source.path)
        if _source_signature(current) != source.signature:
            raise ExtractionIntegrityError(
                f"La source {source.relative_path} a été remplacée avant son extraction."
            )


def _extract_snapshot(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    *,
    progress=None,
) -> list[dict]:
    records = {source.relative_path: source for source in inventory.sources}
    map_names_record = next(
        (source for source in inventory.sources if source.kind == "map_names"),
        None,
    )
    map_names = (
        load_map_names(snapshot_root / "Data", strict=True)
        if map_names_record is not None
        else {}
    )
    candidates = [
        source
        for source in inventory.sources
        if source.kind in {"map", "common_events", "bank", "pbs"}
    ]
    order = {"map": 0, "common_events": 1, "bank": 2, "pbs": 3}
    candidates.sort(key=lambda source: (order[source.kind], source.relative_path.casefold()))
    rows: list[dict] = []
    total = max(1, len(candidates))
    for index, source in enumerate(candidates, start=1):
        path = snapshot_root.joinpath(*Path(source.relative_path).parts)
        try:
            if source.kind == "map":
                map_id = map_id_from_path(path)
                extracted = extract_map(
                    path,
                    source.relative_path,
                    map_names.get(map_id or -1, ""),
                    strict=True,
                )
            elif source.kind == "common_events":
                extracted = extract_common_events(
                    path,
                    source.relative_path,
                    strict=True,
                )
            elif source.kind == "bank":
                extracted = extract_message_bank(path, source.relative_path)
            else:
                extracted = extract_pbs(path, source.relative_path)
        except Exception as exc:
            raise ExtractionIntegrityError(
                "Extraction Essentials refusée : source compatible illisible "
                f"{source.relative_path} ({type(exc).__name__})."
            ) from exc
        for row in extracted:
            for field_name in (
                "rpg_command_code",
                "rpg_command_indent",
                "rpg_parameter_index",
                "rpg_continuation_end",
                "rpg_dialogue_segments",
                "rpg_common_event_array_index",
                "rpg_common_event_trigger",
                "rpg_common_event_switch_id",
                "rpg_common_event_sha256",
                "rpg_choice_branch_command",
                "rpg_choice_branch_parameter_index",
                "pbs_encoding",
                "pbs_bom",
                "pbs_newline",
                "pbs_field_index",
                "pbs_value_sha256",
            ):
                row.setdefault(field_name, "")
            row["adaptateur"] = "pokemon_essentials"
            row["source_sha256"] = records[source.relative_path].sha256
            row["source_manifest_sha256"] = inventory.source_manifest_sha256
        rows.extend(extracted)
        if progress:
            progress(index, total, source.relative_path)

    duplicates = [
        row_id
        for row_id, count in Counter(
            str(row.get("id_stable") or "") for row in rows
        ).items()
        if not row_id or count > 1
    ]
    if duplicates:
        raise ExtractionIntegrityError(
            "Extraction Essentials ambiguë : identifiants d'occurrence absents ou dupliqués."
        )
    return rows


def extract_structured_verified(
    root: Path,
    progress=None,
    logger=None,
) -> StructuredExtractionResult:
    del logger  # Les erreurs sont bloquantes et remontées sans résultat partiel.
    before = build_extraction_inventory(root)
    try:
        with tempfile.TemporaryDirectory(prefix="pft_essentials_extraction_") as temp_dir:
            snapshot_root = Path(temp_dir) / "snapshot"
            snapshot_root.mkdir()
            _snapshot_sources(before, snapshot_root)
            rows = _extract_snapshot(snapshot_root, before, progress=progress)
    except ExtractionIntegrityError:
        raise
    except OSError as exc:
        raise ExtractionIntegrityError(
            "Impossible de créer ou supprimer l'instantané temporaire d'extraction."
        ) from exc

    after = build_extraction_inventory(before.root)
    if before.operation_token() != after.operation_token():
        raise ExtractionIntegrityError(
            "Les sources Essentials ont été modifiées, ajoutées, supprimées ou réorientées "
            "pendant l'extraction. Aucun résultat n'est accepté."
        )
    return StructuredExtractionResult(
        rows=rows,
        errors=[],
        sources=before.sources,
        source_manifest_sha256=before.source_manifest_sha256,
    )


def extract_structured(root: Path, progress=None, logger=None) -> tuple[list[dict], list[str]]:
    result = extract_structured_verified(root, progress=progress, logger=logger)
    return result.rows, result.errors


FIELDNAMES = [
    "id_stable", "type", "fichier", "carte_id", "carte_nom",
    "evenement_id", "evenement_nom", "page", "commande", "sous_index",
    "rpg_command_code", "rpg_command_indent", "rpg_parameter_index",
    "rpg_continuation_end", "rpg_dialogue_segments",
    "rpg_common_event_array_index", "rpg_common_event_trigger",
    "rpg_common_event_switch_id", "rpg_common_event_sha256",
    "rpg_choice_branch_command",
    "rpg_choice_branch_parameter_index",
    "texte_source", "traduction_fr", "codes_proteges", "statut",
    "pbs_encoding", "pbs_bom", "pbs_newline", "pbs_field_index",
    "pbs_value_sha256", "adaptateur", "source_sha256",
    "source_manifest_sha256", "profil_essentials",
    "version_essentials_declaree", "methode_version_essentials",
]


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
