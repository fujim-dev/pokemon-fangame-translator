# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extraction déterministe des textes Flux vers le schéma CSV commun."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from flux_archive import FluxArchiveError, FluxArchiveReader
from ruby_marshal_reader import RubyObject, RubyString, RubyUserDefined, load
from structured_extractor import codes, looks_visible, text_value


ProgressCallback = Callable[[int, int, str], None]
MAP_NAME_RE = re.compile(r"Map\d{3,4}\.rxdata", re.I)

FLUX_EXTRA_FIELDS = [
    "adaptateur",
    "conteneur",
    "source_flux",
    "chemin_structurel",
    "empreinte_source",
    "empreinte_texte_csv",
    "empreinte_valeur_actuelle",
]


class FluxExtractionError(RuntimeError):
    """Extraction Flux impossible sans perdre la fidélité structurelle."""


@dataclass(frozen=True)
class FluxTextOccurrence:
    source_kind: str
    internal_path: str
    structural_path: tuple[object, ...]
    text: str
    raw_parts: tuple[bytes, ...]
    row_type: str
    current_raw: bytes | None = None
    map_id: str = ""
    map_name: str = ""
    event_id: str = ""
    event_name: str = ""
    page: str = ""
    command: str = ""
    sub_index: str = ""

    @property
    def structural_path_text(self) -> str:
        return json.dumps(self.structural_path, ensure_ascii=False, separators=(",", ":"))

    @property
    def source_hash(self) -> str:
        digest = hashlib.sha256()
        for raw in self.raw_parts:
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
        return digest.hexdigest()

    @property
    def stable_id(self) -> str:
        payload = "\x1f".join(
            (
                "pokemon_flux",
                self.source_kind,
                self.internal_path,
                self.structural_path_text,
                self.source_hash,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_row(self) -> dict[str, str]:
        current_hash = hashlib.sha256(self.current_raw).hexdigest() if self.current_raw is not None else ""
        return {
            "id_stable": self.stable_id,
            "type": self.row_type,
            "fichier": self.internal_path,
            "carte_id": self.map_id,
            "carte_nom": self.map_name,
            "evenement_id": self.event_id,
            "evenement_nom": self.event_name,
            "page": self.page,
            "commande": self.command,
            "sous_index": self.sub_index,
            "texte_source": self.text,
            "traduction_fr": "",
            "codes_proteges": codes(self.text),
            "statut": "À traduire",
            "adaptateur": "pokemon_flux",
            "conteneur": "Data/Data_0.fpk",
            "source_flux": self.source_kind,
            "chemin_structurel": self.structural_path_text,
            "empreinte_source": self.source_hash,
            "empreinte_texte_csv": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            "empreinte_valeur_actuelle": current_hash,
        }


def _ruby_raw(value) -> bytes | None:
    return value.data if isinstance(value, RubyString) else None


def _ruby_visible(value) -> tuple[str, bytes] | None:
    if not isinstance(value, RubyString):
        return None
    text = value.text()
    if not looks_visible(text):
        return None
    return text, value.data


def _ruby_text_part(value) -> tuple[str, bytes] | None:
    if not isinstance(value, RubyString):
        return None
    return value.text(), value.data


def _walk_marshaled_strings(value, path=(), seen: set[int] | None = None):
    """Retourne une fois chaque nœud chaîne et son premier chemin structurel."""
    if seen is None:
        seen = set()
    if isinstance(value, RubyString):
        identity = id(value)
        if identity not in seen:
            seen.add(identity)
            yield path, value
        return
    if isinstance(value, (list, dict, RubyObject, RubyUserDefined)):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_marshaled_strings(child, path + ("list", index), seen)
    elif isinstance(value, dict):
        for index, (key, child) in enumerate(value.items()):
            if key == "__default__":
                continue
            yield from _walk_marshaled_strings(key, path + ("dict", index, "key"), seen)
            yield from _walk_marshaled_strings(child, path + ("dict", index, "value"), seen)
    elif isinstance(value, RubyObject):
        for key, child in value.ivars.items():
            yield from _walk_marshaled_strings(child, path + ("ivar", str(key)), seen)
    elif isinstance(value, RubyUserDefined):
        for key, child in value.ivars.items():
            yield from _walk_marshaled_strings(child, path + ("ivar", str(key)), seen)


def _message_game_occurrences(root) -> list[FluxTextOccurrence]:
    occurrences: list[FluxTextOccurrence] = []
    seen_containers: set[int] = set()

    def walk(value, path=()):
        if isinstance(value, (list, dict, RubyObject, RubyUserDefined)):
            identity = id(value)
            if identity in seen_containers:
                return
            seen_containers.add(identity)
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + ("list", index))
        elif isinstance(value, dict):
            for index, (key, child) in enumerate(value.items()):
                if key == "__default__":
                    continue
                visible = _ruby_visible(key)
                if visible is not None:
                    text, raw = visible
                    occurrences.append(
                        FluxTextOccurrence(
                            source_kind="messages_game",
                            internal_path="Data/messages_game.dat",
                            structural_path=path + ("dict", index, "key"),
                            text=text,
                            raw_parts=(raw,),
                            row_type="Banque de messages",
                            current_raw=_ruby_raw(child),
                            event_name="/".join(map(str, path + ("dict", index))),
                        )
                    )
                walk(child, path + ("dict", index, "value"))
        elif isinstance(value, RubyObject):
            for key, child in value.ivars.items():
                walk(child, path + ("ivar", str(key)))
        elif isinstance(value, RubyUserDefined):
            for key, child in value.ivars.items():
                walk(child, path + ("ivar", str(key)))

    walk(root)
    return occurrences


def _event_occurrences(
    commands: list,
    *,
    source_kind: str,
    internal_path: str,
    base_path: tuple[object, ...],
    map_id: str,
    map_name: str,
    event_id: str,
    event_name: str,
    page: str,
) -> list[FluxTextOccurrence]:
    result: list[FluxTextOccurrence] = []
    index = 0
    while index < len(commands):
        command = commands[index]
        if not isinstance(command, RubyObject):
            index += 1
            continue
        code = command.ivars.get("@code")
        params = command.ivars.get("@parameters", [])
        if code == 101:
            parts: list[tuple[str, bytes]] = []
            if isinstance(params, list) and params:
                part = _ruby_text_part(params[0])
                if part is not None:
                    parts.append(part)
            cursor = index + 1
            while cursor < len(commands):
                continuation = commands[cursor]
                if not isinstance(continuation, RubyObject) or continuation.ivars.get("@code") != 401:
                    break
                continuation_params = continuation.ivars.get("@parameters", [])
                if isinstance(continuation_params, list) and continuation_params:
                    part = _ruby_text_part(continuation_params[0])
                    if part is not None:
                        parts.append(part)
                cursor += 1
            combined_text = "\\n".join(text for text, _raw in parts)
            if parts and looks_visible(combined_text):
                result.append(
                    FluxTextOccurrence(
                        source_kind=source_kind,
                        internal_path=internal_path,
                        structural_path=base_path + ("commands", index, "dialogue", len(parts)),
                        text=combined_text,
                        raw_parts=tuple(raw for _text, raw in parts),
                        row_type="Dialogue",
                        map_id=map_id,
                        map_name=map_name,
                        event_id=event_id,
                        event_name=event_name,
                        page=page,
                        command=str(index),
                        sub_index=f"lignes:{len(parts)}",
                    )
                )
            index = cursor
            continue
        if code == 102 and isinstance(params, list) and params and isinstance(params[0], list):
            for choice_index, choice in enumerate(params[0]):
                visible = _ruby_visible(choice)
                if visible is None:
                    continue
                text, raw = visible
                result.append(
                    FluxTextOccurrence(
                        source_kind=source_kind,
                        internal_path=internal_path,
                        structural_path=base_path + ("commands", index, "choice", choice_index),
                        text=text,
                        raw_parts=(raw,),
                        row_type="Choix",
                        map_id=map_id,
                        map_name=map_name,
                        event_id=event_id,
                        event_name=event_name,
                        page=page,
                        command=str(index),
                        sub_index=str(choice_index),
                    )
                )
        index += 1
    return result


def _map_occurrences(path: Path, extracted_root: Path, loaded, map_names: dict[int, str]) -> list[FluxTextOccurrence]:
    if not isinstance(loaded, RubyObject) or loaded.class_name != "RPG::Map":
        raise FluxExtractionError(f"Carte Flux non reconnue : {path.name}")
    match = re.fullmatch(r"Map(\d{3,4})\.rxdata", path.name, re.I)
    map_id_value = int(match.group(1)) if match else 0
    internal_path = path.relative_to(extracted_root).as_posix()
    result: list[FluxTextOccurrence] = []
    events = loaded.ivars.get("@events", {})
    if not isinstance(events, dict):
        raise FluxExtractionError(f"Table d'événements invalide : {path.name}")
    for event_id in sorted(key for key in events if isinstance(key, int)):
        event = events[event_id]
        if not isinstance(event, RubyObject):
            continue
        event_name = text_value(event.ivars.get("@name")) or f"Événement {event_id}"
        pages = event.ivars.get("@pages", [])
        if not isinstance(pages, list):
            continue
        for page_index, page in enumerate(pages):
            if not isinstance(page, RubyObject):
                continue
            commands = page.ivars.get("@list", [])
            if not isinstance(commands, list):
                continue
            result.extend(
                _event_occurrences(
                    commands,
                    source_kind="map_events",
                    internal_path=internal_path,
                    base_path=("events", event_id, "pages", page_index),
                    map_id=str(map_id_value),
                    map_name=map_names.get(map_id_value, ""),
                    event_id=str(event_id),
                    event_name=event_name,
                    page=str(page_index + 1),
                )
            )
    return result


def _common_event_occurrences(path: Path, extracted_root: Path, loaded) -> list[FluxTextOccurrence]:
    if not isinstance(loaded, list):
        raise FluxExtractionError("CommonEvents.rxdata n'est pas une liste Ruby reconnue.")
    internal_path = path.relative_to(extracted_root).as_posix()
    result: list[FluxTextOccurrence] = []
    for event_index, event in enumerate(loaded):
        if not isinstance(event, RubyObject):
            continue
        commands = event.ivars.get("@list", [])
        if not isinstance(commands, list):
            continue
        event_name = text_value(event.ivars.get("@name")) or f"Événement commun {event_index}"
        result.extend(
            _event_occurrences(
                commands,
                source_kind="common_events",
                internal_path=internal_path,
                base_path=("common_events", event_index),
                map_id="",
                map_name="Événements communs",
                event_id=str(event_index),
                event_name=event_name,
                page="1",
            )
        )
    return result


def _matched_marshaled_occurrences(
    loaded,
    *,
    source_kind: str,
    internal_path: str,
    canonical_raw: frozenset[bytes],
) -> list[FluxTextOccurrence]:
    result: list[FluxTextOccurrence] = []
    for path, value in _walk_marshaled_strings(loaded):
        if value.data not in canonical_raw:
            continue
        visible = _ruby_visible(value)
        if visible is None:
            continue
        text, raw = visible
        result.append(
            FluxTextOccurrence(
                source_kind=source_kind,
                internal_path=internal_path,
                structural_path=tuple(path),
                text=text,
                raw_parts=(raw,),
                row_type="Banque de messages",
                event_name="/".join(map(str, path)),
            )
        )
    return result


def _load_map_names(loaded) -> dict[int, str]:
    result: dict[int, str] = {}
    if not isinstance(loaded, dict):
        return result
    for key, value in loaded.items():
        if isinstance(key, int) and isinstance(value, RubyObject):
            name = text_value(value.ivars.get("@name"))
            if name:
                result[key] = name
    return result


def _fingerprint_file(path: Path) -> tuple[str, int, int]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise FluxExtractionError("Data_0.fpk a changé pendant l'extraction en lecture seule.")
    return digest.hexdigest(), after.st_size, after.st_mtime_ns


def collect_flux_occurrences(
    extracted_root: Path,
    marshal_files: Iterable[Path],
    loaded: dict[Path, object],
) -> list[FluxTextOccurrence]:
    """Collecte les seules occurrences dont le sens ou la provenance est vérifiable.

    ``messages_game.dat`` fournit le dictionnaire canonique. Les chaînes de
    ``messages.dat`` et des autres données ne sont retenues que si leurs octets
    correspondent exactement à une clé canonique. Les cartes et événements
    communs sont, eux, lus par leurs codes de commandes RPG Maker connus.
    """
    extracted_root = extracted_root.resolve()
    marshal_files = tuple(marshal_files)
    data = extracted_root / "Data"
    messages_game_path = data / "messages_game.dat"
    messages_path = data / "messages.dat"
    common_path = data / "CommonEvents.rxdata"
    required = (messages_game_path, messages_path, common_path)
    missing_required = [path.name for path in required if path not in loaded]
    if missing_required:
        raise FluxExtractionError(
            "Sources Flux essentielles illisibles : " + ", ".join(missing_required)
        )

    occurrences = _message_game_occurrences(loaded[messages_game_path])
    canonical_raw = frozenset(
        occurrence.raw_parts[0]
        for occurrence in occurrences
    )
    occurrences.extend(
        _matched_marshaled_occurrences(
            loaded[messages_path],
            source_kind="messages",
            internal_path="Data/messages.dat",
            canonical_raw=canonical_raw,
        )
    )

    map_names = _load_map_names(loaded.get(data / "MapInfos.rxdata"))
    map_paths = sorted(
        (path for path in data.glob("Map*.rxdata") if MAP_NAME_RE.fullmatch(path.name)),
        key=lambda path: path.name.casefold(),
    )
    for path in map_paths:
        if path not in loaded:
            raise FluxExtractionError(f"Carte Flux essentielle illisible : {path.name}")
        occurrences.extend(_map_occurrences(path, extracted_root, loaded[path], map_names))
    occurrences.extend(
        _common_event_occurrences(common_path, extracted_root, loaded[common_path])
    )

    excluded = {messages_game_path, messages_path, common_path, *map_paths}
    for path in marshal_files:
        if path in excluded or path not in loaded:
            continue
        relative = path.relative_to(extracted_root).as_posix()
        occurrences.extend(
            _matched_marshaled_occurrences(
                loaded[path],
                source_kind="other_data",
                internal_path=relative,
                canonical_raw=canonical_raw,
            )
        )

    occurrences.sort(
        key=lambda occurrence: (
            occurrence.source_kind,
            occurrence.internal_path.casefold(),
            occurrence.structural_path_text,
            occurrence.source_hash,
        )
    )
    return occurrences


def validate_flux_occurrences(occurrences: Iterable[FluxTextOccurrence], rows: list[dict[str, str]]) -> None:
    occurrences = list(occurrences)
    if len(occurrences) != len(rows):
        raise FluxExtractionError("Le nombre de lignes CSV diffère des occurrences Flux collectées.")
    ids: set[str] = set()
    for occurrence, row in zip(occurrences, rows):
        if occurrence.stable_id in ids:
            raise FluxExtractionError("Collision d'identifiants stables Flux.")
        ids.add(occurrence.stable_id)
        expected = occurrence.to_row()
        for field in (
            "id_stable",
            "type",
            "fichier",
            "texte_source",
            "codes_proteges",
            "source_flux",
            "chemin_structurel",
            "empreinte_source",
            "empreinte_texte_csv",
            "empreinte_valeur_actuelle",
        ):
            if row.get(field, "") != expected[field]:
                raise FluxExtractionError(f"Fidélité CSV Flux invalide pour le champ {field}.")


def extract_flux_texts(
    game_root: Path,
    *,
    archive_reader: FluxArchiveReader | None = None,
    progress: ProgressCallback | None = None,
    logger=None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Extrait les textes connus sans jamais écrire dans le dossier du jeu."""
    root = game_root.expanduser().resolve()
    candidates = [
        path
        for path in (root / "Data" / "Data_0.fpk", root / "Data_0.fpk")
        if path.is_file()
    ]
    if len(candidates) != 1:
        raise FluxExtractionError("Un unique Data_0.fpk Flux est requis.")
    fpk = candidates[0]
    before_fingerprint = _fingerprint_file(fpk)
    reader = archive_reader or FluxArchiveReader()
    inventory = reader.inspect(fpk)
    if not inventory.safe:
        raise FluxArchiveError("Extraction Flux refusée : inventaire FPK non sûr.")

    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pft_flux_extract_") as temporary:
        extracted_root = Path(temporary)
        reader.extract_to(fpk, extracted_root, inventory)
        data = extracted_root / "Data"
        marshal_files = sorted(
            (
                path
                for path in data.rglob("*")
                if path.is_file() and path.suffix.casefold() in {".rxdata", ".dat"}
            ),
            key=lambda path: path.relative_to(extracted_root).as_posix().casefold(),
        )
        loaded: dict[Path, object] = {}
        total = max(1, len(marshal_files))
        for index, path in enumerate(marshal_files, start=1):
            relative = path.relative_to(extracted_root).as_posix()
            try:
                loaded[path] = load(path)
            except Exception as exc:
                message = f"{relative}: structure Marshal non prise en charge ({type(exc).__name__})"
                errors.append(message)
                if logger:
                    logger(message)
            if progress:
                progress(index, total, relative)

        occurrences = collect_flux_occurrences(extracted_root, marshal_files, loaded)
        rows = [occurrence.to_row() for occurrence in occurrences]
        validate_flux_occurrences(occurrences, rows)

    after_fingerprint = _fingerprint_file(fpk)
    if after_fingerprint != before_fingerprint:
        raise FluxExtractionError("Le FPK original a changé pendant l'extraction.")
    return rows, errors
