# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import csv
import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from ruby_marshal_reader import RubyObject, RubyString, load, as_text

TRANSLATABLE_PBS_KEYS = {
    "Name", "NamePlural", "PortionName", "PortionNamePlural",
    "Description", "Category", "Pokedex", "FormName", "LoseText",
    "VictorySpeech", "IntroText", "EndSpeech", "Title", "DisplayName",
}

RPG_CODE_RE = re.compile(r"\\(?:[A-Za-z]+\[[^\]]*\]|pn|sh|wu|n|l|g|b|r|[.!|^><]|[0-9]+)|<[^>]+>", re.I)


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


def load_map_names(data_dir: Path) -> dict[int, str]:
    path = data_dir / "MapInfos.rxdata"
    if not path.exists():
        return {}
    root = load(path)
    names = {}
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


def extract_map(path: Path, relative: str, map_name: str) -> list[dict]:
    root = load(path)
    if not isinstance(root, RubyObject) or root.class_name != "RPG::Map":
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
            index = 0
            while index < len(commands):
                command = commands[index]
                if not isinstance(command, RubyObject):
                    index += 1
                    continue
                code = command.ivars.get("@code")
                params = command.ivars.get("@parameters", [])

                if code == 101:
                    pieces: list[str] = []
                    if isinstance(params, list) and params:
                        first = text_value(params[0])
                        if first:
                            pieces.append(first)
                    end_index = index
                    while end_index + 1 < len(commands):
                        next_cmd = commands[end_index + 1]
                        if not isinstance(next_cmd, RubyObject) or next_cmd.ivars.get("@code") != 401:
                            break
                        next_params = next_cmd.ivars.get("@parameters", [])
                        if isinstance(next_params, list) and next_params:
                            continuation = text_value(next_params[0])
                            pieces.append(continuation)
                        end_index += 1
                    message = "\\n".join(pieces).strip()
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


def extract_pbs(path: Path, relative: str) -> list[dict]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = path.read_text(encoding="cp1252")
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
        if key not in TRANSLATABLE_PBS_KEYS or not looks_visible(value):
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
        })
    return rows


def extract_structured(root: Path, progress=None, logger=None) -> tuple[list[dict], list[str]]:
    data_dir = root / "Data"
    pbs_dir = root / "PBS"
    rows: list[dict] = []
    errors: list[str] = []
    map_names = load_map_names(data_dir)

    candidates: list[tuple[str, Path]] = []
    for path in sorted(data_dir.glob("Map*.rxdata")):
        if map_id_from_path(path) is not None:
            candidates.append(("map", path))
    for name in ("messages_game.dat", "messages_core.dat"):
        path = data_dir / name
        if path.exists():
            candidates.append(("bank", path))
    for path in iter_pbs_files(pbs_dir) or []:
        candidates.append(("pbs", path))

    total = max(1, len(candidates))
    for index, (kind, path) in enumerate(candidates, start=1):
        relative = str(path.relative_to(root)).replace("\\", "/")
        try:
            if kind == "map":
                map_id = map_id_from_path(path)
                rows.extend(extract_map(path, relative, map_names.get(map_id or -1, "")))
            elif kind == "bank":
                rows.extend(extract_message_bank(path, relative))
            else:
                rows.extend(extract_pbs(path, relative))
        except Exception as exc:  # rapport plutôt que plantage global
            errors.append(f"{relative}: {type(exc).__name__}: {exc}")
            if logger:
                logger(errors[-1])
        if progress:
            progress(index, total, relative)
    return rows, errors


FIELDNAMES = [
    "id_stable", "type", "fichier", "carte_id", "carte_nom",
    "evenement_id", "evenement_nom", "page", "commande", "sous_index",
    "texte_source", "traduction_fr", "codes_proteges", "statut",
]


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
