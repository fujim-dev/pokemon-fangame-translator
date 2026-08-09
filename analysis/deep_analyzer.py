from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Callable

from ruby_marshal_reader import RubyObject, load
from structured_extractor import (
    extract_message_bank,
    extract_pbs,
    iter_pbs_files,
    looks_visible,
    text_value,
)

from .language_coverage import calculate_coverage
from .models import AnalysisIssue, DeepAnalysisReport


ProgressCallback = Callable[[int, int, str], None]
MAP_NAME_RE = re.compile(r"Map\d{3,4}\.rxdata", re.I)
RESOURCE_EXTENSIONS = {
    "audio": (".ogg", ".mp3", ".wav", ".mid", ".midi", ".wma"),
    "picture": (".png", ".jpg", ".jpeg", ".bmp"),
}


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return False


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _add_issue(
    report: DeepAnalysisReport,
    code: str,
    severity: str,
    category: str,
    message: str,
    relative_path: str = "",
    *,
    blocking: bool = False,
) -> None:
    issue = AnalysisIssue(code, severity, category, message, relative_path, blocking)
    if issue not in report.issues:
        report.issues.append(issue)


def _inventory(root: Path, report: DeepAnalysisReport) -> None:
    extensions: Counter[str] = Counter()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError:
            _add_issue(
                report,
                "unreadable_directory",
                "error",
                "integrity",
                "Dossier illisible pendant l'inventaire.",
                _relative(directory, root),
                blocking=True,
            )
            continue
        for entry in entries:
            path = Path(entry.path)
            relative = _relative(path, root)
            if _is_link_or_junction(path):
                _add_issue(
                    report,
                    "filesystem_link",
                    "warning",
                    "security",
                    "Lien symbolique ou jonction ignoré sans être suivi.",
                    relative,
                )
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    report.files_seen += 1
                    try:
                        report.bytes_seen += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        _add_issue(
                            report,
                            "unreadable_file_metadata",
                            "warning",
                            "integrity",
                            "Métadonnées du fichier illisibles.",
                            relative,
                        )
                    extension = path.suffix.casefold() or "[sans extension]"
                    extensions[extension] += 1
                    if extension == ".rb":
                        report.ruby_script_files += 1
            except OSError:
                _add_issue(
                    report,
                    "unreadable_file_metadata",
                    "warning",
                    "integrity",
                    "Type du fichier impossible à déterminer.",
                    relative,
                )
    report.extension_counts = dict(sorted(extensions.items()))


def _check_expected_essentials_files(root: Path, report: DeepAnalysisReport) -> None:
    for relative in ("Game.exe", "Game.ini"):
        path = root / relative
        if not path.is_file() or _is_link_or_junction(path):
            _add_issue(
                report,
                "missing_expected_file",
                "warning",
                "integrity",
                "Fichier attendu par la structure Essentials classique absent.",
                relative,
            )
    data = root / "Data"
    if not data.is_dir() or _is_link_or_junction(data):
        _add_issue(
            report,
            "missing_expected_directory",
            "error",
            "integrity",
            "Dossier Data compatible absent ou redirigé.",
            "Data/",
            blocking=True,
        )
        return
    if not any(
        path.is_file() and not _is_link_or_junction(path)
        for path in (data / "System.rxdata", data / "MapInfos.rxdata")
    ):
        _add_issue(
            report,
            "missing_expected_file",
            "error",
            "integrity",
            "Base RPG Maker XP attendue absente.",
            "Data/System.rxdata ou Data/MapInfos.rxdata",
            blocking=True,
        )


def _safe_resource_relative(base: str, name: str) -> str | None:
    normalized = (name or "").strip().replace("\\", "/")
    windows = PureWindowsPath(normalized)
    parts = tuple(normalized.split("/"))
    if (
        not normalized
        or normalized.startswith("/")
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return None
    return "/".join((base, *parts))


def _resource_exists(root: Path, relative: str, extensions: tuple[str, ...]) -> bool:
    candidate = root.joinpath(*relative.split("/"))
    if candidate.suffix:
        return candidate.is_file() and not _is_link_or_junction(candidate)
    return any(
        candidate.with_suffix(extension).is_file()
        and not _is_link_or_junction(candidate.with_suffix(extension))
        for extension in extensions
    )


def _check_resource(
    report: DeepAnalysisReport,
    root: Path,
    kind: str,
    base: str,
    name: str,
    seen_references: set[tuple[str, str]],
) -> None:
    relative = _safe_resource_relative(base, name)
    if relative is None:
        _add_issue(
            report,
            "unsafe_static_reference",
            "warning",
            "reference",
            "Référence statique invalide ou non relative.",
            base,
        )
        return
    key = (kind, relative.casefold())
    if key in seen_references:
        return
    seen_references.add(key)
    report.static_references_checked += 1
    if not _resource_exists(root, relative, RESOURCE_EXTENSIONS[kind]):
        report.missing_static_references += 1
        _add_issue(
            report,
            "missing_static_reference",
            "warning",
            "reference",
            "Ressource statique référencée mais absente.",
            relative,
        )


def _inspect_commands(
    commands: list,
    report: DeepAnalysisReport,
    root: Path,
    texts: list[str],
    seen_references: set[tuple[str, str]],
) -> None:
    report.event_commands += sum(isinstance(command, RubyObject) for command in commands)
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
                first = text_value(params[0]).strip()
                if first:
                    pieces.append(first)
            cursor = index + 1
            while cursor < len(commands):
                continuation = commands[cursor]
                if not isinstance(continuation, RubyObject) or continuation.ivars.get("@code") != 401:
                    break
                continuation_params = continuation.ivars.get("@parameters", [])
                if isinstance(continuation_params, list) and continuation_params:
                    piece = text_value(continuation_params[0]).strip()
                    if piece:
                        pieces.append(piece)
                cursor += 1
            message = " ".join(pieces).strip()
            if looks_visible(message):
                texts.append(message)
            index = cursor
            continue
        if code == 102 and isinstance(params, list) and params and isinstance(params[0], list):
            texts.extend(
                choice_text
                for choice in params[0]
                if (choice_text := text_value(choice).strip()) and looks_visible(choice_text)
            )
        if code in {355, 655}:
            report.dynamic_script_commands += 1
        if code in {241, 245, 249, 250} and isinstance(params, list) and params:
            audio = params[0]
            if isinstance(audio, RubyObject):
                name = text_value(audio.ivars.get("@name")).strip()
                audio_folder = {241: "BGM", 245: "BGS", 249: "ME", 250: "SE"}[code]
                if name:
                    _check_resource(
                        report, root, "audio", f"Audio/{audio_folder}", name, seen_references
                    )
        if code == 231 and isinstance(params, list) and len(params) > 1:
            name = text_value(params[1]).strip()
            if name:
                _check_resource(
                    report, root, "picture", "Graphics/Pictures", name, seen_references
                )
        if code == 201 and isinstance(params, list) and len(params) > 1 and params[0] == 0:
            map_id = params[1]
            if isinstance(map_id, int) and map_id > 0:
                relative = f"Data/Map{map_id:03d}.rxdata"
                key = ("map", relative.casefold())
                if key not in seen_references:
                    seen_references.add(key)
                    report.static_references_checked += 1
                    path = root / "Data" / f"Map{map_id:03d}.rxdata"
                    if not path.is_file() or _is_link_or_junction(path):
                        report.missing_static_references += 1
                        _add_issue(
                            report,
                            "missing_static_reference",
                            "warning",
                            "reference",
                            "Carte ciblée par un transfert statique mais absente.",
                            relative,
                        )
        index += 1


def _analyze_map(
    path: Path,
    report: DeepAnalysisReport,
    root: Path,
    texts: list[str],
    seen_references: set[tuple[str, str]],
) -> None:
    relative = _relative(path, root)
    if _is_link_or_junction(path):
        return
    try:
        loaded = load(path)
    except Exception:
        _add_issue(
            report,
            "unreadable_file",
            "error",
            "integrity",
            "Carte illisible ou format Marshal invalide.",
            relative,
            blocking=True,
        )
        return
    if not isinstance(loaded, RubyObject) or loaded.class_name != "RPG::Map":
        _add_issue(
            report,
            "unsupported_map",
            "warning",
            "compatibility",
            "Le fichier ne contient pas une carte RPG::Map reconnue.",
            relative,
        )
        return
    report.maps_analyzed += 1
    events = loaded.ivars.get("@events", {})
    if not isinstance(events, dict):
        _add_issue(
            report,
            "invalid_map_structure",
            "error",
            "integrity",
            "La table des événements de la carte est invalide.",
            relative,
            blocking=True,
        )
        return
    for event in events.values():
        if not isinstance(event, RubyObject):
            continue
        report.map_events += 1
        pages = event.ivars.get("@pages", [])
        if not isinstance(pages, list):
            continue
        for page in pages:
            if not isinstance(page, RubyObject):
                continue
            report.map_pages += 1
            commands = page.ivars.get("@list", [])
            if isinstance(commands, list):
                _inspect_commands(commands, report, root, texts, seen_references)


def _analyze_common_events(
    path: Path,
    report: DeepAnalysisReport,
    root: Path,
    texts: list[str],
    seen_references: set[tuple[str, str]],
) -> None:
    if not path.is_file() or _is_link_or_junction(path):
        return
    try:
        loaded = load(path)
    except Exception:
        _add_issue(
            report,
            "unreadable_file",
            "error",
            "integrity",
            "Événements communs illisibles ou format Marshal invalide.",
            _relative(path, root),
            blocking=True,
        )
        return
    if not isinstance(loaded, list):
        _add_issue(
            report,
            "unsupported_common_events",
            "warning",
            "compatibility",
            "Structure des événements communs non reconnue.",
            _relative(path, root),
        )
        return
    unsupported_entries = [
        event
        for event in loaded
        if event is not None
        and (not isinstance(event, RubyObject) or event.class_name != "RPG::CommonEvent")
    ]
    if unsupported_entries:
        _add_issue(
            report,
            "unsupported_common_event_entry",
            "warning",
            "compatibility",
            "Une ou plusieurs entrées CommonEvents ne sont pas extractibles avec certitude.",
            _relative(path, root),
        )
    common_events = [
        event
        for event in loaded
        if isinstance(event, RubyObject) and event.class_name == "RPG::CommonEvent"
    ]
    report.common_events_found = len(common_events)
    for event in common_events:
        commands = event.ivars.get("@list", [])
        if isinstance(commands, list):
            report.common_events_analyzed += 1
            _inspect_commands(commands, report, root, texts, seen_references)


def analyze_game(
    root: Path,
    *,
    adapter_id: str,
    adapter_display_name: str,
    adapter_confidence: int,
    mode: str = "complete",
    progress: ProgressCallback | None = None,
) -> DeepAnalysisReport:
    """Analyse statiquement les données reconnues sans exécuter de code du jeu."""
    if mode not in {"quick", "complete", "deep"}:
        raise ValueError(f"Mode d'analyse inconnu : {mode}")
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Le dossier du fangame est introuvable.")
    report = DeepAnalysisReport(
        game_label=root.name,
        adapter_id=adapter_id,
        adapter_display_name=adapter_display_name,
        adapter_confidence=adapter_confidence,
        mode=mode,
    )
    _inventory(root, report)
    if adapter_id == "pokemon_essentials":
        _check_expected_essentials_files(root, report)

    scripts = root / "Data" / "Scripts.rxdata"
    plugin_scripts = root / "Data" / "PluginScripts.rxdata"
    for script_bank in (scripts, plugin_scripts):
        if script_bank.is_file() and not _is_link_or_junction(script_bank):
            report.unverified.append(
                f"{_relative(script_bank, root)} présent : contenu Ruby non exécuté et non validé."
            )
    if report.ruby_script_files:
        report.unverified.append(
            f"{report.ruby_script_files} fichier(s) Ruby .rb signalé(s), jamais exécuté(s)."
        )
    encrypted = [
        path.name
        for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in {".rgssad", ".rgss2a", ".rgss3a"}
    ]
    if encrypted:
        report.unsupported.append(
            f"{len(encrypted)} archive(s) RPG Maker chiffrée(s) non analysée(s)."
        )
    if adapter_id == "unknown":
        report.unsupported.append(
            "Structure inconnue : inventaire autorisé, interprétation et écriture bloquées."
        )

    data = root / "Data"
    structured_reading_allowed = adapter_id == "pokemon_essentials"
    maps = (
        sorted(
            (path for path in data.glob("Map*.rxdata") if MAP_NAME_RE.fullmatch(path.name)),
            key=lambda path: path.name.casefold(),
        )
        if structured_reading_allowed and data.is_dir() and not _is_link_or_junction(data)
        else []
    )
    report.map_files_found = len(maps)
    pbs = root / "PBS"
    pbs_files = (
        list(iter_pbs_files(pbs) or [])
        if structured_reading_allowed and not _is_link_or_junction(pbs)
        else []
    )
    report.pbs_files_found = len(pbs_files)
    banks = [
        data / name
        for name in ("messages.dat", "messages_game.dat", "messages_core.dat")
        if structured_reading_allowed
        and (data / name).is_file()
        and not _is_link_or_junction(data / name)
    ]
    report.message_banks_found = len(banks)
    common_events_path = data / "CommonEvents.rxdata"
    common_events_available = (
        structured_reading_allowed
        and common_events_path.is_file()
        and not _is_link_or_junction(common_events_path)
    )
    tasks_total = max(1, len(maps) + len(pbs_files) + len(banks) + int(common_events_available))
    task_index = 0
    texts: list[str] = []
    extractable_by_source: Counter[str] = Counter()
    seen_references: set[tuple[str, str]] = set()

    for path in maps:
        task_index += 1
        if progress:
            progress(task_index, tasks_total, _relative(path, root))
        before_count = len(texts)
        _analyze_map(path, report, root, texts, seen_references)
        extracted_count = len(texts) - before_count
        if extracted_count:
            extractable_by_source["maps"] += extracted_count

    if common_events_available:
        task_index += 1
        if progress:
            progress(task_index, tasks_total, _relative(common_events_path, root))
        before_count = len(texts)
        _analyze_common_events(
            common_events_path, report, root, texts, seen_references
        )
        extracted_count = len(texts) - before_count
        if extracted_count:
            extractable_by_source["common_events"] += extracted_count

    for path in banks:
        task_index += 1
        relative = _relative(path, root)
        if progress:
            progress(task_index, tasks_total, relative)
        try:
            rows = extract_message_bank(path, relative)
        except Exception:
            _add_issue(
                report,
                "unreadable_file",
                "error",
                "integrity",
                "Banque de messages illisible ou format Marshal invalide.",
                relative,
                blocking=True,
            )
            continue
        report.message_banks_analyzed += 1
        extracted_texts = [
            (row.get("traduction_fr") or row.get("texte_source") or "").strip()
            for row in rows
            if (row.get("traduction_fr") or row.get("texte_source") or "").strip()
        ]
        texts.extend(extracted_texts)
        extractable_by_source["message_banks"] += len(extracted_texts)

    for path in pbs_files:
        task_index += 1
        relative = _relative(path, root)
        if progress:
            progress(task_index, tasks_total, relative)
        if _is_link_or_junction(path):
            continue
        try:
            raw_pbs = path.read_bytes()
            raw_pbs.decode("utf-8-sig")
        except UnicodeDecodeError:
            report.pbs_legacy_encoding_files += 1
            _add_issue(
                report,
                "legacy_text_encoding",
                "warning",
                "encoding",
                "Encodage historique CP1252 détecté ; lecture effectuée sans réécriture.",
                relative,
            )
        except OSError:
            raw_pbs = b""
        try:
            rows = extract_pbs(path, relative)
        except Exception:
            _add_issue(
                report,
                "unreadable_file",
                "error",
                "integrity",
                "Fichier PBS illisible.",
                relative,
                blocking=True,
            )
            continue
        report.pbs_files_analyzed += 1
        extracted_texts = [
            (row.get("texte_source") or "").strip()
            for row in rows
            if (row.get("texte_source") or "").strip()
        ]
        texts.extend(extracted_texts)
        extractable_by_source["pbs"] += len(extracted_texts)

    if report.dynamic_script_commands:
        report.unverified.append(
            f"{report.dynamic_script_commands} commande(s) de script dynamique signalée(s), jamais exécutée(s)."
        )
    if not report.unverified:
        report.verified.append("Aucun script Ruby ou appel dynamique accessible n'a été exécuté.")
    report.verified.extend(
        [
            f"{report.maps_analyzed}/{report.map_files_found} carte(s) compatible(s) relue(s).",
            f"{report.map_pages} page(s) d'événements comptée(s), conditions incluses.",
            f"{report.common_events_analyzed}/{report.common_events_found} événement(s) commun(s) relu(s).",
            f"{report.message_banks_analyzed}/{report.message_banks_found} banque(s) de messages relue(s).",
            f"{report.pbs_files_analyzed}/{report.pbs_files_found} fichier(s) PBS relu(s).",
            f"{report.static_references_checked} référence(s) statique(s) contrôlée(s).",
        ]
    )
    incomplete_sources = bool(
        report.unreadable_files
        or report.unverified
        or report.unsupported
        or any(issue.code == "filesystem_link" for issue in report.issues)
    )
    report.extractable_text_occurrences = len(texts)
    report.extractable_unique_texts = len(set(texts))
    report.extractable_by_source = dict(sorted(extractable_by_source.items()))
    report.coverage = calculate_coverage(texts, incomplete_sources=incomplete_sources)
    return report
