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

from analysis.integrity import (
    IntegrityError,
    SnapshotComparison,
    compare_snapshots,
    snapshot_tree,
)
from ruby_marshal_reader import RubyObject, RubyString, load
from ruby_marshal_writer import dumps
from project_identity import ProjectIdentityError, read_project_identity
from rpg_dialogue import (
    DialogueSegmentation,
    DialogueSegmentationError,
    segment_dialogue_commands,
    split_dialogue_translation,
    validate_dialogue_command_stream,
)
from structured_extractor import (
    ExtractionIntegrityError,
    build_extraction_inventory,
    extract_common_events,
    extract_map,
    extract_message_bank,
    extract_pbs,
    is_translatable_pbs_key,
    looks_visible,
    stable_id,
    text_value,
)
from safe_io import atomic_write_bytes, atomic_write_text, read_stable_bytes
from translation_project import TranslationProjectError, open_verified_project

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
ESSENTIALS_ADAPTER_ID = "pokemon_essentials"
V21_1_VALIDATION_SCOPE = "essentials_v21_1_message_bank_candidate_v1"
V21_1_BANK_CORPUS_VALIDATION_SCOPE = "essentials_v21_1_message_bank_corpus_candidate_v1"
V21_1_MAP_VALIDATION_SCOPE = "essentials_v21_1_map_dialogue_choice_candidate_v1"
V21_1_COMMON_EVENTS_VALIDATION_SCOPE = (
    "essentials_v21_1_common_event_dialogue_corpus_candidate_v1"
)
V21_1_VALIDATION_PROFILE = "essentials_v21_1_readonly"
V21_1_VALIDATION_FILE = "Data/messages_game.dat"
V21_1_COMMON_EVENTS_FILE = "Data/CommonEvents.rxdata"
V21_1_BANK_CORPUS_FILES = frozenset(
    {"Data/messages_core.dat", "Data/messages_game.dat"}
)
V21_1_PRIVATE_VALIDATION_SCOPES = frozenset(
    {
        V21_1_VALIDATION_SCOPE,
        V21_1_BANK_CORPUS_VALIDATION_SCOPE,
        V21_1_MAP_VALIDATION_SCOPE,
        V21_1_COMMON_EVENTS_VALIDATION_SCOPE,
    }
)
RESERVED_COPY_OUTPUTS = (
    "PFT_RECONSTRUCTION_V1.0.txt",
    "LIRE_AVANT_DE_JOUER.txt",
    "LANCER_VERSION_FR.bat",
    "RECONSTRUCTION_INCOMPLETE.txt",
)


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
    rpg_command_code: str = ""
    rpg_command_indent: str = ""
    rpg_parameter_index: str = ""
    rpg_continuation_end: str = ""
    rpg_dialogue_segments: str = ""
    rpg_common_event_array_index: str = ""
    rpg_common_event_trigger: str = ""
    rpg_common_event_switch_id: str = ""
    rpg_common_event_sha256: str = ""
    rpg_choice_branch_command: str = ""
    rpg_choice_branch_parameter_index: str = ""
    decision: str = "pending"  # applicable, skipped, blocked
    reason: str = ""


@dataclass
class ReconstructionPlan:
    game_root: str
    csv_path: str
    created_at: str
    mode: str
    adapter_id: str = ""
    adapter_version: str = ""
    adapter_profile: str = ""
    validation_scope: str = ""
    csv_sha256: str = ""
    project_identity_sha256: str = ""
    project_provenance_token: str = ""
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
    integrity_valid: bool
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
    if row_type == "Événement commun — Dialogue":
        return lowered == ("data", "commonevents.rxdata")
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


def _resolve_safe_game_root(game_root: Path) -> Path:
    """Valide la racine choisie avant de perdre son identité lexicale.

    ``Path.resolve()`` suit un lien ou une jonction. Le contrôle doit donc être
    fait sur le chemin fourni par l'appelant, avant sa canonicalisation.
    """
    expanded = game_root.expanduser()
    if _is_link_or_junction(expanded):
        raise ReconstructionError(
            "Le dossier du fangame ne peut pas être un lien symbolique ou une jonction."
        )
    resolved = expanded.resolve()
    if not resolved.is_dir():
        raise ReconstructionError("Le dossier du fangame est introuvable.")
    return resolved


def _require_essentials_reconstruction(game_root: Path):
    """Réinterroge le registre avant toute opération de reconstruction.

    Cette barrière protège également les appels directs au moteur, sans
    dépendre de l'état des boutons de l'interface.
    """
    from adapters import AdapterOperationBlocked, GameCapability, authorize_adapter_operation

    try:
        return authorize_adapter_operation(
            game_root,
            expected_adapter_id=ESSENTIALS_ADAPTER_ID,
            capability=GameCapability.RECONSTRUCT,
            require_write_authorization=True,
        )
    except AdapterOperationBlocked as exc:
        raise ReconstructionError(
            "Reconstruction bloquée : l'adaptateur Pokémon Essentials n'est pas "
            f"autorisé pour ce dossier ({exc})."
        ) from exc


def _require_v21_1_validation(game_root: Path):
    """Autorise uniquement la porte privée et bornée du round-trip v21.1.

    Cette fonction n'accorde jamais la capacité ``RECONSTRUCT`` au profil. Elle
    réutilise la détection multi-adaptateurs, exige le profil exact confirmé et
    refuse de fonctionner si ce profil n'est plus strictement en lecture seule
    dans l'interface publique.
    """
    from adapters import AdapterOperationBlocked, GameCapability, authorize_adapter_operation

    try:
        detection = authorize_adapter_operation(
            game_root,
            expected_adapter_id=ESSENTIALS_ADAPTER_ID,
            capability=GameCapability.EXTRACT,
        )
    except AdapterOperationBlocked as exc:
        raise ReconstructionError(
            "Validation v21.1 bloquée : la détection Essentials n'est pas concluante "
            f"pour cette copie ({exc})."
        ) from exc
    if (
        detection.structural_profile != V21_1_VALIDATION_PROFILE
        or detection.declared_version != "21.1"
        or not detection.extraction_compatible
        or detection.game_write_compatible
        or detection.reconstruction_validated
        or detection.can(GameCapability.RECONSTRUCT)
    ):
        raise ReconstructionError(
            "Validation v21.1 bloquée : seul le profil v21.1 confirmé et encore "
            "volontairement privé de reconstruction peut utiliser cette porte interne."
        )
    return detection


def _validate_v21_1_validation_scope(
    plan: ReconstructionPlan,
    detection,
) -> PlanItem:
    """Refuse tout plan plus large que l'unique preuve synthétique autorisée."""
    if plan.validation_scope != V21_1_VALIDATION_SCOPE:
        raise ReconstructionError(
            "Le plan ne porte pas la portée de validation v21.1 attendue."
        )
    if (
        plan.adapter_id != detection.adapter_id
        or plan.adapter_version != detection.recognized_version
        or plan.adapter_profile != detection.structural_profile
        or plan.mode != "accepted"
    ):
        raise ReconstructionError(
            "Le plan de validation v21.1 ne correspond plus au profil détecté."
        )
    accepted = [item for item in plan.items if item.status == "Accepté"]
    applicable = [item for item in plan.items if item.decision == "applicable"]
    if len(accepted) != 1 or len(applicable) != 1:
        raise ReconstructionError(
            "La validation v21.1 exige une seule occurrence acceptée et applicable."
        )
    selected = applicable[0]
    if accepted[0].id_stable != selected.id_stable:
        raise ReconstructionError(
            "La seule occurrence acceptée n'est pas l'occurrence applicable du plan."
        )
    if (
        selected.type != "Banque de messages"
        or selected.fichier.replace("\\", "/").casefold()
        != V21_1_VALIDATION_FILE.casefold()
    ):
        raise ReconstructionError(
            "La validation v21.1 est limitée à une seule banque de messages dans "
            "Data/messages_game.dat ; événements communs, Point, PBS et cartes restent exclus."
        )
    if not selected.translation or extract_protected(selected.source) != extract_protected(
        selected.translation
    ):
        raise ReconstructionError(
            "La traduction de validation v21.1 ne conserve pas exactement les commandes."
        )
    if any(item.decision == "blocked" for item in plan.items):
        raise ReconstructionError(
            "Le plan de validation v21.1 contient une occurrence bloquée."
        )
    if set(plan.source_hashes) != {V21_1_VALIDATION_FILE}:
        raise ReconstructionError(
            "Le plan de validation v21.1 cible un inventaire de fichiers inattendu."
        )
    return selected


def _validate_v21_1_scope_header(
    plan: ReconstructionPlan,
    detection,
    expected_scope: str,
) -> list[PlanItem]:
    if plan.validation_scope != expected_scope:
        raise ReconstructionError(
            "Le plan ne porte pas la portée de validation v21.1 attendue."
        )
    if (
        plan.adapter_id != detection.adapter_id
        or plan.adapter_version != detection.recognized_version
        or plan.adapter_profile != detection.structural_profile
        or plan.mode != "accepted"
    ):
        raise ReconstructionError(
            "Le plan de validation v21.1 ne correspond plus au profil détecté."
        )
    if any(item.decision == "blocked" for item in plan.items):
        raise ReconstructionError(
            "Le plan de validation v21.1 contient une occurrence bloquée."
        )
    return [item for item in plan.items if item.decision == "applicable"]


def _validate_v21_1_bank_corpus_scope(
    plan: ReconstructionPlan,
    detection,
) -> list[PlanItem]:
    """Borne le corpus réel aux trois formes de banques v21.1 observées."""
    applicable = _validate_v21_1_scope_header(
        plan,
        detection,
        V21_1_BANK_CORPUS_VALIDATION_SCOPE,
    )
    accepted = [item for item in plan.items if item.status == "Accepté"]
    if len(accepted) != 3 or len(applicable) != 3:
        raise ReconstructionError(
            "Le corpus de banques v21.1 exige exactement trois occurrences acceptées."
        )
    if {item.id_stable for item in accepted} != {
        item.id_stable for item in applicable
    }:
        raise ReconstructionError(
            "Les occurrences acceptées ne correspondent pas exactement au corpus applicable."
        )
    if any(item.type != "Banque de messages" for item in applicable):
        raise ReconstructionError(
            "Le corpus v21.1 est limité aux banques de messages ; cartes, PBS et "
            "événements communs restent exclus."
        )
    by_shape: Counter[tuple[str, str]] = Counter()
    for item in applicable:
        normalized = item.fichier.replace("\\", "/")
        location = item.event_name.strip()
        if normalized.casefold() == "data/messages_core.dat":
            shape = "direct" if re.fullmatch(r"\d+/entry/\d+", location) else "unknown"
            by_shape[("core", shape)] += 1
        elif normalized.casefold() == "data/messages_game.dat":
            if re.fullmatch(r"\d+/entry/\d+", location):
                shape = "direct"
            elif re.fullmatch(r"\d+/\d+/entry/\d+", location):
                shape = "nested"
            else:
                shape = "unknown"
            by_shape[("game", shape)] += 1
        else:
            raise ReconstructionError(
                "Le corpus de banques v21.1 cible un fichier inattendu."
            )
        if not item.translation or extract_protected(item.source) != extract_protected(
            item.translation
        ):
            raise ReconstructionError(
                "Une traduction du corpus v21.1 ne conserve pas exactement les commandes."
            )
    expected_shapes = Counter(
        {("core", "direct"): 1, ("game", "direct"): 1, ("game", "nested"): 1}
    )
    if by_shape != expected_shapes:
        raise ReconstructionError(
            "Le corpus de banques v21.1 doit couvrir exactement les formes core directe, "
            "game directe et game imbriquée."
        )
    if {path.casefold() for path in plan.source_hashes} != {
        path.casefold() for path in V21_1_BANK_CORPUS_FILES
    }:
        raise ReconstructionError(
            "Le corpus de banques v21.1 cible un inventaire de fichiers inattendu."
        )
    return applicable


def _validate_v21_1_map_scope(
    plan: ReconstructionPlan,
    detection,
) -> list[PlanItem]:
    """Borne la preuve carte à un dialogue et un choix d'une même page."""
    applicable = _validate_v21_1_scope_header(
        plan,
        detection,
        V21_1_MAP_VALIDATION_SCOPE,
    )
    accepted = [item for item in plan.items if item.status == "Accepté"]
    if len(accepted) != 2 or len(applicable) != 2:
        raise ReconstructionError(
            "La validation de carte v21.1 exige exactement un dialogue et un choix acceptés."
        )
    if {item.id_stable for item in accepted} != {
        item.id_stable for item in applicable
    } or Counter(item.type for item in applicable) != Counter(
        {"Dialogue": 1, "Choix": 1}
    ):
        raise ReconstructionError(
            "La validation de carte v21.1 exige exactement le dialogue et le choix applicables."
        )
    shared_locations = {
        (
            item.fichier.replace("\\", "/").casefold(),
            item.map_id,
            item.event_id,
            item.page,
        )
        for item in applicable
    }
    if len(shared_locations) != 1:
        raise ReconstructionError(
            "Le dialogue et le choix v21.1 doivent appartenir à la même page de carte."
        )
    relative = applicable[0].fichier.replace("\\", "/")
    map_match = re.fullmatch(r"Data/Map(\d{3,4})\.rxdata", relative, re.IGNORECASE)
    if not map_match:
        raise ReconstructionError("La validation v21.1 exige une carte MapXXX.rxdata.")
    if _integer(applicable[0].map_id, "Identifiant de carte") != int(map_match.group(1)):
        raise ReconstructionError(
            "L'identifiant de carte v21.1 ne correspond pas au fichier MapXXX.rxdata."
        )
    if {path.casefold() for path in plan.source_hashes} != {relative.casefold()}:
        raise ReconstructionError(
            "Le plan de carte v21.1 cible un inventaire de fichiers inattendu."
        )
    dialogue = next(item for item in applicable if item.type == "Dialogue")
    choice = next(item for item in applicable if item.type == "Choix")
    if (
        _integer(dialogue.rpg_command_code, "Code RPG du dialogue") != 101
        or _integer(dialogue.rpg_parameter_index, "Paramètre RPG du dialogue") != 0
        or not dialogue.rpg_dialogue_segments
        or _integer(dialogue.rpg_continuation_end, "Fin du dialogue")
        < _integer(dialogue.command, "Commande du dialogue")
    ):
        raise ReconstructionError("Métadonnées 101/401 du dialogue v21.1 invalides.")
    if (
        _integer(choice.rpg_command_code, "Code RPG du choix") != 102
        or _integer(choice.rpg_parameter_index, "Paramètre RPG du choix") != 0
        or _integer(choice.rpg_continuation_end, "Fin du choix")
        != _integer(choice.command, "Commande du choix")
        or _integer(choice.rpg_choice_branch_parameter_index, "Paramètre 402") != 1
        or _integer(choice.rpg_choice_branch_command, "Branche 402")
        <= _integer(choice.command, "Commande du choix")
    ):
        raise ReconstructionError(
            "Métadonnées 102/402 du choix v21.1 absentes ou ambiguës."
        )
    for item in applicable:
        _ = _integer(item.rpg_command_indent, "Indentation RPG")
        if not item.translation or extract_protected(item.source) != extract_protected(
            item.translation
        ):
            raise ReconstructionError(
                "Une traduction de carte v21.1 ne conserve pas exactement les commandes."
            )
    return applicable


def _validate_v21_1_common_events_scope(
    plan: ReconstructionPlan,
    detection,
) -> list[PlanItem]:
    """Borne la preuve à trois dialogues répartis sur deux événements communs."""
    applicable = _validate_v21_1_scope_header(
        plan,
        detection,
        V21_1_COMMON_EVENTS_VALIDATION_SCOPE,
    )
    accepted = [item for item in plan.items if item.status == "Accepté"]
    if len(accepted) != 3 or len(applicable) != 3:
        raise ReconstructionError(
            "La validation des événements communs v21.1 exige exactement trois "
            "dialogues acceptés et applicables."
        )
    if {item.id_stable for item in accepted} != {
        item.id_stable for item in applicable
    }:
        raise ReconstructionError(
            "Les occurrences acceptées ne correspondent pas exactement au corpus "
            "d'événements communs applicable."
        )
    if any(
        item.type != "Événement commun — Dialogue"
        or item.fichier.replace("\\", "/").casefold()
        != V21_1_COMMON_EVENTS_FILE.casefold()
        for item in applicable
    ):
        raise ReconstructionError(
            "Le corpus v21.1 est limité aux dialogues de Data/CommonEvents.rxdata."
        )
    if {path.casefold() for path in plan.source_hashes} != {
        V21_1_COMMON_EVENTS_FILE.casefold()
    }:
        raise ReconstructionError(
            "Le plan d'événements communs v21.1 cible un inventaire inattendu."
        )

    locations: set[tuple[int, int, int]] = set()
    event_counts: Counter[tuple[int, int]] = Counter()
    event_proofs: dict[tuple[int, int], set[tuple[int, int, str]]] = defaultdict(set)
    segment_counts: list[int] = []
    has_internal_line_control = False
    for item in applicable:
        array_index = _integer(
            item.rpg_common_event_array_index,
            "Index de l'événement commun",
        )
        event_id = _integer(item.event_id, "ID de l'événement commun")
        command_index = _integer(item.command, "Commande de l'événement commun")
        trigger = _integer(item.rpg_common_event_trigger, "Trigger de l'événement commun")
        switch_id = _integer(
            item.rpg_common_event_switch_id,
            "Switch de l'événement commun",
        )
        if (
            array_index <= 0
            or event_id != array_index
            or command_index < 0
            or trigger not in {0, 1, 2}
            or switch_id <= 0
        ):
            raise ReconstructionError(
                "ID, index, trigger, switch ou commande de l'événement commun "
                "v21.1 incohérent."
            )
        location = (array_index, event_id, command_index)
        if location in locations:
            raise ReconstructionError(
                "Deux occurrences d'événement commun ciblent la même commande."
            )
        locations.add(location)
        event_key = (array_index, event_id)
        event_counts[event_key] += 1
        if not re.fullmatch(r"[0-9a-f]{64}", item.rpg_common_event_sha256):
            raise ReconstructionError(
                "Empreinte de l'événement commun v21.1 absente ou invalide."
            )
        event_proofs[event_key].add(
            (trigger, switch_id, item.rpg_common_event_sha256)
        )
        if (
            item.map_id
            or item.page
            or item.sub_index
            or _integer(item.rpg_command_code, "Code du dialogue commun") != 101
            or _integer(item.rpg_parameter_index, "Paramètre du dialogue commun") != 0
            or _integer(item.rpg_command_indent, "Indentation du dialogue commun") < 0
            or _integer(item.rpg_continuation_end, "Fin du dialogue commun")
            < command_index
            or not item.rpg_dialogue_segments
        ):
            raise ReconstructionError(
                "Métadonnées du dialogue d'événement commun v21.1 incohérentes."
            )
        if not item.translation or extract_protected(item.source) != extract_protected(
            item.translation
        ):
            raise ReconstructionError(
                "Une traduction d'événement commun ne conserve pas exactement "
                "les commandes."
            )
        try:
            metadata = json.loads(item.rpg_dialogue_segments)
            segments = metadata["segments"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ReconstructionError(
                "Preuve de segmentation de l'événement commun illisible."
            ) from exc
        if (
            metadata.get("format") != "pft_rpg_dialogue_segments_v1"
            or metadata.get("start_index") != command_index
            or metadata.get("end_index")
            != _integer(item.rpg_continuation_end, "Fin du dialogue commun")
            or not isinstance(segments, list)
            or not segments
        ):
            raise ReconstructionError(
                "Preuve de segmentation de l'événement commun incohérente."
            )
        internal_counts: list[int] = []
        for segment in segments:
            if not isinstance(segment, dict):
                raise ReconstructionError(
                    "Preuve de segmentation de l'événement commun incohérente."
                )
            internal_count = segment.get("internal_line_control_count")
            if not isinstance(internal_count, int) or internal_count < 0:
                raise ReconstructionError(
                    "Nombre de contrôles internes de l'événement commun invalide."
                )
            internal_counts.append(internal_count)
        segment_counts.append(len(segments))
        has_internal_line_control = (
            has_internal_line_control or any(count > 0 for count in internal_counts)
        )

    if sorted(event_counts.values()) != [1, 2]:
        raise ReconstructionError(
            "Le corpus doit couvrir deux dialogues dans un événement commun et un "
            "dialogue dans un second événement."
        )
    if any(len(proofs) != 1 for proofs in event_proofs.values()):
        raise ReconstructionError(
            "Les occurrences d'un même événement commun portent des preuves incompatibles."
        )
    if not any(count == 1 for count in segment_counts) or not any(
        count >= 3 for count in segment_counts
    ) or not has_internal_line_control:
        raise ReconstructionError(
            "Le corpus doit inclure un dialogue simple, plusieurs continuations 401 "
            "et un contrôle interne \\n."
        )
    return applicable


def _validate_v21_1_private_scope(
    plan: ReconstructionPlan,
    detection,
) -> list[PlanItem]:
    if plan.validation_scope == V21_1_VALIDATION_SCOPE:
        return [_validate_v21_1_validation_scope(plan, detection)]
    if plan.validation_scope == V21_1_BANK_CORPUS_VALIDATION_SCOPE:
        return _validate_v21_1_bank_corpus_scope(plan, detection)
    if plan.validation_scope == V21_1_MAP_VALIDATION_SCOPE:
        return _validate_v21_1_map_scope(plan, detection)
    if plan.validation_scope == V21_1_COMMON_EVENTS_VALIDATION_SCOPE:
        return _validate_v21_1_common_events_scope(plan, detection)
    raise ReconstructionError("Portée de validation privée inconnue.")


def _assert_reserved_copy_outputs_absent(source_root: Path) -> None:
    """Empêche les fichiers générés après validation d'écraser un homonyme."""
    collisions = [name for name in RESERVED_COPY_OUTPUTS if (source_root / name).exists()]
    if collisions:
        raise ReconstructionError(
            "Reconstruction bloquée : le fangame contient déjà un fichier réservé "
            f"par l'application ({', '.join(collisions)})."
        )


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


def _integrity_snapshot(root: Path, label: str):
    try:
        return snapshot_tree(root)
    except IntegrityError as exc:
        raise ReconstructionError(
            f"Contrôle d'intégrité impossible pour {label} : {exc}"
        ) from exc


def _integrity_failure(label: str, comparison: SnapshotComparison) -> str:
    details = []
    if comparison.missing_files:
        details.append(f"{len(comparison.missing_files)} fichier(s) manquant(s)")
    if comparison.unexpected_files:
        details.append(f"{len(comparison.unexpected_files)} fichier(s) inattendu(s)")
    if comparison.changed_files:
        details.append(f"{len(comparison.changed_files)} fichier(s) modifié(s) hors plan")
    if comparison.emptied_files:
        details.append(f"{len(comparison.emptied_files)} fichier(s) devenu(s) vide(s)")
    summary = ", ".join(details) or "écart non identifié"
    return f"Contrôle d'intégrité échoué pour {label} : {summary}."


def _integer(value: str, field_name: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise ReconstructionError(f"{field_name} invalide : {value!r}")


def _supported_row_type(row_type: str, *, validation_scope: str = "") -> bool:
    if (
        validation_scope == V21_1_COMMON_EVENTS_VALIDATION_SCOPE
        and row_type == "Événement commun — Dialogue"
    ):
        return True
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


def _build_plan_verified_body(
    game_root: Path,
    csv_path: Path,
    mode: str = "recommended",
    *,
    preauthorized_detection=None,
    validation_scope: str = "",
) -> ReconstructionPlan:
    game_root = _resolve_safe_game_root(game_root)
    csv_input = csv_path.expanduser()
    if _is_link_or_junction(csv_input) or _is_link_or_junction(csv_input.parent):
        raise ReconstructionError(
            "Le CSV de traduction ou son dossier ne peut pas être un lien ou une jonction."
        )
    csv_path = csv_input.resolve()
    if not csv_path.is_file():
        raise ReconstructionError("Le projet de traduction est introuvable.")
    if mode not in {"accepted", "recommended", "all_reviewed"}:
        raise ReconstructionError(f"Mode de reconstruction inconnu : {mode}")
    detection = preauthorized_detection or _require_essentials_reconstruction(game_root)
    try:
        identity = read_project_identity(
            csv_path,
            game_root,
            expected_adapter_id=detection.adapter_id,
            require_extraction_provenance=True,
        )
    except ProjectIdentityError as exc:
        raise ReconstructionError(f"Projet de traduction refusé : {exc}") from exc
    if validation_scope and identity.adapter_profile != detection.structural_profile:
        raise ReconstructionError(
            "Projet de traduction refusé : le profil Essentials de l'identité ne "
            "correspond pas au profil détecté."
        )
    if identity.source_manifest_sha256:
        try:
            current_inventory = build_extraction_inventory(game_root)
        except ExtractionIntegrityError as exc:
            raise ReconstructionError(
                f"Projet de traduction refusé : inventaire Essentials invérifiable ({exc})."
            ) from exc
        if current_inventory.source_manifest_sha256 != identity.source_manifest_sha256:
            raise ReconstructionError(
                "Projet de traduction refusé : les sources Essentials ne correspondent plus "
                "à l'état enregistré lors de l'extraction. Relancez l'extraction."
            )

    plan = ReconstructionPlan(
        game_root=str(game_root),
        csv_path=str(csv_path),
        created_at=datetime.now().isoformat(timespec="seconds"),
        mode=mode,
        adapter_id=detection.adapter_id,
        adapter_version=detection.recognized_version,
        adapter_profile=detection.structural_profile,
        validation_scope=validation_scope,
        csv_sha256=sha256_file(csv_path),
        project_identity_sha256=identity.sha256,
    )

    rows = load_project_rows(csv_path)
    if identity.source_manifest_sha256:
        row_manifests = {
            str(row.get("source_manifest_sha256") or "").casefold()
            for row in rows
        }
        if row_manifests != {identity.source_manifest_sha256}:
            raise ReconstructionError(
                "Projet de traduction refusé : le CSV ne correspond pas à l'inventaire "
                "Essentials enregistré lors de l'extraction."
            )
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
            rpg_command_code=row.get("rpg_command_code", ""),
            rpg_command_indent=row.get("rpg_command_indent", ""),
            rpg_parameter_index=row.get("rpg_parameter_index", ""),
            rpg_continuation_end=row.get("rpg_continuation_end", ""),
            rpg_dialogue_segments=row.get("rpg_dialogue_segments", ""),
            rpg_common_event_array_index=row.get(
                "rpg_common_event_array_index", ""
            ),
            rpg_common_event_trigger=row.get("rpg_common_event_trigger", ""),
            rpg_common_event_switch_id=row.get(
                "rpg_common_event_switch_id", ""
            ),
            rpg_common_event_sha256=row.get("rpg_common_event_sha256", ""),
            rpg_choice_branch_command=row.get("rpg_choice_branch_command", ""),
            rpg_choice_branch_parameter_index=row.get(
                "rpg_choice_branch_parameter_index", ""
            ),
        )

        if not _supported_row_type(item.type, validation_scope=validation_scope):
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


def build_plan(game_root: Path, csv_path: Path, mode: str = "recommended") -> ReconstructionPlan:
    """Construit un plan seulement sous une garde de provenance exclusive."""
    safe_root = _resolve_safe_game_root(game_root)
    _require_essentials_reconstruction(safe_root)
    csv_input = csv_path.expanduser()
    if _is_link_or_junction(csv_input) or _is_link_or_junction(csv_input.parent):
        raise ReconstructionError(
            "Le CSV de traduction ou son dossier ne peut pas être un lien ou une jonction."
        )
    try:
        guard = open_verified_project(
            csv_input,
            game_root=safe_root,
            expected_adapter_id=ESSENTIALS_ADAPTER_ID,
        )
    except TranslationProjectError as exc:
        raise ReconstructionError(f"Projet de traduction refusé : {exc}") from exc
    try:
        plan = _build_plan_verified_body(safe_root, csv_input, mode)
        guard.check_current()
        assert guard.snapshot is not None
        plan.project_provenance_token = guard.snapshot.provenance_token()
        return plan
    finally:
        guard.close()


def build_v21_1_validation_plan(
    game_root: Path,
    csv_path: Path,
) -> ReconstructionPlan:
    """Construit le candidat privé v21.1 sans débloquer la reconstruction UI.

    La portée est volontairement figée à une seule occurrence acceptée de
    ``Data/messages_game.dat``. Cette porte sert à démontrer le round-trip sur
    copie ; elle n'est pas une déclaration de compatibilité générale.
    """
    return _build_v21_1_private_validation_plan(
        game_root,
        csv_path,
        V21_1_VALIDATION_SCOPE,
    )


def build_v21_1_bank_corpus_validation_plan(
    game_root: Path,
    csv_path: Path,
) -> ReconstructionPlan:
    """Construit le corpus privé des trois formes de banques v21.1 observées."""
    return _build_v21_1_private_validation_plan(
        game_root,
        csv_path,
        V21_1_BANK_CORPUS_VALIDATION_SCOPE,
    )


def build_v21_1_map_validation_plan(
    game_root: Path,
    csv_path: Path,
) -> ReconstructionPlan:
    """Construit la preuve privée d'un dialogue et d'un choix de carte v21.1."""
    return _build_v21_1_private_validation_plan(
        game_root,
        csv_path,
        V21_1_MAP_VALIDATION_SCOPE,
    )


def build_v21_1_common_events_validation_plan(
    game_root: Path,
    csv_path: Path,
) -> ReconstructionPlan:
    """Construit le corpus privé de trois dialogues d'événements communs v21.1."""
    return _build_v21_1_private_validation_plan(
        game_root,
        csv_path,
        V21_1_COMMON_EVENTS_VALIDATION_SCOPE,
    )


def _build_v21_1_private_validation_plan(
    game_root: Path,
    csv_path: Path,
    validation_scope: str,
) -> ReconstructionPlan:
    if validation_scope not in V21_1_PRIVATE_VALIDATION_SCOPES:
        raise ReconstructionError("Portée de validation privée inconnue.")
    safe_root = _resolve_safe_game_root(game_root)
    detection = _require_v21_1_validation(safe_root)
    csv_input = csv_path.expanduser()
    if _is_link_or_junction(csv_input) or _is_link_or_junction(csv_input.parent):
        raise ReconstructionError(
            "Le CSV de validation ou son dossier ne peut pas être un lien ou une jonction."
        )
    try:
        guard = open_verified_project(
            csv_input,
            game_root=safe_root,
            expected_adapter_id=ESSENTIALS_ADAPTER_ID,
        )
    except TranslationProjectError as exc:
        raise ReconstructionError(f"Projet de traduction refusé : {exc}") from exc
    try:
        plan = _build_plan_verified_body(
            safe_root,
            csv_input,
            "accepted",
            preauthorized_detection=detection,
            validation_scope=validation_scope,
        )
        _validate_v21_1_private_scope(plan, detection)
        guard.check_current()
        assert guard.snapshot is not None
        plan.project_provenance_token = guard.snapshot.provenance_token()
        return plan
    finally:
        guard.close()


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


def _locate_v21_map_message(root: RubyObject, item: PlanItem):
    """Exige les classes RPG standard avant la preuve privée v21.1."""
    if root.class_name != "RPG::Map":
        raise ReconstructionError("Carte RPG::Map v21.1 attendue")
    event_id = _integer(item.event_id, "Événement")
    page_number = _integer(item.page, "Page")
    command_index = _integer(item.command, "Commande")
    events = root.ivars.get("@events", {})
    event = events.get(event_id) if isinstance(events, dict) else None
    if (
        not isinstance(event, RubyObject)
        or event.class_name != "RPG::Event"
        or event.ivars.get("@id") != event_id
    ):
        raise ReconstructionError("Événement RPG::Event v21.1 introuvable")
    pages = event.ivars.get("@pages", [])
    if not isinstance(pages, list) or not (1 <= page_number <= len(pages)):
        raise ReconstructionError("Page v21.1 introuvable")
    page = pages[page_number - 1]
    if not isinstance(page, RubyObject) or page.class_name != "RPG::Event::Page":
        raise ReconstructionError("Page RPG::Event::Page v21.1 attendue")
    commands = page.ivars.get("@list", [])
    if not isinstance(commands, list) or not (0 <= command_index < len(commands)):
        raise ReconstructionError("Commande v21.1 introuvable")
    return commands, command_index


def _apply_map_item(root: RubyObject, item: PlanItem) -> None:
    commands, index = _locate_v21_map_message(root, item)
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


def _strict_v21_dialogue_commands(
    root: RubyObject,
    item: PlanItem,
) -> tuple[list[RubyObject], list[str]]:
    commands, index = _locate_map_message(root, item)
    try:
        segmentation = segment_dialogue_commands(commands, index)
    except DialogueSegmentationError as exc:
        raise ReconstructionError(
            f"Segmentation 101/401 du dialogue v21.1 invalide : {exc}"
        ) from exc
    if (
        _integer(item.rpg_command_code, "Code RPG") != 101
        or _integer(item.rpg_parameter_index, "Paramètre RPG") != 0
        or _integer(item.rpg_command_indent, "Indentation RPG")
        != segmentation.indent
        or _integer(item.rpg_continuation_end, "Fin 401")
        != segmentation.end_index
        or item.rpg_dialogue_segments != segmentation.metadata
    ):
        raise ReconstructionError(
            "Preuve de segmentation 101/401 du dialogue v21.1 absente ou incohérente"
        )
    if segmentation.source_text.strip() != item.source.strip():
        raise ReconstructionError("Le dialogue v21.1 original ne correspond plus au projet")
    try:
        translated_pieces = split_dialogue_translation(segmentation, item.translation)
    except DialogueSegmentationError as exc:
        raise ReconstructionError(
            f"Segmentation de la traduction v21.1 bloquée : {exc}"
        ) from exc
    actual_commands = [
        commands[segment.command_index] for segment in segmentation.segments
    ]
    return actual_commands, translated_pieces


def _strict_v21_choice_commands(
    root: RubyObject,
    item: PlanItem,
) -> tuple[RubyObject, RubyObject, int]:
    commands, index = _locate_v21_map_message(root, item)
    command = commands[index]
    expected_indent = _integer(item.rpg_command_indent, "Indentation RPG")
    choice_index = _integer(item.sub_index, "Index de choix")
    if (
        not isinstance(command, RubyObject)
        or command.class_name != "RPG::EventCommand"
        or command.ivars.get("@code") != 102
        or command.ivars.get("@indent") != expected_indent
        or _integer(item.rpg_command_code, "Code RPG") != 102
        or _integer(item.rpg_parameter_index, "Paramètre RPG") != 0
        or _integer(item.rpg_continuation_end, "Fin du choix") != index
    ):
        raise ReconstructionError("Structure 102 du choix v21.1 incohérente")
    parameters = command.ivars.get("@parameters", [])
    if (
        not isinstance(parameters, list)
        or not parameters
        or not isinstance(parameters[0], list)
        or not (0 <= choice_index < len(parameters[0]))
        or text_value(parameters[0][choice_index]).strip() != item.source.strip()
    ):
        raise ReconstructionError("Texte 102 du choix v21.1 incohérent")

    branch_index = _integer(item.rpg_choice_branch_command, "Branche 402")
    branch_parameter_index = _integer(
        item.rpg_choice_branch_parameter_index,
        "Paramètre de branche 402",
    )
    if not (index < branch_index < len(commands)) or branch_parameter_index != 1:
        raise ReconstructionError("Branche 402 du choix v21.1 absente ou ambiguë")
    branch = commands[branch_index]
    branch_parameters = (
        branch.ivars.get("@parameters", []) if isinstance(branch, RubyObject) else []
    )
    if (
        not isinstance(branch, RubyObject)
        or branch.class_name != "RPG::EventCommand"
        or branch.ivars.get("@code") != 402
        or branch.ivars.get("@indent") != expected_indent
        or not isinstance(branch_parameters, list)
        or len(branch_parameters) <= branch_parameter_index
        or branch_parameters[0] != choice_index
        or text_value(branch_parameters[branch_parameter_index]).strip()
        != item.source.strip()
    ):
        raise ReconstructionError("Branche 402 du choix v21.1 incohérente")

    matching_branches: list[int] = []
    for candidate_index in range(index + 1, len(commands)):
        candidate = commands[candidate_index]
        if not isinstance(candidate, RubyObject):
            continue
        if candidate.class_name != "RPG::EventCommand":
            raise ReconstructionError("Commande de branche v21.1 non standard")
        candidate_code = candidate.ivars.get("@code")
        candidate_indent = candidate.ivars.get("@indent")
        if candidate_code == 404 and candidate_indent == expected_indent:
            break
        if candidate_code != 402 or candidate_indent != expected_indent:
            continue
        candidate_parameters = candidate.ivars.get("@parameters", [])
        if (
            isinstance(candidate_parameters, list)
            and len(candidate_parameters) >= 2
            and candidate_parameters[0] == choice_index
            and text_value(candidate_parameters[1]).strip() == item.source.strip()
        ):
            matching_branches.append(candidate_index)
    if matching_branches != [branch_index]:
        raise ReconstructionError("Branche 402 du choix v21.1 absente ou ambiguë")
    return command, branch, choice_index


def _apply_v21_1_map_items(root: RubyObject, items: list[PlanItem]) -> None:
    """Modifie uniquement les paramètres textuels vérifiés du corpus carte."""
    dialogue = next((item for item in items if item.type == "Dialogue"), None)
    choice = next((item for item in items if item.type == "Choix"), None)
    if dialogue is None or choice is None or len(items) != 2:
        raise ReconstructionError("Corpus de carte v21.1 incomplet")

    dialogue_commands, translated_pieces = _strict_v21_dialogue_commands(
        root,
        dialogue,
    )
    choice_command, branch_command, choice_index = _strict_v21_choice_commands(
        root,
        choice,
    )
    for event_command, translated_piece in zip(
        dialogue_commands,
        translated_pieces,
    ):
        parameters = event_command.ivars["@parameters"]
        parameters[0] = _ruby_string_set(parameters[0], translated_piece)
        _assert_utf8_translation_bytes(
            parameters[0],
            f"{dialogue.fichier} — dialogue v21.1 {dialogue.id_stable}",
        )

    choice_parameters = choice_command.ivars["@parameters"]
    branch_parameters = branch_command.ivars["@parameters"]
    choice_parameters[0][choice_index] = _ruby_string_set(
        choice_parameters[0][choice_index],
        choice.translation,
    )
    branch_parameters[1] = _ruby_string_set(
        branch_parameters[1],
        choice.translation,
    )
    _assert_utf8_translation_bytes(
        choice_parameters[0][choice_index],
        f"{choice.fichier} — choix 102 v21.1 {choice.id_stable}",
    )
    _assert_utf8_translation_bytes(
        branch_parameters[1],
        f"{choice.fichier} — branche 402 v21.1 {choice.id_stable}",
    )


def _strict_v21_common_event_dialogue(
    root: list,
    item: PlanItem,
) -> tuple[list, DialogueSegmentation, list[str]]:
    """Localise un dialogue commun et revalide l'événement source complet."""
    array_index = _integer(
        item.rpg_common_event_array_index,
        "Index de l'événement commun",
    )
    event_id = _integer(item.event_id, "ID de l'événement commun")
    if not (0 < array_index < len(root)) or event_id != array_index:
        raise ReconstructionError("Événement commun v21.1 introuvable ou déplacé")
    event = root[array_index]
    if (
        not isinstance(event, RubyObject)
        or event.class_name != "RPG::CommonEvent"
        or event.ivars.get("@id") != event_id
        or not isinstance(event.ivars.get("@name"), RubyString)
        or event.ivars.get("@trigger")
        != _integer(item.rpg_common_event_trigger, "Trigger de l'événement commun")
        or event.ivars.get("@switch_id")
        != _integer(item.rpg_common_event_switch_id, "Switch de l'événement commun")
    ):
        raise ReconstructionError(
            "ID, classe, trigger ou switch de l'événement commun v21.1 incohérent"
        )
    if hashlib.sha256(dumps(event)).hexdigest() != item.rpg_common_event_sha256:
        raise ReconstructionError(
            "L'événement commun v21.1 ne correspond plus à son empreinte extraite"
        )
    commands = event.ivars.get("@list")
    command_index = _integer(item.command, "Commande de l'événement commun")
    if not isinstance(commands, list) or not (0 <= command_index < len(commands)):
        raise ReconstructionError("Liste ou commande d'événement commun v21.1 invalide")
    try:
        segmentations = {
            candidate.start_index: candidate
            for candidate in validate_dialogue_command_stream(commands)
        }
    except DialogueSegmentationError as exc:
        raise ReconstructionError(
            f"Segmentation 101/401 de l'événement commun invalide : {exc}"
        ) from exc
    segmentation = segmentations.get(command_index)
    if segmentation is None:
        raise ReconstructionError(
            "La commande ciblée n'est pas le début d'un dialogue 101/401 valide."
        )
    if (
        _integer(item.rpg_command_code, "Code du dialogue commun") != 101
        or _integer(item.rpg_parameter_index, "Paramètre du dialogue commun") != 0
        or _integer(item.rpg_command_indent, "Indentation du dialogue commun")
        != segmentation.indent
        or _integer(item.rpg_continuation_end, "Fin du dialogue commun")
        != segmentation.end_index
        or item.rpg_dialogue_segments != segmentation.metadata
    ):
        raise ReconstructionError(
            "Preuve structurelle 101/401 de l'événement commun absente ou incohérente"
        )
    if segmentation.source_text.strip() != item.source.strip():
        raise ReconstructionError(
            "Le dialogue de l'événement commun ne correspond plus au projet"
        )
    try:
        translated_pieces = split_dialogue_translation(segmentation, item.translation)
    except DialogueSegmentationError as exc:
        raise ReconstructionError(
            f"Segmentation de la traduction de l'événement commun bloquée : {exc}"
        ) from exc
    return commands, segmentation, translated_pieces


def _apply_v21_1_common_event_items(root: list, items: list[PlanItem]) -> None:
    """Valide toutes les occurrences communes avant la première mutation."""
    if (
        not isinstance(root, list)
        or not root
        or root[0] is not None
        or len(items) != 3
    ):
        raise ReconstructionError("Corpus d'événements communs v21.1 invalide")
    validated: list[
        tuple[PlanItem, list, DialogueSegmentation, list[str]]
    ] = []
    occupied: set[tuple[int, int]] = set()
    for item in items:
        commands, segmentation, translated_pieces = _strict_v21_common_event_dialogue(
            root,
            item,
        )
        array_index = _integer(
            item.rpg_common_event_array_index,
            "Index de l'événement commun",
        )
        for segment in segmentation.segments:
            location = (array_index, segment.command_index)
            if location in occupied:
                raise ReconstructionError(
                    "Deux dialogues d'événements communs se chevauchent."
                )
            occupied.add(location)
        validated.append((item, commands, segmentation, translated_pieces))

    for item, commands, segmentation, translated_pieces in validated:
        for segment, translated_piece in zip(
            segmentation.segments,
            translated_pieces,
        ):
            event_command = commands[segment.command_index]
            parameters = event_command.ivars["@parameters"]
            original_string = parameters[segment.parameter_index]
            original_ivars = dumps(original_string.ivars)
            parameters[segment.parameter_index] = _ruby_string_set(
                original_string,
                translated_piece,
            )
            if dumps(parameters[segment.parameter_index].ivars) != original_ivars:
                raise ReconstructionError(
                    "La traduction exigerait de modifier les métadonnées d'encodage "
                    "du dialogue d'événement commun."
                )
            _assert_utf8_translation_bytes(
                parameters[segment.parameter_index],
                f"{item.fichier} — événement commun v21.1 {item.id_stable}",
            )


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
        if not is_translatable_pbs_key(key) or not looks_visible(value):
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
    try:
        atomic_write_text(path, rebuilt, encoding=encoding, newline="")
    except UnicodeEncodeError as exc:
        raise ReconstructionError(f"Caractère incompatible avec l'encodage {encoding}: {exc}")


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
    # Relire avant le remplacement pour détecter immédiatement une écriture invalide.
    atomic_write_bytes(path, payload, validator=load)


def _apply_file(
    target_root: Path,
    relative: str,
    items: list[PlanItem],
    *,
    validation_scope: str = "",
) -> None:
    path = _resolve_group_path(target_root, relative, items)
    if relative.lower().endswith(".rxdata"):
        root = load(path)
        if validation_scope == V21_1_COMMON_EVENTS_VALIDATION_SCOPE:
            if not isinstance(root, list):
                raise ReconstructionError("Table RPG::CommonEvent v21.1 attendue")
            _apply_v21_1_common_event_items(root, items)
        elif validation_scope == V21_1_MAP_VALIDATION_SCOPE:
            if not isinstance(root, RubyObject) or root.class_name != "RPG::Map":
                raise ReconstructionError("Carte RPG::Map v21.1 attendue")
            _apply_v21_1_map_items(root, items)
        else:
            if not isinstance(root, RubyObject) or root.class_name != "RPG::Map":
                raise ReconstructionError(
                    "Seules les cartes RPG::Map sont modifiées en v1.0.2"
                )
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


def _expected_v21_1_message_bank_payload(
    source_root: Path,
    item: PlanItem,
) -> bytes:
    """Construit en mémoire l'unique fichier exact autorisé par la validation."""
    path = _resolve_contained_path(source_root, V21_1_VALIDATION_FILE)
    root = load(path)
    if not isinstance(root, (list, dict)):
        raise ReconstructionError("Banque v21.1 Array ou Hash attendue")
    _apply_bank_items(root, V21_1_VALIDATION_FILE, [item])
    payload = dumps(root)
    if not payload.startswith(b"\x04\x08"):
        raise ReconstructionError("Candidat Marshal v21.1 invalide")
    return payload


def _expected_v21_1_private_payloads(
    source_root: Path,
    plan: ReconstructionPlan,
    validation_items: list[PlanItem],
) -> dict[str, bytes]:
    """Calcule chaque fichier privé attendu sans écrire sur disque."""
    by_file: dict[str, list[PlanItem]] = defaultdict(list)
    for item in validation_items:
        by_file[item.fichier].append(item)
    expected: dict[str, bytes] = {}
    for relative, items in by_file.items():
        path = _resolve_contained_path(source_root, relative)
        root = load(path)
        if relative.lower().endswith(".rxdata"):
            if plan.validation_scope == V21_1_COMMON_EVENTS_VALIDATION_SCOPE:
                if not isinstance(root, list):
                    raise ReconstructionError("Table RPG::CommonEvent v21.1 attendue")
                _apply_v21_1_common_event_items(root, items)
            elif (
                plan.validation_scope == V21_1_MAP_VALIDATION_SCOPE
                and isinstance(root, RubyObject)
                and root.class_name == "RPG::Map"
            ):
                _apply_v21_1_map_items(root, items)
            else:
                raise ReconstructionError("Carte RPG::Map v21.1 attendue")
        elif relative.lower().endswith(".dat"):
            if not isinstance(root, (list, dict)):
                raise ReconstructionError("Banque v21.1 Array ou Hash attendue")
            _apply_bank_items(root, relative, items)
        else:
            raise ReconstructionError("Format privé v21.1 inattendu")
        payload = dumps(root)
        if not payload.startswith(b"\x04\x08"):
            raise ReconstructionError("Candidat Marshal v21.1 invalide")
        expected[relative] = payload
    return expected


def _validate_file(target_root: Path, relative: str, items: list[PlanItem]) -> list[str]:
    path = _resolve_group_path(target_root, relative, items)
    expected = {item.id_stable: item.translation for item in items}
    if relative.lower().endswith(".rxdata"):
        if items and items[0].type == "Événement commun — Dialogue":
            extracted = extract_common_events(path, relative, strict=True)
        else:
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
    validation_detection = None
    if plan.validation_scope:
        if plan.validation_scope not in V21_1_PRIVATE_VALIDATION_SCOPES:
            raise ReconstructionError("Portée de validation privée inconnue.")
        validation_detection = _require_v21_1_validation(game_root)
        _validate_v21_1_private_scope(plan, validation_detection)
    by_file: dict[str, list[PlanItem]] = defaultdict(list)
    for item in plan.items:
        if item.decision == "applicable":
            by_file[item.fichier].append(item)

    for relative, items in by_file.items():
        try:
            path = _resolve_group_path(game_root, relative, items)
            if relative.lower().endswith(".rxdata"):
                root = load(path)
                if plan.validation_scope == V21_1_COMMON_EVENTS_VALIDATION_SCOPE:
                    if not isinstance(root, list):
                        raise ReconstructionError("Table RPG::CommonEvent v21.1 attendue")
                    _apply_v21_1_common_event_items(root, items)
                    continue
                if not isinstance(root, RubyObject) or root.class_name != "RPG::Map":
                    raise ReconstructionError("Carte RPG::Map attendue")
                if plan.validation_scope == V21_1_MAP_VALIDATION_SCOPE:
                    _apply_v21_1_map_items(root, items)
                    continue
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
    if validation_detection is not None:
        _validate_v21_1_private_scope(plan, validation_detection)
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
            _mark_incomplete_copy(target, "la copie initiale")
        raise


def _mark_incomplete_copy(target_root: Path, reason: str) -> str:
    """Marque une copie invalide sans masquer l'erreur métier d'origine."""
    marker = target_root / "RECONSTRUCTION_INCOMPLETE.txt"
    try:
        atomic_write_text(
            marker,
            "Cette copie est incomplète et ne doit pas être utilisée.\n"
            f"Étape en échec : {reason}.\n"
            "Consultez le rapport du projet puis supprimez ce dossier.\n",
            encoding="utf-8",
        )
    except Exception as exc:
        return f" Le marqueur d'échec n'a pas pu être écrit ({type(exc).__name__})."
    return ""


def _write_final_artifact(
    path: Path,
    content: str,
    *,
    target_root: Path,
    label: str,
    newline: str | None = None,
) -> None:
    """Écrit un résultat final ou invalide explicitement la copie."""
    try:
        atomic_write_text(path, content, encoding="utf-8", newline=newline)
    except Exception as exc:
        marker_warning = _mark_incomplete_copy(target_root, label)
        raise ReconstructionError(
            f"Finalisation impossible pendant {label}. La copie est marquée incomplète."
            f"{marker_warning}"
        ) from exc


def _reconstruct_copy_verified_body(
    plan: ReconstructionPlan,
    target_root: Path,
    report_dir: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> ReconstructionResult:
    source_root = _resolve_safe_game_root(Path(plan.game_root))
    validation_items: list[PlanItem] = []
    if plan.validation_scope:
        if plan.validation_scope not in V21_1_PRIVATE_VALIDATION_SCOPES:
            raise ReconstructionError("Portée de validation privée inconnue.")
        detection = _require_v21_1_validation(source_root)
        validation_items = _validate_v21_1_private_scope(plan, detection)
    else:
        detection = _require_essentials_reconstruction(source_root)
    if plan.adapter_id != detection.adapter_id:
        raise ReconstructionError(
            "Le plan ne correspond plus à l'adaptateur détecté. Relancez la simulation."
        )
    csv_path = Path(plan.csv_path).expanduser().resolve()
    if not plan.csv_sha256 or not csv_path.is_file() or sha256_file(csv_path) != plan.csv_sha256:
        raise ReconstructionError(
            "Le CSV a changé depuis la simulation. Relancez la simulation avant toute copie."
        )
    try:
        identity = read_project_identity(
            csv_path,
            source_root,
            expected_adapter_id=detection.adapter_id,
            require_extraction_provenance=True,
        )
    except ProjectIdentityError as exc:
        raise ReconstructionError(f"Projet de traduction refusé : {exc}") from exc
    if validation_items and identity.adapter_profile != detection.structural_profile:
        raise ReconstructionError(
            "Projet de validation refusé : le profil Essentials de l'identité a changé."
        )
    if (
        not plan.project_identity_sha256
        or identity.sha256 != plan.project_identity_sha256
    ):
        raise ReconstructionError(
            "L'identité du projet a changé depuis la simulation. Relancez la simulation."
        )
    _assert_reserved_copy_outputs_absent(source_root)
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
    expected_validation_payloads = (
        _expected_v21_1_private_payloads(source_root, plan, validation_items)
        if validation_items
        else {}
    )
    if progress:
        progress(0, 1, "Calcul de l'empreinte du fangame original…")
    source_before = _integrity_snapshot(source_root, "le fangame original")
    _copy_game(source_root, target_root, progress=(lambda message: progress(0, 1, message) if progress else None))

    by_file: dict[str, list[PlanItem]] = defaultdict(list)
    for item in applicable:
        by_file[item.fichier].append(item)

    modified_files: list[str] = []
    validation_errors: list[str] = []
    applied = 0
    original_integrity: SnapshotComparison | None = None
    copy_integrity: SnapshotComparison | None = None
    try:
        total_files = len(by_file)
        for file_index, (relative, items) in enumerate(sorted(by_file.items()), start=1):
            if progress:
                progress(file_index, total_files, f"Réinjection : {relative}")
            if plan.validation_scope:
                _apply_file(
                    target_root,
                    relative,
                    items,
                    validation_scope=plan.validation_scope,
                )
            else:
                _apply_file(target_root, relative, items)
            errors = _validate_file(target_root, relative, items)
            if errors:
                validation_errors.extend(errors)
                raise ReconstructionError(errors[0])
            if relative in expected_validation_payloads:
                rebuilt = read_stable_bytes(
                    _resolve_contained_path(target_root, relative)
                )
                if rebuilt != expected_validation_payloads[relative]:
                    raise ReconstructionError(
                        "Le fichier v21.1 reconstruit diffère du candidat exact calculé "
                        "en mémoire ; la copie est refusée."
                    )
            modified_files.append(relative)
            applied += len(items)
        # Une seconde vérification détecte toute modification des fichiers
        # originaux pendant la reconstruction avant d'annoncer un succès.
        _assert_plan_sources_unchanged(plan, source_root)
        if progress:
            progress(total_files, total_files, "Contrôle d'intégrité complet…")
        source_after = _integrity_snapshot(source_root, "le fangame original")
        copy_after = _integrity_snapshot(target_root, "la copie française")
        original_integrity = compare_snapshots(source_before, source_after)
        copy_integrity = compare_snapshots(
            source_before,
            copy_after,
            allowed_changed=by_file,
        )
        if not original_integrity.passed:
            raise ReconstructionError(
                _integrity_failure("le fangame original", original_integrity)
            )
        if not copy_integrity.passed:
            raise ReconstructionError(
                _integrity_failure("la copie française", copy_integrity)
            )
    except Exception:
        # Une copie incomplète ne doit jamais sembler utilisable.
        _mark_incomplete_copy(target_root, "validation ou contrôle d'intégrité")
        raise

    original_unchanged = bool(original_integrity and original_integrity.passed)
    integrity_valid = bool(copy_integrity and copy_integrity.passed and original_unchanged)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest_path = report_dir / f"MANIFESTE_RECONSTRUCTION_{timestamp}.json"
    manifest = {
        "version": "1.0",
        "date": datetime.now().isoformat(timespec="seconds"),
        "jeu_original": str(source_root),
        "copie_francaise": str(target_root),
        "csv": plan.csv_path,
        "mode": plan.mode,
        "profil_adaptateur": plan.adapter_profile,
        "portee_validation": plan.validation_scope,
        "original_inchange": original_unchanged,
        "fichiers_modifies": modified_files,
        "hachages_originaux": plan.source_hashes,
        "hachages_copie": {
            relative: sha256_file(_resolve_contained_path(target_root, relative))
            for relative in modified_files
        },
        "traductions_appliquees": applied,
        "validation_erreurs": validation_errors,
        "controle_integrite": {
            "statut": "valide" if integrity_valid else "invalide",
            "fichiers_source": source_before.file_count,
            "octets_source": source_before.total_size,
            "original": original_integrity.to_manifest() if original_integrity else {},
            "copie": copy_integrity.to_manifest() if copy_integrity else {},
            "fichiers_generes_apres_controle": [
                "PFT_RECONSTRUCTION_V1.0.txt",
                "LIRE_AVANT_DE_JOUER.txt",
                "LANCER_VERSION_FR.bat",
            ],
        },
    }
    _write_final_artifact(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2),
        target_root=target_root,
        label="l'écriture du manifeste",
    )

    report_path = report_dir / f"RAPPORT_RECONSTRUCTION_{timestamp}.txt"
    counts = plan.counts()
    _write_final_artifact(report_path, "\n".join([
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
        f"Contrôle d'intégrité complet : {'VALIDE' if integrity_valid else 'INVALIDE'}",
        f"Fichiers source contrôlés : {source_before.file_count}",
        f"Fichiers non ciblés modifiés : {len(copy_integrity.changed_files) if copy_integrity else 0}",
        f"Fichiers manquants : {len(copy_integrity.missing_files) if copy_integrity else 0}",
        f"Fichiers devenus vides : {len(copy_integrity.emptied_files) if copy_integrity else 0}",
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
    ]), target_root=target_root, label="l'écriture du rapport")

    _write_final_artifact(
        target_root / "PFT_RECONSTRUCTION_V1.0.txt",
        "Cette copie a été créée par Pokémon Fangame Translator v1.0.2.\n"
        f"Traductions appliquées : {applied}\n"
        f"Rapport : {report_path}\n"
        "Le dossier original n'a pas été modifié.\n",
        target_root=target_root,
        label="l'écriture de l'attestation de reconstruction",
    )


    _write_final_artifact(
        target_root / "LIRE_AVANT_DE_JOUER.txt",
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
        target_root=target_root,
        label="l'écriture du guide de la copie",
    )

    _write_final_artifact(
        target_root / "LANCER_VERSION_FR.bat",
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "if not exist \"Game.exe\" (\r\n"
        "  echo Game.exe est introuvable dans ce dossier.\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "start \"\" \"Game.exe\"\r\n",
        target_root=target_root,
        label="l'écriture du lanceur",
        newline="",
    )

    return ReconstructionResult(
        target_root=str(target_root),
        applied=applied,
        skipped=counts.get("skipped", 0),
        blocked=counts.get("blocked", 0),
        modified_files=modified_files,
        validation_errors=validation_errors,
        original_unchanged=original_unchanged,
        integrity_valid=integrity_valid,
        report_path=str(report_path),
        manifest_path=str(manifest_path),
    )


def reconstruct_copy(
    plan: ReconstructionPlan,
    target_root: Path,
    report_dir: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> ReconstructionResult:
    """Garde le projet verrouillé et identique pendant toute la reconstruction."""
    source_root = _resolve_safe_game_root(Path(plan.game_root))
    csv_path = Path(plan.csv_path).expanduser()
    try:
        guard = open_verified_project(
            csv_path,
            game_root=source_root,
            expected_adapter_id=ESSENTIALS_ADAPTER_ID,
        )
    except TranslationProjectError as exc:
        raise ReconstructionError(f"Projet de traduction refusé : {exc}") from exc
    try:
        assert guard.snapshot is not None
        if (
            not plan.project_provenance_token
            or guard.snapshot.provenance_token() != plan.project_provenance_token
        ):
            raise ReconstructionError(
                "La provenance du projet a changé depuis la simulation. "
                "Relancez la simulation avant toute copie."
            )
        return _reconstruct_copy_verified_body(
            plan,
            target_root,
            report_dir,
            progress=progress,
        )
    finally:
        guard.close()


def save_plan(plan: ReconstructionPlan, path: Path) -> None:
    payload = asdict(plan)
    payload["counts"] = plan.counts()
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
