# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation analytique de Pokémon Flux sans exécuter le jeu ni Ruby."""
from __future__ import annotations

import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Callable

from flux_archive import FluxArchiveReader
from flux_extractor import FluxExtractionError, collect_flux_occurrences
from ruby_marshal_reader import RubyHashKey, RubyObject, RubyUserDefined, load
from structured_extractor import text_value

from .language_coverage import calculate_coverage
from .models import AnalysisIssue, DeepAnalysisReport

if TYPE_CHECKING:
    from adapters.base import DetectionResult


ProgressCallback = Callable[[int, int, str], None]
MAP_NAME_RE = re.compile(r"Map\d{3,4}\.rxdata", re.I)
RUBY_RESOURCE_PATH_RE = re.compile(
    r'''(?P<quote>["'])(?P<path>(?:Audio|Graphics)[\\/][^"'\r\n]+)(?P=quote)''',
    re.I,
)
RUBY_AUDIO_CALL_RE = re.compile(
    r'''\bpb(?P<kind>BGM|BGS|ME|SE)Play\s*\(\s*(?P<quote>["'])(?P<name>[^"'\r\n]+)(?P=quote)''',
    re.I,
)
AUDIO_EXTENSIONS = (".ogg", ".mp3", ".wav", ".mid", ".midi", ".wma")
GRAPHICS_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")


@dataclass(frozen=True)
class _ResourceCatalog:
    audio_paths: frozenset[str]
    graphics_paths: frozenset[str]
    audio_available: bool
    graphics_available: bool


def _issue(
    report: DeepAnalysisReport,
    code: str,
    severity: str,
    category: str,
    message: str,
    relative_path: str = "",
    *,
    blocking: bool = False,
) -> None:
    value = AnalysisIssue(code, severity, category, message, relative_path, blocking)
    if value not in report.issues:
        report.issues.append(value)


def _locate_fpk(root: Path) -> Path:
    candidates = [
        path
        for path in (root / "Data" / "Data_0.fpk", root / "Data_0.fpk")
        if path.is_file()
    ]
    if len(candidates) != 1:
        raise ValueError("Le profil Flux ne contient pas un unique Data_0.fpk.")
    return candidates[0]


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return False


def _safe_resource_relative(base: str, name: str) -> str | None:
    normalized = (name or "").strip().replace("\\", "/")
    windows = PureWindowsPath(normalized)
    parts = tuple(normalized.split("/"))
    if (
        not normalized
        or normalized.startswith("/")
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return None
    return "/".join((base, *parts))


def _inventory_audio(root: Path, report: DeepAnalysisReport) -> tuple[frozenset[str], bool]:
    audio_root = root / "Audio"
    if not audio_root.is_dir() or _is_link_or_junction(audio_root):
        report.unverified.append("Le dossier Audio externe est absent ou redirigé.")
        return frozenset(), False
    paths: set[str] = set()
    pending = [audio_root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            _issue(
                report,
                "unreadable_external_resources",
                "warning",
                "reference",
                "Un dossier Audio externe est illisible.",
                "Audio/",
            )
            return frozenset(paths), False
        for path in entries:
            if _is_link_or_junction(path):
                _issue(
                    report,
                    "external_resource_link",
                    "warning",
                    "security",
                    "Lien ou jonction externe ignoré sans être suivi.",
                    path.relative_to(root).as_posix(),
                )
                continue
            try:
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    paths.add(path.relative_to(root).as_posix().casefold())
            except OSError:
                continue
    report.verified.append(f"Audio externe inventorié en lecture seule : {len(paths)} fichier(s).")
    return frozenset(paths), True


def _external_resource_catalog(
    root: Path,
    reader: FluxArchiveReader,
    report: DeepAnalysisReport,
) -> _ResourceCatalog:
    audio_paths, audio_available = _inventory_audio(root, report)
    graphics_fpk = root / "Graphics" / "Assets_0.fpk"
    graphics_paths: frozenset[str] = frozenset()
    graphics_available = False
    if not graphics_fpk.is_file() or _is_link_or_junction(graphics_fpk):
        report.unverified.append("L'archive graphique externe Graphics/Assets_0.fpk est absente ou redirigée.")
    else:
        try:
            graphics_inventory = reader.inspect(graphics_fpk)
        except Exception as exc:
            _issue(
                report,
                "unreadable_graphics_archive",
                "warning",
                "reference",
                f"L'archive graphique externe ne peut pas être inventoriée ({type(exc).__name__}).",
                "Graphics/Assets_0.fpk",
            )
        else:
            if not graphics_inventory.safe:
                for problem in graphics_inventory.issues:
                    _issue(
                        report,
                        "unsafe_graphics_archive",
                        "warning",
                        "security",
                        problem,
                        "Graphics/Assets_0.fpk",
                    )
            else:
                graphics_paths = frozenset(
                    entry.normalized_path.casefold()
                    for entry in graphics_inventory.file_entries
                )
                graphics_available = True
                report.verified.append(
                    "Archive graphique externe inventoriée sans extraction : "
                    f"{len(graphics_paths)} fichier(s)."
                )
    return _ResourceCatalog(
        audio_paths=audio_paths,
        graphics_paths=graphics_paths,
        audio_available=audio_available,
        graphics_available=graphics_available,
    )


def _resource_exists(paths: frozenset[str], relative: str, extensions: tuple[str, ...]) -> bool:
    key = relative.casefold()
    if PurePosixPath(relative).suffix:
        return key in paths
    return any(f"{key}{extension}" in paths for extension in extensions)


def _check_resource(
    report: DeepAnalysisReport,
    catalog: _ResourceCatalog,
    kind: str,
    base: str,
    name: str,
    seen_references: set[tuple[str, str]],
) -> None:
    relative = _safe_resource_relative(base, name)
    if relative is None:
        _issue(
            report,
            "unsafe_static_reference",
            "warning",
            "reference",
            "Référence statique externe invalide ou non relative.",
            base,
        )
        return
    key = (kind, relative.casefold())
    if key in seen_references:
        return
    seen_references.add(key)
    if kind == "audio":
        available = catalog.audio_available
        exists = _resource_exists(catalog.audio_paths, relative, AUDIO_EXTENSIONS)
    else:
        available = catalog.graphics_available
        exists = _resource_exists(catalog.graphics_paths, relative, GRAPHICS_EXTENSIONS)
    if not available:
        return
    report.static_references_checked += 1
    if not exists:
        report.missing_static_references += 1
        _issue(
            report,
            "missing_static_reference",
            "warning",
            "reference",
            "Ressource statique externe référencée mais absente.",
            relative,
        )


def _resource_name(value) -> str:
    return text_value(value).strip()


def _check_audio_object(
    value,
    folder: str,
    report: DeepAnalysisReport,
    catalog: _ResourceCatalog,
    seen_references: set[tuple[str, str]],
) -> None:
    if not isinstance(value, RubyObject):
        return
    name = _resource_name(value.ivars.get("@name"))
    if name:
        _check_resource(report, catalog, "audio", f"Audio/{folder}", name, seen_references)


def _inspect_commands(
    commands: list,
    report: DeepAnalysisReport,
    catalog: _ResourceCatalog,
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
            cursor = index + 1
            while cursor < len(commands):
                continuation = commands[cursor]
                if not isinstance(continuation, RubyObject) or continuation.ivars.get("@code") != 401:
                    break
                cursor += 1
            index = cursor
            continue
        if code in {355, 655}:
            report.dynamic_script_commands += 1
        if code in {241, 245, 249, 250, 132, 133} and isinstance(params, list) and params:
            folder = {241: "BGM", 245: "BGS", 249: "ME", 250: "SE", 132: "BGM", 133: "ME"}[code]
            _check_audio_object(params[0], folder, report, catalog, seen_references)
        if code == 231 and isinstance(params, list) and len(params) > 1:
            name = _resource_name(params[1])
            if name:
                _check_resource(
                    report, catalog, "graphics", "Graphics/Pictures", name, seen_references
                )
        if code == 204 and isinstance(params, list) and len(params) > 1:
            folder = {0: "Panoramas", 1: "Fogs", 2: "Battlebacks"}.get(params[0])
            name = _resource_name(params[1])
            if folder and name:
                _check_resource(
                    report, catalog, "graphics", f"Graphics/{folder}", name, seen_references
                )
        if code == 131 and isinstance(params, list) and params:
            name = _resource_name(params[0])
            if name:
                _check_resource(
                    report, catalog, "graphics", "Graphics/Windowskins", name, seen_references
                )
        if code == 222 and isinstance(params, list) and params:
            name = _resource_name(params[0])
            if name:
                _check_resource(
                    report, catalog, "graphics", "Graphics/Transitions", name, seen_references
                )
        if code == 322 and isinstance(params, list):
            for position, folder in ((1, "Characters"), (3, "Battlers")):
                if len(params) > position and (name := _resource_name(params[position])):
                    _check_resource(
                        report, catalog, "graphics", f"Graphics/{folder}", name, seen_references
                    )
        index += 1


def _walk_objects(value, seen: set[int] | None = None):
    """Parcourt une structure Marshal déjà décodée sans en exécuter le contenu."""
    if seen is None:
        seen = set()
    if isinstance(value, RubyHashKey):
        yield from _walk_objects(value.value, seen)
        return
    if isinstance(value, (list, dict, RubyObject, RubyUserDefined)):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
    if isinstance(value, RubyObject):
        yield value
        for child in value.ivars.values():
            yield from _walk_objects(child, seen)
    elif isinstance(value, RubyUserDefined):
        for child in value.ivars.values():
            yield from _walk_objects(child, seen)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child, seen)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key == "__default__":
                continue
            yield from _walk_objects(key, seen)
            yield from _walk_objects(child, seen)


def _check_named_graphic(
    value,
    ivar: str,
    folder: str,
    report: DeepAnalysisReport,
    catalog: _ResourceCatalog,
    seen_references: set[tuple[str, str]],
) -> None:
    if not isinstance(value, RubyObject):
        return
    name = _resource_name(value.ivars.get(ivar))
    if name:
        _check_resource(
            report, catalog, "graphics", f"Graphics/{folder}", name, seen_references
        )


def _inspect_database_resources(
    data: Path,
    loaded: dict[Path, object],
    report: DeepAnalysisReport,
    catalog: _ResourceCatalog,
    seen_references: set[tuple[str, str]],
) -> None:
    system = loaded.get(data / "System.rxdata")
    if isinstance(system, RubyObject):
        for ivar, folder in (
            ("@windowskin_name", "Windowskins"),
            ("@title_name", "Titles"),
            ("@gameover_name", "Titles"),
            ("@battle_transition", "Transitions"),
            ("@battleback_name", "Battlebacks"),
            ("@battler_name", "Battlers"),
        ):
            _check_named_graphic(system, ivar, folder, report, catalog, seen_references)
        for ivar, folder in (
            ("@title_bgm", "BGM"),
            ("@battle_bgm", "BGM"),
            ("@battle_end_me", "ME"),
            ("@gameover_me", "ME"),
            ("@cursor_se", "SE"),
            ("@decision_se", "SE"),
            ("@cancel_se", "SE"),
            ("@buzzer_se", "SE"),
            ("@equip_se", "SE"),
            ("@shop_se", "SE"),
            ("@save_se", "SE"),
            ("@load_se", "SE"),
            ("@battle_start_se", "SE"),
            ("@escape_se", "SE"),
            ("@actor_collapse_se", "SE"),
            ("@enemy_collapse_se", "SE"),
        ):
            _check_audio_object(system.ivars.get(ivar), folder, report, catalog, seen_references)

    for filename in ("Tilesets.rxdata", "TilesetsTemp.rxdata"):
        for value in _walk_objects(loaded.get(data / filename)):
            if value.class_name != "RPG::Tileset":
                continue
            for ivar, folder in (
                ("@tileset_name", "Tilesets"),
                ("@panorama_name", "Panoramas"),
                ("@fog_name", "Fogs"),
                ("@battleback_name", "Battlebacks"),
            ):
                _check_named_graphic(value, ivar, folder, report, catalog, seen_references)
            autotiles = value.ivars.get("@autotile_names", [])
            if isinstance(autotiles, list):
                for item in autotiles:
                    name = _resource_name(item)
                    if name:
                        _check_resource(
                            report,
                            catalog,
                            "graphics",
                            "Graphics/Autotiles",
                            name,
                            seen_references,
                        )

    for filename, class_name, fields in (
        ("Actors.rxdata", "RPG::Actor", (("@character_name", "Characters"), ("@battler_name", "Battlers"))),
        ("Enemies.rxdata", "RPG::Enemy", (("@battler_name", "Battlers"),)),
        ("Items.rxdata", "RPG::Item", (("@icon_name", "Icons"),)),
        ("Skills.rxdata", "RPG::Skill", (("@icon_name", "Icons"),)),
        ("Weapons.rxdata", "RPG::Weapon", (("@icon_name", "Icons"),)),
        ("Armors.rxdata", "RPG::Armor", (("@icon_name", "Icons"),)),
    ):
        for value in _walk_objects(loaded.get(data / filename)):
            if value.class_name != class_name:
                continue
            for ivar, folder in fields:
                _check_named_graphic(value, ivar, folder, report, catalog, seen_references)
            if class_name in {"RPG::Item", "RPG::Skill"}:
                _check_audio_object(
                    value.ivars.get("@menu_se"), "SE", report, catalog, seen_references
                )

    for value in _walk_objects(loaded.get(data / "Animations.rxdata")):
        if value.class_name == "RPG::Animation":
            _check_named_graphic(
                value, "@animation_name", "Animations", report, catalog, seen_references
            )
        elif value.class_name == "RPG::Animation::Timing":
            _check_audio_object(value.ivars.get("@se"), "SE", report, catalog, seen_references)


def _inspect_page_graphic(
    page: RubyObject,
    report: DeepAnalysisReport,
    catalog: _ResourceCatalog,
    seen_references: set[tuple[str, str]],
) -> None:
    graphic = page.ivars.get("@graphic")
    _check_named_graphic(
        graphic, "@character_name", "Characters", report, catalog, seen_references
    )


def _ruby_without_comments(source: str) -> str:
    """Retire les commentaires de ligne sans toucher aux chaînes Ruby simples."""
    result: list[str] = []
    for line in source.splitlines():
        quote = ""
        escaped = False
        kept: list[str] = []
        for character in line:
            if quote:
                kept.append(character)
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
            elif character in {"'", '"'}:
                quote = character
                kept.append(character)
            elif character == "#":
                break
            else:
                kept.append(character)
        result.append("".join(kept))
    return "\n".join(result)


def _literal_resource_is_dynamic(value: str, suffix: str = "") -> bool:
    normalized = value.replace("\\", "/")
    markers = ("#{", "%", "*", "$", "[", "]", "{", "}")
    return (
        normalized.endswith("/")
        or len(normalized.split("/")) < 3
        or any(marker in normalized for marker in markers)
        or suffix.lstrip().startswith(("+", "<<"))
    )


def _inspect_ruby_literal_resources(
    extracted_root: Path,
    report: DeepAnalysisReport,
    catalog: _ResourceCatalog,
) -> None:
    references: set[tuple[str, str]] = set()
    dynamic_expressions: set[str] = set()
    for path in sorted(extracted_root.rglob("*.rb"), key=lambda item: item.as_posix().casefold()):
        try:
            source = _ruby_without_comments(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for match in RUBY_RESOURCE_PATH_RE.finditer(source):
            value = match.group("path").replace("\\", "/")
            line_end = source.find("\n", match.end())
            suffix = source[match.end():line_end if line_end >= 0 else len(source)]
            if _literal_resource_is_dynamic(value, suffix):
                dynamic_expressions.add(value)
                continue
            kind = "audio" if value.casefold().startswith("audio/") else "graphics"
            references.add((kind, value))
        for match in RUBY_AUDIO_CALL_RE.finditer(source):
            name = match.group("name")
            value = f"Audio/{match.group('kind').upper()}/{name}"
            line_end = source.find("\n", match.end())
            suffix = source[match.end():line_end if line_end >= 0 else len(source)]
            if _literal_resource_is_dynamic(value, suffix):
                dynamic_expressions.add(value)
                continue
            references.add(("audio", value))

    checked = missing = 0
    for kind, relative in sorted(references):
        if kind == "audio":
            if not catalog.audio_available:
                continue
            exists = _resource_exists(catalog.audio_paths, relative, AUDIO_EXTENSIONS)
        else:
            if not catalog.graphics_available:
                continue
            exists = _resource_exists(catalog.graphics_paths, relative, GRAPHICS_EXTENSIONS)
        checked += 1
        missing += not exists
    report.ruby_literal_references_checked = checked
    report.ruby_literal_references_missing = missing
    report.ruby_dynamic_resource_expressions = len(dynamic_expressions)
    if checked:
        report.verified.append(
            f"{checked} référence(s) Audio/Graphics littérale(s) repérée(s) dans les scripts Ruby "
            "et comparée(s) aux inventaires, sans exécuter Ruby."
        )
    if missing:
        report.unverified.append(
            f"{missing} référence(s) littérale(s) Ruby pointe(nt) vers une ressource absente ; "
            "leur chemin d'exécution n'est pas prouvé statiquement."
        )
    if dynamic_expressions:
        report.unverified.append(
            f"{len(dynamic_expressions)} expression(s) Ruby de ressource construite(s) dynamiquement "
            "ont été conservée(s) sans résolution improvisée."
        )


def analyze_flux_game(
    root: Path,
    *,
    detection: "DetectionResult",
    mode: str = "complete",
    progress: ProgressCallback | None = None,
    archive_reader: FluxArchiveReader | None = None,
) -> DeepAnalysisReport:
    if mode not in {"quick", "complete", "deep"}:
        raise ValueError(f"Mode d'analyse inconnu : {mode}")
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Le dossier Pokémon Flux est introuvable.")

    report = DeepAnalysisReport(
        game_label=root.name,
        adapter_id=detection.adapter_id,
        adapter_display_name=detection.display_name,
        adapter_confidence=detection.confidence,
        mode=mode,
    )
    reader = archive_reader or FluxArchiveReader()
    fpk = _locate_fpk(root)
    inventory = reader.inspect(fpk)
    report.files_seen = len(inventory.file_entries)
    report.bytes_seen = inventory.unpacked_size
    extensions = Counter(
        PurePosixPath(entry.normalized_path).suffix.casefold() or "[sans extension]"
        for entry in inventory.file_entries
    )
    report.extension_counts = dict(sorted(extensions.items()))
    report.ruby_script_files = sum(
        entry.normalized_path.casefold().endswith(".rb")
        for entry in inventory.file_entries
    )

    if not inventory.safe:
        for problem in inventory.issues:
            _issue(
                report,
                "unsafe_flux_archive",
                "error",
                "security",
                problem,
                "Data/Data_0.fpk",
                blocking=True,
            )
        report.coverage = calculate_coverage([], incomplete_sources=True)
        return report

    report.verified.extend(
        [
            f"FPK 7z inventorié en lecture seule : {len(inventory.file_entries)} fichier(s).",
            "Tous les chemins internes du FPK ont passé les contrôles de sécurité.",
            f"Version détectée : {detection.recognized_version or 'inconnue'}.",
        ]
    )
    if mode == "quick":
        report.unverified.append(
            "Mode rapide : les fichiers Marshal internes n'ont pas été extraits ni interprétés."
        )
        report.coverage = calculate_coverage([], incomplete_sources=True)
        return report

    catalog = _external_resource_catalog(root, reader, report)
    seen_references: set[tuple[str, str]] = set()
    with tempfile.TemporaryDirectory(prefix="pft_flux_readonly_") as temporary:
        # Une seule forme canonique doit servir à l'extraction, au chargement
        # Marshal et à la collecte. Les runners Windows peuvent sinon exposer
        # le même dossier temporaire sous deux graphies différentes.
        extracted_root = Path(temporary).resolve()
        reader.extract_to(fpk, extracted_root, inventory)
        report.verified.append(
            "L'extraction temporaire correspond exactement à l'inventaire annoncé par le FPK."
        )
        data = extracted_root / "Data"
        if not data.is_dir():
            _issue(
                report,
                "missing_flux_data",
                "error",
                "integrity",
                "Le FPK ne contient pas le dossier Data attendu.",
                "Data/",
                blocking=True,
            )
            report.coverage = calculate_coverage([], incomplete_sources=True)
            return report

        marshal_files = sorted(
            (
                path
                for path in data.rglob("*")
                if path.is_file() and path.suffix.casefold() in {".rxdata", ".dat"}
            ),
            key=lambda path: path.relative_to(extracted_root).as_posix().casefold(),
        )
        loaded: dict[Path, object] = {}
        unsupported: list[str] = []
        tasks_total = max(1, len(marshal_files))
        for task_index, path in enumerate(marshal_files, start=1):
            relative = path.relative_to(extracted_root).as_posix()
            if progress:
                progress(task_index, tasks_total, relative)
            try:
                loaded[path] = load(path)
            except Exception as exc:
                required = bool(
                    MAP_NAME_RE.fullmatch(path.name)
                    or path.name in {"CommonEvents.rxdata", "messages_game.dat", "messages.dat"}
                )
                _issue(
                    report,
                    "unreadable_flux_marshal" if required else "unsupported_flux_marshal",
                    "error" if required else "warning",
                    "integrity" if required else "compatibility",
                    (
                        "Fichier Flux essentiel illisible en analyse statique."
                        if required
                        else f"Structure Marshal Flux secondaire non prise en charge ({type(exc).__name__})."
                    ),
                    relative,
                    blocking=required,
                )
                unsupported.append(relative)

        maps = sorted(
            (path for path in data.glob("Map*.rxdata") if MAP_NAME_RE.fullmatch(path.name)),
            key=lambda path: path.name.casefold(),
        )
        report.map_files_found = len(maps)
        for path in maps:
            value = loaded.get(path)
            if not isinstance(value, RubyObject) or value.class_name != "RPG::Map":
                continue
            report.maps_analyzed += 1
            events = value.ivars.get("@events", {})
            if not isinstance(events, dict):
                _issue(
                    report,
                    "invalid_flux_map",
                    "error",
                    "integrity",
                    "Table des événements Flux invalide.",
                    path.relative_to(extracted_root).as_posix(),
                    blocking=True,
                )
                continue
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
                    _inspect_page_graphic(page, report, catalog, seen_references)
                    commands = page.ivars.get("@list", [])
                    if isinstance(commands, list):
                        _inspect_commands(commands, report, catalog, seen_references)

        common_path = data / "CommonEvents.rxdata"
        common = loaded.get(common_path)
        if isinstance(common, list):
            events = [event for event in common if isinstance(event, RubyObject)]
            report.common_events_found = len(events)
            for event in events:
                commands = event.ivars.get("@list", [])
                if isinstance(commands, list):
                    report.common_events_analyzed += 1
                    _inspect_commands(commands, report, catalog, seen_references)

        bank_paths = [data / "messages_game.dat", data / "messages.dat"]
        report.message_banks_found = sum(path.is_file() for path in bank_paths)
        messages_game = loaded.get(data / "messages_game.dat")
        if messages_game is not None:
            report.message_banks_analyzed += 1
        messages = loaded.get(data / "messages.dat")
        if messages is not None:
            report.message_banks_analyzed += 1

        _inspect_database_resources(data, loaded, report, catalog, seen_references)
        _inspect_ruby_literal_resources(extracted_root, report, catalog)

        try:
            occurrences = collect_flux_occurrences(extracted_root, marshal_files, loaded)
        except FluxExtractionError as exc:
            occurrences = []
            _issue(
                report,
                "flux_text_collection_failed",
                "error",
                "integrity",
                str(exc),
                "Data/",
                blocking=True,
            )
        source_counts = Counter(occurrence.source_kind for occurrence in occurrences)
        report.extractable_text_occurrences = len(occurrences)
        report.extractable_unique_texts = len({occurrence.text for occurrence in occurrences})
        report.extractable_by_source = dict(sorted(source_counts.items()))
        texts = [occurrence.text for occurrence in occurrences]

        if unsupported:
            report.unsupported.append(
                f"{len(unsupported)} fichier(s) Marshal secondaire(s) inventorié(s) mais non interprété(s)."
            )
        other_format_counts = Counter(
            PurePosixPath(entry.normalized_path).suffix.casefold() or "[sans extension]"
            for entry in inventory.file_entries
            if PurePosixPath(entry.normalized_path).suffix.casefold()
            not in {".rxdata", ".dat", ".rb"}
            and entry.normalized_path.casefold() != "data/script_index"
        )
        if other_format_counts:
            details = ", ".join(
                f"{extension}={count}" for extension, count in sorted(other_format_counts.items())
            )
            report.unsupported.append(
                f"{sum(other_format_counts.values())} fichier(s) d'autres formats Flux "
                f"conservé(s) en inventaire seulement ({details})."
            )

        report.verified.extend(
            [
                f"{report.maps_analyzed}/{report.map_files_found} carte(s) Flux relue(s).",
                f"{report.map_pages} page(s) d'événements comptée(s).",
                f"{report.common_events_analyzed}/{report.common_events_found} événement(s) commun(s) relu(s).",
                f"{report.message_banks_analyzed}/{report.message_banks_found} banque(s) de messages relue(s).",
                f"{report.extractable_text_occurrences} occurrence(s) de texte Flux extractible(s), "
                f"répartie(s) en {report.extractable_unique_texts} texte(s) source distinct(s).",
                f"{report.static_references_checked} référence(s) Audio/Graphics statique(s) contrôlée(s), "
                f"dont {report.missing_static_references} absente(s).",
            ]
        )
        if report.ruby_script_files:
            report.unverified.append(
                f"{report.ruby_script_files} script(s) Ruby .rb signalé(s), jamais exécuté(s)."
            )
        if report.dynamic_script_commands:
            report.unverified.append(
                f"{report.dynamic_script_commands} commande(s) de script dynamique signalée(s), jamais exécutée(s)."
            )
        incomplete = bool(report.issues or report.unverified or report.unsupported)
        report.coverage = calculate_coverage(texts, incomplete_sources=incomplete)
    return report
