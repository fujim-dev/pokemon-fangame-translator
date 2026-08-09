# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspection statique et bornée des profils Pokémon Essentials.

Ce module ne lance aucun interpréteur Ruby. Il lit uniquement les marqueurs
déclaratifs et, pour ``Scripts.rxdata``, le conteneur Marshal puis les blocs
zlib nécessaires à l'identification de la version et de la structure.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path

from ruby_marshal_reader import MarshalReader, RubyString


ESSENTIALS_LEGACY_PROFILE = "essentials_legacy_rxmp"
ESSENTIALS_V21_1_READONLY_PROFILE = "essentials_v21_1_readonly"
ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE = "essentials_modified_or_unknown"

MAX_SCRIPT_BANK_BYTES = 64 * 1024 * 1024
MAX_COMPRESSED_SCRIPT_BYTES = 8 * 1024 * 1024
MAX_DECOMPRESSED_SCRIPT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_DECOMPRESSED_BYTES = 64 * 1024 * 1024

ESSENTIALS_LABEL_RE = re.compile(
    r"(?:Pok[eé]mon\s+)?Essentials\s+v?(\d+(?:\.\d+){0,2})",
    re.I,
)
SETTINGS_VERSION_RE = re.compile(
    rb"\bmodule\s+Essentials\b[\s\S]{0,131072}?"
    rb"\bVERSION\s*=\s*['\"](\d+(?:\.\d+){0,2})['\"]",
    re.I,
)


@dataclass(frozen=True)
class VersionMarker:
    method: str
    version: str


@dataclass(frozen=True)
class EssentialsStaticInspection:
    markers: tuple[VersionMarker, ...] = ()
    declared_version: str = ""
    version_detection_method: str = ""
    version_conflict: bool = False
    script_version: str = ""
    mkxp_present: bool = False
    modern_script_markers: frozenset[str] = frozenset()
    plugin_scripts_present: bool = False
    plugin_scripts_meaningful: bool = False
    warnings: tuple[str, ...] = ()


def _read_limited(path: Path, maximum: int) -> bytes:
    size = path.stat().st_size
    if size > maximum:
        raise ValueError(f"fichier trop volumineux ({size} octets)")
    with path.open("rb") as handle:
        payload = handle.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("fichier plus volumineux que la limite autorisée")
    return payload


def _marshal_from_bytes(payload: bytes):
    if payload[:2] != b"\x04\x08":
        raise ValueError("flux Ruby Marshal 4.8 attendu")
    reader = MarshalReader(payload)
    reader.pos = 2
    value = reader.read_object()
    if reader.pos != len(payload):
        raise ValueError("octets inattendus après le conteneur Ruby Marshal")
    return value


def _ruby_bytes(value) -> bytes | None:
    if isinstance(value, RubyString):
        return value.data
    if isinstance(value, bytes):
        return value
    return None


def _ruby_text(value) -> str:
    if isinstance(value, RubyString):
        return value.text()
    if isinstance(value, str):
        return value
    return ""


def _decompress_script(payload: bytes) -> bytes:
    if len(payload) > MAX_COMPRESSED_SCRIPT_BYTES:
        raise ValueError("bloc de script compressé trop volumineux")
    inflater = zlib.decompressobj()
    result = inflater.decompress(payload, MAX_DECOMPRESSED_SCRIPT_BYTES + 1)
    if (
        len(result) > MAX_DECOMPRESSED_SCRIPT_BYTES
        or inflater.unconsumed_tail
        or inflater.unused_data
        or not inflater.eof
    ):
        raise ValueError("bloc de script décompressé trop volumineux ou incomplet")
    return result


def _inspect_scripts(path: Path) -> tuple[str, frozenset[str], tuple[str, ...]]:
    try:
        root = _marshal_from_bytes(_read_limited(path, MAX_SCRIPT_BANK_BYTES))
    except Exception as exc:
        return "", frozenset(), (
            f"Data/Scripts.rxdata n'a pas pu être inspecté statiquement ({type(exc).__name__}).",
        )
    if not isinstance(root, list):
        return "", frozenset(), (
            "Data/Scripts.rxdata ne contient pas la table de scripts attendue.",
        )

    settings_versions: set[str] = set()
    modern_markers: set[str] = set()
    warnings: list[str] = []
    total_decompressed = 0
    for entry_index, entry in enumerate(root):
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        name = _ruby_text(entry[1]).strip()
        compressed = _ruby_bytes(entry[2])
        if compressed is None:
            continue
        try:
            script = _decompress_script(compressed)
        except Exception as exc:
            warnings.append(
                "Un bloc de Scripts.rxdata n'a pas pu être décompressé de façon bornée "
                f"(entrée {entry_index}, {type(exc).__name__})."
            )
            continue
        total_decompressed += len(script)
        if total_decompressed > MAX_TOTAL_DECOMPRESSED_BYTES:
            warnings.append(
                "L'inspection de Scripts.rxdata a atteint sa limite totale de décompression."
            )
            break

        normalized_name = re.sub(r"\s+", " ", name).strip().casefold()
        if normalized_name == "settings" or normalized_name.endswith("] settings"):
            settings_versions.update(
                match.group(1).decode("ascii")
                for match in SETTINGS_VERSION_RE.finditer(script)
            )
        if re.search(rb"\b(?:module|class)\s+GameData\b", script):
            modern_markers.add("GameData")
        if re.search(rb"\b(?:module|class)\s+PluginManager\b", script):
            modern_markers.add("PluginManager")
        if re.search(rb"\b(?:module|class)\s+MessageTypes\b", script):
            modern_markers.add("MessageTypes")

    if len(settings_versions) > 1:
        warnings.append(
            "Plusieurs constantes Essentials::VERSION contradictoires ont été trouvées."
        )
        return ", ".join(sorted(settings_versions)), frozenset(modern_markers), tuple(warnings)
    return next(iter(settings_versions), ""), frozenset(modern_markers), tuple(warnings)


def _declared_version_from_text(text: str) -> str:
    match = ESSENTIALS_LABEL_RE.search(text)
    return match.group(1) if match else ""


def _inspect_game_ini(path: Path) -> str:
    raw = _read_limited(path, 1024 * 1024)
    text = raw.decode("utf-8-sig", errors="replace")
    in_game = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        section = re.fullmatch(r"\[([^\]]+)\]", line)
        if section:
            in_game = section.group(1).strip().casefold() == "game"
            continue
        if not in_game or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().casefold() == "title":
            return _declared_version_from_text(value.strip())
    return ""


def _inspect_mkxp(path: Path) -> str:
    raw = _read_limited(path, 2 * 1024 * 1024)
    text = raw.decode("utf-8-sig", errors="replace")
    match = re.search(r'"windowTitle"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text)
    if not match:
        return ""
    return _declared_version_from_text(match.group(1))


def _inspect_plugin_scripts(path: Path) -> tuple[bool, str]:
    try:
        root = _marshal_from_bytes(_read_limited(path, MAX_SCRIPT_BANK_BYTES))
    except Exception as exc:
        return False, (
            "Data/PluginScripts.rxdata est présent mais son conteneur statique est "
            f"illisible ({type(exc).__name__})."
        )
    if not isinstance(root, list):
        return False, "Data/PluginScripts.rxdata n'est pas une table de scripts reconnue."
    meaningful = any(entry is not None for entry in root)
    return meaningful, ""


def inspect_essentials_static(root: Path) -> EssentialsStaticInspection:
    """Retourne des marqueurs de version sans exécuter le moindre code du jeu."""
    markers: list[VersionMarker] = []
    warnings: list[str] = []
    data = root / "Data"

    game_ini = root / "Game.ini"
    if game_ini.is_file():
        try:
            version = _inspect_game_ini(game_ini)
        except Exception as exc:
            warnings.append(f"Game.ini n'a pas pu être inspecté ({type(exc).__name__}).")
        else:
            if version:
                markers.append(VersionMarker("Game.ini:Game.Title", version))

    mkxp = root / "mkxp.json"
    mkxp_present = mkxp.is_file()
    if mkxp_present:
        try:
            version = _inspect_mkxp(mkxp)
        except Exception as exc:
            warnings.append(f"mkxp.json n'a pas pu être inspecté ({type(exc).__name__}).")
        else:
            if version:
                markers.append(VersionMarker("mkxp.json:windowTitle", version))

    scripts = data / "Scripts.rxdata"
    script_version = ""
    modern_script_markers: frozenset[str] = frozenset()
    if scripts.is_file():
        script_version, modern_script_markers, script_warnings = _inspect_scripts(scripts)
        warnings.extend(script_warnings)
        if script_version and ", " not in script_version:
            markers.append(VersionMarker("Scripts.rxdata:Settings/Essentials::VERSION", script_version))

    plugin_scripts = data / "PluginScripts.rxdata"
    plugin_present = plugin_scripts.is_file()
    plugin_meaningful = False
    if plugin_present:
        plugin_meaningful, plugin_warning = _inspect_plugin_scripts(plugin_scripts)
        if plugin_warning:
            warnings.append(plugin_warning)

    versions = sorted({marker.version for marker in markers})
    conflict = len(versions) > 1 or ", " in script_version
    if conflict:
        declared_version = " / ".join(versions or [script_version])
        warnings.append(
            "Les marqueurs de version Essentials se contredisent ; le profil est bloqué en lecture seule."
        )
    else:
        declared_version = versions[0] if versions else ""
    method = " + ".join(marker.method for marker in markers)
    return EssentialsStaticInspection(
        markers=tuple(markers),
        declared_version=declared_version,
        version_detection_method=method,
        version_conflict=conflict,
        script_version=script_version,
        mkxp_present=mkxp_present,
        modern_script_markers=modern_script_markers,
        plugin_scripts_present=plugin_present,
        plugin_scripts_meaningful=plugin_meaningful,
        warnings=tuple(warnings),
    )
