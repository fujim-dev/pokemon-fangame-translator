# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adaptateur expérimental Pokémon Flux, volontairement en lecture seule."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from analysis.flux_analyzer import analyze_flux_game
from flux_archive import FluxArchiveError, FluxArchiveInventory, FluxArchiveReader
from flux_extractor import extract_flux_texts

from .base import AdapterOperationBlocked, DetectionEvidence, DetectionResult, GameCapability


@dataclass(frozen=True)
class FluxReleaseSignature:
    version: str
    fpk_sha256: str
    executable_sha256: str
    messages_game_sha256: str


SUPPORTED_RELEASES = (
    FluxReleaseSignature(
        version="2.1.0",
        fpk_sha256="df944a20e00ab789fb4b65a492ccc32ce71aff382b4da1b235dc0606f786b174",
        executable_sha256="4d14f290118a3cb80305f5b1cb3f6044c2aed8757d1e64d58bee03ef2dce1037",
        messages_game_sha256="23b3180d33404f070ad14049f04e173de8b3ec2a5c4f285cd27f318709533ec4",
    ),
)

REQUIRED_ARCHIVE_MEMBERS = frozenset(
    {
        "data/messages_game.dat",
        "data/messages.dat",
        "data/commonevents.rxdata",
        "data/mapinfos.rxdata",
        "data/system.rxdata",
        "data/script_index",
    }
)


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return False


def _regular_nonempty(path: Path) -> bool:
    try:
        return path.is_file() and not _is_link_or_junction(path) and path.stat().st_size > 0
    except OSError:
        return False


def sha256_stable_file(path: Path) -> str:
    """Calcule une empreinte et refuse un fichier modifié pendant la lecture."""
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise FluxArchiveError(f"Empreinte Flux impossible pour {path.name} : {exc}") from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise FluxArchiveError(f"Le fichier {path.name} a changé pendant son analyse.")
    return digest.hexdigest()


def locate_flux_fpk(root: Path) -> tuple[Path | None, tuple[str, ...]]:
    candidates = tuple(
        path
        for path in (root / "Data" / "Data_0.fpk", root / "Data_0.fpk")
        if _regular_nonempty(path) and not _is_link_or_junction(path.parent)
    )
    if len(candidates) == 1:
        return candidates[0], ()
    if len(candidates) > 1:
        return None, ("Plusieurs Data_0.fpk concurrents ont été détectés.",)
    return None, ("Data/Data_0.fpk est absent ou illisible.",)


class PokemonFluxAdapter:
    adapter_id = "pokemon_flux"
    display_name = "Pokémon Flux (expérimental)"

    def __init__(
        self,
        archive_reader: FluxArchiveReader | None = None,
        *,
        file_hasher: Callable[[Path], str] = sha256_stable_file,
    ):
        self.archive_reader = archive_reader or FluxArchiveReader()
        self.file_hasher = file_hasher

    @staticmethod
    def _text_prefix(path: Path, limit: int = 128_000) -> str:
        if not _regular_nonempty(path):
            return ""
        try:
            return path.read_bytes()[:limit].decode("utf-8-sig", errors="replace")
        except OSError:
            return ""

    @classmethod
    def _ini_markers(cls, root: Path) -> tuple[bool, bool]:
        content = cls._text_prefix(root / "Flux.ini")
        title = re.search(r"(?im)^\s*Title\s*=\s*Pokemon Flux\s*$", content) is not None
        engine = (
            re.search(r"(?im)^\s*Library\s*=\s*RGSS104E\.dll\s*$", content) is not None
            and re.search(r"(?im)^\s*Scripts\s*=\s*Data\\Scripts\.rxdata\s*$", content) is not None
        )
        return title, engine

    @classmethod
    def _mkxp_marker(cls, root: Path) -> bool:
        content = cls._text_prefix(root / "mkxp.json")
        if not content:
            return False
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return False
        return str(payload.get("execName", "")).casefold() == "flux"

    def probe(self, root: Path) -> DetectionResult:
        root = root.expanduser().resolve()
        evidence: list[DetectionEvidence] = []
        warnings: list[str] = []

        def add(
            evidence_id: str,
            relative_path: str,
            observed: bool,
            weight: int,
            explanation: str,
        ) -> None:
            if observed:
                evidence.append(
                    DetectionEvidence(
                        evidence_id=evidence_id,
                        relative_path=relative_path,
                        observed="présent",
                        weight=weight,
                        explanation=explanation,
                    )
                )

        if not root.is_dir() or _is_link_or_junction(root):
            return DetectionResult(
                adapter_id=self.adapter_id,
                display_name=self.display_name,
                confidence=0,
                capabilities=frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
                warnings=("Dossier Flux absent ou redirigé.",),
                adapter_recognized=False,
                write_actions_allowed=False,
            )

        flux_exe = root / "Flux.exe"
        exe_present = _regular_nonempty(flux_exe)
        fpk, fpk_warnings = locate_flux_fpk(root)
        warnings.extend(fpk_warnings)
        ini_title, ini_engine = self._ini_markers(root)
        mkxp = self._mkxp_marker(root)
        assets_fpk = (
            not _is_link_or_junction(root / "Graphics")
            and _regular_nonempty(root / "Graphics" / "Assets_0.fpk")
        )

        add("flux_executable", "Flux.exe", exe_present, 15, "Exécutable Flux présent.")
        add("flux_fpk", "Data/Data_0.fpk", fpk is not None, 20, "Archive de données Flux présente.")
        add("flux_ini_title", "Flux.ini", ini_title, 10, "Titre interne Pokémon Flux reconnu.")
        add("flux_ini_engine", "Flux.ini", ini_engine, 5, "Configuration RGSS Flux cohérente.")
        add("mkxp_config", "mkxp.json", mkxp, 5, "Configuration mkxp associée à Flux.")
        add("assets_fpk", "Graphics/Assets_0.fpk", assets_fpk, 5, "Archive graphique Flux présente.")

        inventory: FluxArchiveInventory | None = None
        member_keys: frozenset[str] = frozenset()
        archive_readable = archive_structure = maps_present = scripts_present = False
        if fpk is not None:
            try:
                inventory = self.archive_reader.inspect(fpk)
            except Exception as exc:
                warnings.append(f"Inventaire FPK impossible : {type(exc).__name__}: {exc}")
            else:
                archive_readable = inventory.archive_type.casefold() == "7z"
                member_keys = frozenset(path.casefold() for path in inventory.member_paths)
                archive_structure = inventory.safe and REQUIRED_ARCHIVE_MEMBERS.issubset(member_keys)
                maps_present = any(
                    re.fullmatch(r"data/map\d{3,4}\.rxdata", path, re.I)
                    for path in inventory.member_paths
                )
                scripts_present = any(
                    re.fullmatch(r"data/script_\d+\.rb", path, re.I)
                    for path in inventory.member_paths
                )
                warnings.extend(inventory.issues)

        add("fpk_7z", "Data/Data_0.fpk", archive_readable, 10, "Conteneur FPK 7z reconnu.")
        add("fpk_structure", "Data/*", archive_structure, 15, "Fichiers internes Flux attendus présents.")
        add("flux_maps", "Data/MapXXX.rxdata", maps_present, 10, "Cartes Flux détectées dans le FPK.")
        add("flux_scripts", "Data/Script_XXX.rb", scripts_present, 5, "Banque de scripts Flux signalée sans exécution.")

        family_recognized = bool(
            exe_present
            and fpk is not None
            and ini_title
            and ini_engine
            and mkxp
            and archive_structure
            and maps_present
            and scripts_present
        )

        fpk_hash = exe_hash = messages_hash = ""
        if family_recognized and fpk is not None and inventory is not None:
            try:
                fpk_hash = self.file_hasher(fpk).casefold()
                exe_hash = self.file_hasher(flux_exe).casefold()
                messages_hash = self.archive_reader.member_sha256(
                    fpk,
                    "Data/messages_game.dat",
                    inventory,
                ).casefold()
            except Exception as exc:
                warnings.append(f"Empreintes Flux incomplètes : {type(exc).__name__}: {exc}")

        release = next(
            (
                signature
                for signature in SUPPORTED_RELEASES
                if fpk_hash == signature.fpk_sha256
                and exe_hash == signature.executable_sha256
                and messages_hash == signature.messages_game_sha256
            ),
            None,
        )
        add("known_fpk_hash", "Data/Data_0.fpk", release is not None, 15, "Empreinte FPK v2.1.0 reconnue.")
        add("known_executable_hash", "Flux.exe", release is not None, 5, "Empreinte exécutable v2.1.0 reconnue.")
        add("known_messages_hash", "Data/messages_game.dat", release is not None, 5, "Banque de messages v2.1.0 reconnue.")

        if family_recognized and release is None:
            warnings.append(
                "Structure Pokémon Flux reconnue, mais version non homologuée : toute écriture reste bloquée."
            )
        elif release is not None:
            warnings.append(
                "Adaptateur Flux expérimental : extraction CSV en lecture seule autorisée, reconstruction désactivée."
            )
        else:
            warnings.append("Indices Flux insuffisants ou incohérents.")

        raw_confidence = sum(item.weight for item in evidence)
        confidence = 100 if release is not None else min(85, raw_confidence)
        capabilities = {GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}
        if release is not None:
            capabilities.update({GameCapability.EXTRACT, GameCapability.TRANSLATE})
        return DetectionResult(
            adapter_id=self.adapter_id,
            display_name=self.display_name,
            confidence=confidence,
            capabilities=frozenset(capabilities),
            evidence=tuple(evidence),
            warnings=tuple(dict.fromkeys(warnings)),
            recognized_version=release.version if release else "inconnue",
            adapter_recognized=family_recognized,
            write_actions_allowed=False,
        )

    def analyze(self, root: Path, detection: DetectionResult, mode="complete", progress=None):
        if detection.adapter_id != self.adapter_id or not detection.adapter_recognized:
            raise AdapterOperationBlocked(
                "Analyse Flux structurée bloquée : profil Flux non reconnu avec assez de certitude."
            )
        return analyze_flux_game(
            root,
            detection=detection,
            mode=mode,
            progress=progress,
            archive_reader=self.archive_reader,
        )

    def extract(self, root: Path, progress=None, logger=None) -> tuple[list[dict], list[str]]:
        from .registry import authorize_adapter_operation

        authorize_adapter_operation(
            root,
            expected_adapter_id=self.adapter_id,
            capability=GameCapability.EXTRACT,
            adapter=self,
        )
        return extract_flux_texts(
            root,
            archive_reader=self.archive_reader,
            progress=progress,
            logger=logger,
        )
