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

from essentials_town_map import (
    COMPILED_TOWN_MAP_FILE,
    TownMapIntegrityError,
    build_compiled_point_proof,
    validate_compiled_town_map_sections,
)
from essentials_phone import (
    COMPILED_PHONE_FILE,
    PHONE_MESSAGES_FILE,
    PhoneIntegrityError,
    build_phone_entry_proofs,
)
from essentials_trainer import (
    COMPILED_TRAINER_FILE,
    TRAINER_MESSAGES_FILE,
    TRAINER_PBS_FILE,
    TrainerIntegrityError,
    build_trainer_entry_proofs,
)
from essentials_ability import (
    ABILITY_MESSAGES_FILE,
    ABILITY_PBS_FILE,
    COMPILED_ABILITY_FILE,
    AbilityIntegrityError,
    build_ability_description_proofs,
)
from essentials_species import (
    COMPILED_SPECIES_FILE,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
    SpeciesIntegrityError,
    build_species_pokedex_proofs,
)
from essentials_map_metadata import (
    COMPILED_MAP_METADATA_FILE,
    MAP_METADATA_MESSAGES_FILE,
    MAP_METADATA_PBS_FILE,
    MapMetadataIntegrityError,
    build_map_metadata_name_proofs,
)
from essentials_move import (
    COMPILED_MOVE_FILE,
    MOVE_MESSAGES_FILE,
    MOVE_PBS_FILE,
    MoveIntegrityError,
    build_move_text_proofs,
)
from essentials_item import (
    COMPILED_ITEM_FILE,
    ITEM_MESSAGES_FILE,
    ITEM_PBS_FILE,
    ITEM_TEXT_FIELDS,
    ItemIntegrityError,
    build_item_text_proofs,
)
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
    "IntroAfternoon", "IntroEvening", "StorageCreator",
}

PBS_POINT_STRUCTURE_FORMAT = "pft_pbs_point_structure_v1"

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
        if name == "town_map.dat":
            return "compiled_town_map"
        if name == "phone.dat":
            return "compiled_phone"
        if name == "trainers.dat":
            return "compiled_trainer"
        if name == "abilities.dat":
            return "compiled_ability"
        if name == "species.dat":
            return "compiled_species"
        if name == "map_metadata.dat":
            return "compiled_map_metadata"
        if name == "moves.dat":
            return "compiled_move"
        if name == "items.dat":
            return "compiled_item"
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
    choice_closed = False
    for branch_index in range(command_index + 1, len(commands)):
        branch = commands[branch_index]
        if not isinstance(branch, RubyObject):
            continue
        branch_code = branch.ivars.get("@code")
        branch_indent = branch.ivars.get("@indent")
        if branch_code == 404 and branch_indent == indent:
            choice_closed = True
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
    if not choice_closed or len(matches) != 1:
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
                        branch_command, branch_parameter = _choice_branch_metadata(
                            commands,
                            index,
                            choice_index,
                            choice_text,
                            command.ivars.get("@indent", ""),
                        )
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
                            "rpg_choice_branch_command": branch_command,
                            "rpg_choice_branch_parameter_index": branch_parameter,
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


def is_translatable_pbs_key(key: str, relative: str = "") -> bool:
    # Dans moves.txt, Category est l'enum de combat Physical/Special/Status.
    # Le nom homonyme reste textuel dans certains autres PBS (Species, etc.).
    if Path(relative).name.casefold() == "moves.txt":
        return key in {"Name", "Description"}
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


def _split_pbs_line(raw_line: str) -> tuple[str, str]:
    if raw_line.endswith("\r\n"):
        return raw_line[:-2], "\r\n"
    if raw_line.endswith("\n"):
        return raw_line[:-1], "\n"
    return raw_line, ""


def _pbs_assignment_parts(body: str) -> tuple[str, str, str, str] | None:
    """Découpe une affectation PBS sans normaliser ses espaces.

    Le préfixe comprend le signe ``=`` et les espaces qui le suivent. Les
    espaces finaux restent séparés afin qu'une réinjection puisse remplacer
    uniquement la valeur sans reformater la ligne.
    """
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
    value = remaining[:-trailing_length] if trailing_length else remaining
    prefix = body[: separator + 1] + after[:leading_length]
    trailing = remaining[-trailing_length:] if trailing_length else ""
    return prefix, key, value, trailing


def _strict_point_layout(value: str) -> dict | None:
    """Retourne les limites exactes d'un Point CSV simple et non cité.

    Les formes avec guillemets restent extractibles via ``csv.reader``, mais ne
    reçoivent aucune preuve de reconstruction privée : les réécrire demanderait
    une normalisation CSV potentiellement ambiguë.
    """
    if any(character in value for character in ('"', "\r", "\n")):
        return None
    separator_offsets = [index for index, character in enumerate(value) if character == ","]
    fields = []
    start = 0
    for end in separator_offsets + [len(value)]:
        raw_field = value[start:end]
        leading = len(raw_field) - len(raw_field.lstrip(" \t"))
        remaining = raw_field[leading:]
        trailing = len(remaining) - len(remaining.rstrip(" \t"))
        core_end = end - trailing
        fields.append(
            {
                "start": start,
                "end": end,
                "core_start": start + leading,
                "core_end": core_end,
                "raw": raw_field,
                "core": value[start + leading : core_end],
                "leading": leading,
                "trailing": trailing,
            }
        )
        start = end + 1
    return {"fields": fields, "separator_offsets": separator_offsets}


def build_pbs_point_structure_proof(
    *,
    body: str,
    line_ending: str,
    encoding: str,
    section: str,
    key_occurrence: int,
    line_number: int,
    field_index: int,
    file_sha256: str,
) -> str:
    """Crée une preuve sans texte permettant une réinjection Point exacte."""
    assignment = _pbs_assignment_parts(body)
    if assignment is None:
        return ""
    prefix, key, value, trailing = assignment
    if key != "Point":
        return ""
    layout = _strict_point_layout(value)
    if layout is None or not (0 <= field_index < len(layout["fields"])):
        return ""
    codec = encoding.replace("-sig", "")
    non_target = "\0".join(
        f"{index}:{field['raw']}"
        for index, field in enumerate(layout["fields"])
        if index != field_index
    )
    proof = {
        "format": PBS_POINT_STRUCTURE_FORMAT,
        "line_number": line_number,
        "section": section,
        "key_occurrence": key_occurrence,
        "field_count": len(layout["fields"]),
        "field_index": field_index,
        "field_spans": [
            [field["start"], field["end"], field["leading"], field["trailing"]]
            for field in layout["fields"]
        ],
        "separator": ",",
        "separator_offsets": layout["separator_offsets"],
        "newline": "CRLF" if line_ending == "\r\n" else ("LF" if line_ending == "\n" else "NONE"),
        "prefix_sha256": hashlib.sha256(prefix.encode(codec)).hexdigest(),
        "trailing_sha256": hashlib.sha256(trailing.encode(codec)).hexdigest(),
        "value_sha256": _pbs_value_sha256(value, encoding),
        "line_sha256": hashlib.sha256((body + line_ending).encode(codec)).hexdigest(),
        "non_target_fields_sha256": hashlib.sha256(non_target.encode(codec)).hexdigest(),
        "file_sha256": file_sha256,
    }
    return json.dumps(proof, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _pbs_value_sha256(value: str, encoding: str) -> str:
    codec = encoding.replace("-sig", "")
    return hashlib.sha256(value.encode(codec)).hexdigest()


def extract_pbs(path: Path, relative: str) -> list[dict]:
    raw = path.read_bytes()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    content, encoding, bom, newline = _pbs_format(raw)
    rows = []
    section = "GLOBAL"
    occurrence: Counter[tuple[str, str]] = Counter()
    for line_number, raw_line in enumerate(content.splitlines(keepends=True), start=1):
        body, line_ending = _split_pbs_line(raw_line)
        line = body.strip()
        if not line or line.startswith("#"):
            continue
        section_match = re.match(r"^\[([^\]]+)\]", line)
        if section_match:
            section = section_match.group(1).strip()
            continue
        assignment = _pbs_assignment_parts(body)
        if assignment is None:
            continue
        _prefix, key, exact_value, _trailing = assignment
        value = exact_value.strip()
        if key == "Point" and Path(relative).name.casefold() == "town_map.txt":
            occurrence[(section, key)] += 1
            sub_index = occurrence[(section, key)]
            point_layout = _strict_point_layout(exact_value)
            fields = (
                [field["core"] for field in point_layout["fields"]]
                if point_layout is not None
                else _point_fields(value)
            )
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
                    "pbs_value_sha256": _pbs_value_sha256(exact_value, encoding),
                    "pbs_line_number": line_number,
                    "pbs_field_count": len(fields),
                    "pbs_point_structure": build_pbs_point_structure_proof(
                        body=body,
                        line_ending=line_ending,
                        encoding=encoding,
                        section=section,
                        key_occurrence=sub_index,
                        line_number=line_number,
                        field_index=field_index,
                        file_sha256=file_sha256,
                    ),
                })
            continue
        if not is_translatable_pbs_key(key, relative) or not looks_visible(value):
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
            "pbs_line_number": line_number,
            "pbs_field_count": "",
            "pbs_point_structure": "",
        })
    return rows


def _bind_compiled_point_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie chaque sous-champ Point extrait à son emplacement Marshal exact."""
    point_rows = [
        row
        for row in rows
        if str(row.get("type") or "").startswith("PBS v21.1 — Point.")
        and str(row.get("fichier") or "").replace("\\", "/").casefold()
        == "pbs/town_map.txt"
    ]
    if not point_rows:
        return
    by_kind = {source.kind: source for source in inventory.sources}
    compiled_source = by_kind.get("compiled_town_map")
    pbs_source = next(
        (
            source
            for source in inventory.sources
            if source.relative_path.casefold() == "pbs/town_map.txt"
        ),
        None,
    )
    if compiled_source is None or pbs_source is None:
        raise ExtractionIntegrityError(
            "Les Point PBS ne peuvent pas être liés à Data/town_map.dat."
        )
    compiled_path = snapshot_root.joinpath(*Path(compiled_source.relative_path).parts)
    pbs_path = snapshot_root.joinpath(*Path(pbs_source.relative_path).parts)
    compiled_raw = compiled_path.read_bytes()
    pbs_raw = pbs_path.read_bytes()
    content, _encoding, _bom, _newline = _pbs_format(pbs_raw)
    lines = content.splitlines(keepends=True)
    sections: dict[int, dict[str, object]] = {}
    current_section: int | None = None
    try:
        for raw_line in lines:
            body, _line_ending = _split_pbs_line(raw_line)
            stripped = body.strip()
            if not stripped or stripped.startswith("#"):
                continue
            section_match = re.fullmatch(r"\[([^\]]+)\]", stripped)
            if section_match:
                section_text = section_match.group(1)
                if not re.fullmatch(r"0|[1-9]\d*", section_text):
                    raise ValueError("identifiant de section non canonique")
                current_section = int(section_text)
                if current_section in sections:
                    raise ValueError("section dupliquée")
                sections[current_section] = {"Name": None, "Filename": None, "Point": 0}
                continue
            assignment = _pbs_assignment_parts(body)
            if assignment is None or current_section is None:
                continue
            _prefix, key, exact_value, _trailing = assignment
            if key in {"Name", "Filename"}:
                if sections[current_section][key] is not None:
                    raise ValueError(f"{key} dupliqué")
                if not exact_value or exact_value != exact_value.strip():
                    raise ValueError(f"{key} ambigu")
                sections[current_section][key] = exact_value
            elif key == "Point":
                sections[current_section]["Point"] = (
                    int(sections[current_section]["Point"]) + 1
                )
        normalized_sections = {}
        for section_id, metadata in sections.items():
            name = metadata["Name"]
            filename = metadata["Filename"]
            if not isinstance(name, str) or not isinstance(filename, str):
                raise ValueError("métadonnées de section absentes")
            normalized_sections[section_id] = (
                name,
                filename,
                int(metadata["Point"]),
            )
        validate_compiled_town_map_sections(
            compiled_raw,
            pbs_sections=normalized_sections,
        )
    except (TypeError, ValueError, TownMapIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "Les sections PBS et compilées de TownMap sont incohérentes."
        ) from exc
    for row in point_rows:
        try:
            line_number = int(row["pbs_line_number"])
            field_index = int(row["pbs_field_index"])
            occurrence = int(str(row["sous_index"]).split(":", 1)[0])
            if not (1 <= line_number <= len(lines)):
                raise ValueError("ligne Point absente")
            body, _line_ending = _split_pbs_line(lines[line_number - 1])
            assignment = _pbs_assignment_parts(body)
            if assignment is None or assignment[1] != "Point":
                raise ValueError("affectation Point absente")
            layout = _strict_point_layout(assignment[2])
            if layout is None:
                raise ValueError("Point cité ou ambigu")
            fields = [str(field["core"]) for field in layout["fields"]]
            proof_json = build_compiled_point_proof(
                compiled_raw,
                section=str(row["evenement_id"]),
                occurrence=occurrence,
                field_index=field_index,
                pbs_fields=fields,
            )
            proof = json.loads(proof_json)
        except (KeyError, TypeError, ValueError, TownMapIntegrityError) as exc:
            raise ExtractionIntegrityError(
                "La correspondance entre un Point PBS et Data/town_map.dat est ambiguë."
            ) from exc
        row["pbs_compiled_file"] = compiled_source.relative_path
        row["pbs_compiled_sha256"] = compiled_source.sha256
        row["pbs_compiled_path"] = json.dumps(
            proof["compiled_path"], ensure_ascii=True, separators=(",", ":")
        )
        row["pbs_compiled_structure"] = proof_json


def _bind_compiled_phone_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie les messages phone.txt aux deux représentations chargées par v21.1."""
    phone_rows = [
        row
        for row in rows
        if str(row.get("fichier") or "").replace("\\", "/").casefold()
        == "pbs/phone.txt"
        and str(row.get("type") or "").startswith("PBS — ")
    ]
    if not phone_rows:
        return
    by_relative = {
        source.relative_path.replace("\\", "/").casefold(): source
        for source in inventory.sources
    }
    pbs_source = by_relative.get("pbs/phone.txt")
    compiled_source = by_relative.get(COMPILED_PHONE_FILE.casefold())
    runtime_source = by_relative.get(PHONE_MESSAGES_FILE.casefold())
    if pbs_source is None or compiled_source is None or runtime_source is None:
        raise ExtractionIntegrityError(
            "Les messages téléphone ne peuvent pas être reliés à leurs deux "
            "représentations compilées v21.1."
        )

    def snapshot_bytes(source: ExtractionSource) -> bytes:
        return snapshot_root.joinpath(*Path(source.relative_path).parts).read_bytes()

    try:
        proofs = build_phone_entry_proofs(
            snapshot_bytes(pbs_source),
            snapshot_bytes(compiled_source),
            snapshot_bytes(runtime_source),
        )
        for row in phone_rows:
            lookup = (
                str(row.get("evenement_id") or ""),
                str(row.get("commande") or ""),
                int(row.get("sous_index") or 0),
            )
            proof = proofs.pop(lookup)
            if proof.source != str(row.get("texte_source") or ""):
                raise PhoneIntegrityError(
                    "Le texte PBS ne correspond pas à la preuve téléphone."
                )
            row["pbs_structure"] = proof.pbs_structure
            row["pbs_compiled_file"] = compiled_source.relative_path
            row["pbs_compiled_sha256"] = compiled_source.sha256
            row["pbs_compiled_path"] = proof.compiled_path
            row["pbs_compiled_structure"] = proof.compiled_structure
            row["pbs_runtime_file"] = runtime_source.relative_path
            row["pbs_runtime_sha256"] = runtime_source.sha256
            row["pbs_runtime_path"] = proof.runtime_path
            row["pbs_runtime_structure"] = proof.runtime_structure
        if proofs:
            raise PhoneIntegrityError(
                "Certaines occurrences téléphone compilées ne sont pas extraites."
            )
    except (KeyError, TypeError, ValueError, OSError, PhoneIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "La correspondance PBS/phone.dat/PHONE_MESSAGES est ambiguë ou incohérente."
        ) from exc


def _bind_compiled_trainer_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie chaque LoseText aux représentations compilée et exécutée v21.1."""
    trainer_rows = [
        row
        for row in rows
        if str(row.get("fichier") or "").replace("\\", "/").casefold()
        == TRAINER_PBS_FILE.casefold()
        and row.get("type") == "PBS — LoseText"
    ]
    if not trainer_rows:
        return
    by_relative = {
        source.relative_path.replace("\\", "/").casefold(): source
        for source in inventory.sources
    }
    pbs_source = by_relative.get(TRAINER_PBS_FILE.casefold())
    compiled_source = by_relative.get(COMPILED_TRAINER_FILE.casefold())
    runtime_source = by_relative.get(TRAINER_MESSAGES_FILE.casefold())
    if pbs_source is None or compiled_source is None or runtime_source is None:
        raise ExtractionIntegrityError(
            "Les LoseText ne peuvent pas être reliés à trainers.dat et à leur "
            "banque d'exécution v21.1."
        )

    def snapshot_bytes(source: ExtractionSource) -> bytes:
        return snapshot_root.joinpath(*Path(source.relative_path).parts).read_bytes()

    try:
        proofs = build_trainer_entry_proofs(
            snapshot_bytes(pbs_source),
            snapshot_bytes(compiled_source),
            snapshot_bytes(runtime_source),
        )
        for row in trainer_rows:
            lookup = (
                str(row.get("evenement_id") or ""),
                str(row.get("commande") or ""),
                int(row.get("sous_index") or 0),
            )
            proof = proofs.pop(lookup)
            if proof.source != str(row.get("texte_source") or ""):
                raise TrainerIntegrityError(
                    "Le texte PBS ne correspond pas à la preuve LoseText."
                )
            row["pbs_structure"] = proof.pbs_structure
            row["pbs_compiled_file"] = compiled_source.relative_path
            row["pbs_compiled_sha256"] = compiled_source.sha256
            row["pbs_compiled_path"] = proof.compiled_path
            row["pbs_compiled_structure"] = proof.compiled_structure
            row["pbs_runtime_file"] = runtime_source.relative_path
            row["pbs_runtime_sha256"] = runtime_source.sha256
            row["pbs_runtime_path"] = proof.runtime_path
            row["pbs_runtime_structure"] = proof.runtime_structure
        if any(looks_visible(proof.source) for proof in proofs.values()):
            raise TrainerIntegrityError(
                "Certaines occurrences LoseText visibles compilées ne sont pas extraites."
            )
    except (KeyError, TypeError, ValueError, OSError, TrainerIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "La correspondance PBS/trainers.dat/TRAINER_SPEECHES_LOSE est "
            "ambiguë ou incohérente."
        ) from exc


def _bind_compiled_ability_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie les descriptions de capacités aux deux représentations v21.1."""
    ability_rows = [
        row
        for row in rows
        if str(row.get("fichier") or "").replace("\\", "/").casefold()
        == ABILITY_PBS_FILE.casefold()
        and row.get("type") == "PBS — Description"
    ]
    if not ability_rows:
        return
    by_relative = {
        source.relative_path.replace("\\", "/").casefold(): source
        for source in inventory.sources
    }
    pbs_source = by_relative.get(ABILITY_PBS_FILE.casefold())
    compiled_source = by_relative.get(COMPILED_ABILITY_FILE.casefold())
    runtime_source = by_relative.get(ABILITY_MESSAGES_FILE.casefold())
    if pbs_source is None or compiled_source is None or runtime_source is None:
        raise ExtractionIntegrityError(
            "Les descriptions de capacités ne peuvent pas être reliées à "
            "abilities.dat et à leur banque core v21.1."
        )

    def snapshot_bytes(source: ExtractionSource) -> bytes:
        return snapshot_root.joinpath(*Path(source.relative_path).parts).read_bytes()

    try:
        proofs = build_ability_description_proofs(
            snapshot_bytes(pbs_source),
            snapshot_bytes(compiled_source),
            snapshot_bytes(runtime_source),
        )
        for row in ability_rows:
            lookup = (
                str(row.get("evenement_id") or ""),
                str(row.get("commande") or ""),
                int(row.get("sous_index") or 0),
            )
            proof = proofs.pop(lookup)
            if proof.source != str(row.get("texte_source") or ""):
                raise AbilityIntegrityError(
                    "Le texte PBS ne correspond pas à la preuve de capacité."
                )
            row["pbs_structure"] = proof.pbs_structure
            row["pbs_compiled_file"] = compiled_source.relative_path
            row["pbs_compiled_sha256"] = compiled_source.sha256
            row["pbs_compiled_path"] = proof.compiled_path
            row["pbs_compiled_structure"] = proof.compiled_structure
            row["pbs_runtime_file"] = runtime_source.relative_path
            row["pbs_runtime_sha256"] = runtime_source.sha256
            row["pbs_runtime_path"] = proof.runtime_path
            row["pbs_runtime_structure"] = proof.runtime_structure
        if any(looks_visible(proof.source) for proof in proofs.values()):
            raise AbilityIntegrityError(
                "Certaines descriptions de capacités visibles ne sont pas extraites."
            )
    except (KeyError, TypeError, ValueError, OSError, AbilityIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "La correspondance PBS/abilities.dat/ABILITY_DESCRIPTIONS est "
            "ambiguë ou incohérente."
        ) from exc


def _bind_compiled_species_pokedex_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie les Pokédex de base au registre espèces et à la banque v21.1."""
    species_rows = [
        row
        for row in rows
        if str(row.get("fichier") or "").replace("\\", "/").casefold()
        == SPECIES_PBS_FILE.casefold()
        and row.get("type") == "PBS — Pokedex"
    ]
    if not species_rows:
        return
    by_relative = {
        source.relative_path.replace("\\", "/").casefold(): source
        for source in inventory.sources
    }
    pbs_source = by_relative.get(SPECIES_PBS_FILE.casefold())
    forms_source = by_relative.get(SPECIES_FORMS_PBS_FILE.casefold())
    compiled_source = by_relative.get(COMPILED_SPECIES_FILE.casefold())
    runtime_source = by_relative.get(SPECIES_MESSAGES_FILE.casefold())
    if (
        pbs_source is None
        or forms_source is None
        or compiled_source is None
        or runtime_source is None
    ):
        raise ExtractionIntegrityError(
            "Les entrées Pokédex ne peuvent pas être reliées aux deux PBS, à "
            "species.dat et à leur banque core v21.1."
        )

    def snapshot_bytes(source: ExtractionSource) -> bytes:
        return snapshot_root.joinpath(*Path(source.relative_path).parts).read_bytes()

    try:
        proofs = build_species_pokedex_proofs(
            snapshot_bytes(pbs_source),
            snapshot_bytes(forms_source),
            snapshot_bytes(compiled_source),
            snapshot_bytes(runtime_source),
        )
        for row in species_rows:
            lookup = (
                str(row.get("evenement_id") or ""),
                str(row.get("commande") or ""),
                int(row.get("sous_index") or 0),
            )
            proof = proofs.pop(lookup)
            if proof.source != str(row.get("texte_source") or ""):
                raise SpeciesIntegrityError(
                    "Le texte PBS ne correspond pas à la preuve Pokédex."
                )
            row["pbs_structure"] = proof.pbs_structure
            row["pbs_compiled_file"] = compiled_source.relative_path
            row["pbs_compiled_sha256"] = compiled_source.sha256
            row["pbs_compiled_path"] = proof.compiled_path
            row["pbs_compiled_structure"] = proof.compiled_structure
            row["pbs_runtime_file"] = runtime_source.relative_path
            row["pbs_runtime_sha256"] = runtime_source.sha256
            row["pbs_runtime_path"] = proof.runtime_path
            row["pbs_runtime_structure"] = proof.runtime_structure
        if any(looks_visible(proof.source) for proof in proofs.values()):
            raise SpeciesIntegrityError(
                "Certaines entrées Pokédex de base visibles ne sont pas extraites."
            )
    except (KeyError, TypeError, ValueError, OSError, SpeciesIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "La correspondance pokemon/pokemon_forms/species.dat/POKEDEX_ENTRIES "
            "est ambiguë ou incohérente."
        ) from exc


def _bind_compiled_map_metadata_name_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie les noms de cartes à leur registre et à MAP_NAMES v21.1."""
    metadata_rows = [
        row
        for row in rows
        if str(row.get("fichier") or "").replace("\\", "/").casefold()
        == MAP_METADATA_PBS_FILE.casefold()
        and row.get("type") == "PBS — Name"
    ]
    if not metadata_rows:
        return
    by_relative = {
        source.relative_path.replace("\\", "/").casefold(): source
        for source in inventory.sources
    }
    pbs_source = by_relative.get(MAP_METADATA_PBS_FILE.casefold())
    compiled_source = by_relative.get(COMPILED_MAP_METADATA_FILE.casefold())
    runtime_source = by_relative.get(MAP_METADATA_MESSAGES_FILE.casefold())
    if pbs_source is None or compiled_source is None or runtime_source is None:
        raise ExtractionIntegrityError(
            "Les noms de cartes ne peuvent pas être reliés à map_metadata.dat "
            "et à MAP_NAMES v21.1."
        )

    def snapshot_bytes(source: ExtractionSource) -> bytes:
        return snapshot_root.joinpath(*Path(source.relative_path).parts).read_bytes()

    try:
        proofs = build_map_metadata_name_proofs(
            snapshot_bytes(pbs_source),
            snapshot_bytes(compiled_source),
            snapshot_bytes(runtime_source),
        )
        for row in metadata_rows:
            lookup = (
                str(row.get("evenement_id") or ""),
                str(row.get("commande") or ""),
                int(row.get("sous_index") or 0),
            )
            proof = proofs.pop(lookup)
            if proof.source != str(row.get("texte_source") or ""):
                raise MapMetadataIntegrityError(
                    "Le texte PBS ne correspond pas à la preuve de nom de carte."
                )
            row["pbs_structure"] = proof.pbs_structure
            row["pbs_compiled_file"] = compiled_source.relative_path
            row["pbs_compiled_sha256"] = compiled_source.sha256
            row["pbs_compiled_path"] = proof.compiled_path
            row["pbs_compiled_structure"] = proof.compiled_structure
            row["pbs_runtime_file"] = runtime_source.relative_path
            row["pbs_runtime_sha256"] = runtime_source.sha256
            row["pbs_runtime_path"] = proof.runtime_path
            row["pbs_runtime_structure"] = proof.runtime_structure
        if any(looks_visible(proof.source) for proof in proofs.values()):
            raise MapMetadataIntegrityError(
                "Certains noms de cartes visibles ne sont pas extraits."
            )
    except (KeyError, TypeError, ValueError, OSError, MapMetadataIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "La correspondance PBS/map_metadata.dat/MAP_NAMES est ambiguë ou incohérente."
        ) from exc


def _bind_compiled_move_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie Name/Description aux objets Move et aux banques v21.1.

    Cette liaison garantit aussi que ``Category`` reste une donnee technique :
    seules les deux commandes explicitement textuelles sont acceptees et
    chacune doit avoir une preuve compilee.
    """
    move_rows = [
        row
        for row in rows
        if str(row.get("fichier") or "").replace("\\", "/").casefold()
        == MOVE_PBS_FILE.casefold()
        and row.get("commande") in {"Name", "Description"}
    ]
    if not move_rows:
        return
    by_relative = {
        source.relative_path.replace("\\", "/").casefold(): source
        for source in inventory.sources
    }
    pbs_source = by_relative.get(MOVE_PBS_FILE.casefold())
    compiled_source = by_relative.get(COMPILED_MOVE_FILE.casefold())
    runtime_source = by_relative.get(MOVE_MESSAGES_FILE.casefold())
    if pbs_source is None or compiled_source is None or runtime_source is None:
        raise ExtractionIntegrityError(
            "Les textes de Moves ne peuvent pas \u00eatre reli\u00e9s \u00e0 moves.dat "
            "et aux banques MOVE_NAMES/MOVE_DESCRIPTIONS v21.1."
        )

    def snapshot_bytes(source: ExtractionSource) -> bytes:
        return snapshot_root.joinpath(*Path(source.relative_path).parts).read_bytes()

    try:
        proofs = build_move_text_proofs(
            snapshot_bytes(pbs_source),
            snapshot_bytes(compiled_source),
            snapshot_bytes(runtime_source),
        )
        for row in move_rows:
            lookup = (
                str(row.get("evenement_id") or ""),
                str(row.get("commande") or ""),
                int(row.get("sous_index") or 0),
            )
            proof = proofs.pop(lookup)
            if proof.source != str(row.get("texte_source") or ""):
                raise MoveIntegrityError(
                    "Le texte PBS ne correspond pas \u00e0 la preuve de Move."
                )
            row["pbs_structure"] = proof.pbs_structure
            row["pbs_compiled_file"] = compiled_source.relative_path
            row["pbs_compiled_sha256"] = compiled_source.sha256
            row["pbs_compiled_path"] = proof.compiled_path
            row["pbs_compiled_structure"] = proof.compiled_structure
            row["pbs_runtime_file"] = runtime_source.relative_path
            row["pbs_runtime_sha256"] = runtime_source.sha256
            row["pbs_runtime_path"] = proof.runtime_path
            row["pbs_runtime_structure"] = proof.runtime_structure
        if any(looks_visible(proof.source) for proof in proofs.values()):
            raise MoveIntegrityError(
                "Certains textes de Moves visibles ne sont pas extraits."
            )
    except (KeyError, TypeError, ValueError, OSError, MoveIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "La correspondance PBS/moves.dat/banques Move est ambigu\u00eb ou incoh\u00e9rente."
        ) from exc


def _bind_compiled_item_proofs(
    snapshot_root: Path,
    inventory: ExtractionInventory,
    rows: list[dict],
) -> None:
    """Lie les cinq champs textuels Item aux deux représentations compilées.

    Les champs techniques restent exclus par la liste explicite. Une ancienne
    clé de banque qui ne correspond plus exactement à la source PBS laisse la
    ligne extractible, mais sans preuve de reconstruction privée.
    """
    item_rows = [
        row
        for row in rows
        if str(row.get("fichier") or "").replace("\\", "/").casefold()
        == ITEM_PBS_FILE.casefold()
        and row.get("commande") in ITEM_TEXT_FIELDS
    ]
    if not item_rows:
        return
    by_relative = {
        source.relative_path.replace("\\", "/").casefold(): source
        for source in inventory.sources
    }
    pbs_source = by_relative.get(ITEM_PBS_FILE.casefold())
    compiled_source = by_relative.get(COMPILED_ITEM_FILE.casefold())
    runtime_source = by_relative.get(ITEM_MESSAGES_FILE.casefold())
    if pbs_source is None or compiled_source is None or runtime_source is None:
        raise ExtractionIntegrityError(
            "Les textes d'Items ne peuvent pas être reliés à items.dat et aux "
            "banques Item v21.1."
        )

    def snapshot_bytes(source: ExtractionSource) -> bytes:
        return snapshot_root.joinpath(*Path(source.relative_path).parts).read_bytes()

    try:
        proofs = build_item_text_proofs(
            snapshot_bytes(pbs_source),
            snapshot_bytes(compiled_source),
            snapshot_bytes(runtime_source),
        )
        for row in item_rows:
            lookup = (
                str(row.get("evenement_id") or ""),
                str(row.get("commande") or ""),
                int(row.get("sous_index") or 0),
            )
            proof = proofs.pop(lookup, None)
            if proof is None:
                continue
            if proof.source != str(row.get("texte_source") or ""):
                raise ItemIntegrityError(
                    "Le texte PBS ne correspond pas à la preuve d'Item."
                )
            row["pbs_structure"] = proof.pbs_structure
            row["pbs_compiled_file"] = compiled_source.relative_path
            row["pbs_compiled_sha256"] = compiled_source.sha256
            row["pbs_compiled_path"] = proof.compiled_path
            row["pbs_compiled_structure"] = proof.compiled_structure
            row["pbs_runtime_file"] = runtime_source.relative_path
            row["pbs_runtime_sha256"] = runtime_source.sha256
            row["pbs_runtime_path"] = proof.runtime_path
            row["pbs_runtime_structure"] = proof.runtime_structure
        if any(looks_visible(proof.source) for proof in proofs.values()):
            raise ItemIntegrityError(
                "Certains textes d'Items prouvés ne sont pas extraits."
            )
    except (KeyError, TypeError, ValueError, OSError, ItemIntegrityError) as exc:
        raise ExtractionIntegrityError(
            "La correspondance PBS/items.dat/banques Item est ambiguë ou incohérente."
        ) from exc


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
    essentials_profile: str = "",
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
                "pbs_line_number",
                "pbs_field_count",
                "pbs_point_structure",
                "pbs_structure",
                "pbs_compiled_file",
                "pbs_compiled_sha256",
                "pbs_compiled_path",
                "pbs_compiled_structure",
                "pbs_runtime_file",
                "pbs_runtime_sha256",
                "pbs_runtime_path",
                "pbs_runtime_structure",
            ):
                row.setdefault(field_name, "")
            row["adaptateur"] = "pokemon_essentials"
            row["source_sha256"] = records[source.relative_path].sha256
            row["source_manifest_sha256"] = inventory.source_manifest_sha256
        rows.extend(extracted)
        if progress:
            progress(index, total, source.relative_path)

    _bind_compiled_point_proofs(snapshot_root, inventory, rows)
    _bind_compiled_phone_proofs(snapshot_root, inventory, rows)
    if essentials_profile == "essentials_v21_1_readonly":
        _bind_compiled_trainer_proofs(snapshot_root, inventory, rows)
        _bind_compiled_ability_proofs(snapshot_root, inventory, rows)
        _bind_compiled_species_pokedex_proofs(snapshot_root, inventory, rows)
        _bind_compiled_map_metadata_name_proofs(snapshot_root, inventory, rows)
        _bind_compiled_move_proofs(snapshot_root, inventory, rows)
        _bind_compiled_item_proofs(snapshot_root, inventory, rows)

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
    *,
    essentials_profile: str = "",
) -> StructuredExtractionResult:
    del logger  # Les erreurs sont bloquantes et remontées sans résultat partiel.
    before = build_extraction_inventory(root)
    try:
        with tempfile.TemporaryDirectory(prefix="pft_essentials_extraction_") as temp_dir:
            snapshot_root = Path(temp_dir) / "snapshot"
            snapshot_root.mkdir()
            _snapshot_sources(before, snapshot_root)
            rows = _extract_snapshot(
                snapshot_root,
                before,
                essentials_profile=essentials_profile,
                progress=progress,
            )
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
    "pbs_value_sha256", "pbs_line_number", "pbs_field_count",
    "pbs_point_structure", "pbs_structure", "pbs_compiled_file", "pbs_compiled_sha256",
    "pbs_compiled_path", "pbs_compiled_structure", "pbs_runtime_file",
    "pbs_runtime_sha256", "pbs_runtime_path", "pbs_runtime_structure",
    "adaptateur", "source_sha256",
    "source_manifest_sha256", "profil_essentials",
    "version_essentials_declaree", "methode_version_essentials",
]


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
