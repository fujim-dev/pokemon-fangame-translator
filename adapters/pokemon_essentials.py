from __future__ import annotations

from dataclasses import replace
import re
from pathlib import Path

from .base import AdapterOperationBlocked, DetectionEvidence, DetectionResult, GameCapability
from .essentials_profiles import (
    ESSENTIALS_LEGACY_PROFILE,
    ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE,
    ESSENTIALS_V21_1_READONLY_PROFILE,
    inspect_essentials_static,
)
from structured_extractor import (
    ExtractionIntegrityError,
    StructuredExtractionResult,
    extract_structured_verified,
)
from analysis.deep_analyzer import analyze_game


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
    except OSError:
        return True


class PokemonEssentialsAdapter:
    adapter_id = "pokemon_essentials"
    display_name = "Pokémon Essentials classique"

    def analyze(self, root: Path, detection: DetectionResult, mode="complete", progress=None):
        return analyze_game(
            root,
            adapter_id=detection.adapter_id,
            adapter_display_name=detection.display_name,
            adapter_confidence=detection.confidence,
            mode=mode,
            progress=progress,
        )

    def extract(self, root: Path, progress=None, logger=None) -> tuple[list[dict], list[str]]:
        result = self.extract_with_provenance(root, progress=progress, logger=logger)
        return result.rows, result.errors

    def extract_with_provenance(
        self,
        root: Path,
        progress=None,
        logger=None,
    ) -> StructuredExtractionResult:
        from .registry import authorize_adapter_operation

        detection_before = authorize_adapter_operation(
            root,
            expected_adapter_id=self.adapter_id,
            capability=GameCapability.EXTRACT,
            adapter=self,
        )
        result = extract_structured_verified(root, progress=progress, logger=logger)
        detection_after = authorize_adapter_operation(
            root,
            expected_adapter_id=self.adapter_id,
            capability=GameCapability.EXTRACT,
            adapter=self,
        )
        before_fingerprint = (
            detection_before.adapter_id,
            detection_before.confidence,
            detection_before.recognized_version,
            detection_before.declared_version,
            detection_before.version_detection_method,
            detection_before.structural_profile,
            detection_before.evidence,
            detection_before.warnings,
            detection_before.capabilities,
        )
        after_fingerprint = (
            detection_after.adapter_id,
            detection_after.confidence,
            detection_after.recognized_version,
            detection_after.declared_version,
            detection_after.version_detection_method,
            detection_after.structural_profile,
            detection_after.evidence,
            detection_after.warnings,
            detection_after.capabilities,
        )
        if before_fingerprint != after_fingerprint:
            raise ExtractionIntegrityError(
                "L'identité Essentials du fangame a changé pendant l'extraction. "
                "Aucun résultat n'est accepté."
            )
        rows = [
            {
                **row,
                "profil_essentials": detection_before.structural_profile,
                "version_essentials_declaree": detection_before.declared_version,
                "methode_version_essentials": detection_before.version_detection_method,
            }
            for row in result.rows
        ]
        return replace(
            result,
            rows=rows,
            essentials_profile=detection_before.structural_profile,
            declared_version=detection_before.declared_version,
            version_detection_method=detection_before.version_detection_method,
        )

    def probe(self, root: Path) -> DetectionResult:
        root_input = root.expanduser()
        if not root_input.is_dir() or _is_link_or_junction(root_input):
            return DetectionResult(
                adapter_id=self.adapter_id,
                display_name=self.display_name,
                confidence=0,
                capabilities=frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
                warnings=("Dossier Essentials absent ou redirigé.",),
                adapter_recognized=False,
                write_actions_allowed=False,
            )
        root = root_input.resolve()
        data = root / "Data"
        evidence: list[DetectionEvidence] = []

        def is_nonempty_file(path: Path) -> bool:
            try:
                return (
                    path.is_file()
                    and not _is_link_or_junction(path)
                    and path.stat().st_size > 0
                )
            except OSError:
                return False

        def add(evidence_id: str, relative_path: str, observed: bool, weight: int, explanation: str) -> None:
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

        game_exe = is_nonempty_file(root / "Game.exe")
        game_ini = is_nonempty_file(root / "Game.ini")
        data_dir = data.is_dir() and not _is_link_or_junction(data)
        rmxp_database = is_nonempty_file(data / "System.rxdata") or is_nonempty_file(
            data / "MapInfos.rxdata"
        )
        map_count = (
            sum(
                1
                for path in data.glob("Map*.rxdata")
                if is_nonempty_file(path)
                and re.fullmatch(r"Map\d{3,4}\.rxdata", path.name, re.I)
            )
            if data_dir
            else 0
        )
        pbs = root / "PBS"
        pbs_dir = pbs.is_dir() and not _is_link_or_junction(pbs)
        pbs_content = pbs_dir and any(
            is_nonempty_file(pbs / name)
            for name in ("pokemon.txt", "moves.txt", "items.txt", "metadata.txt", "types.txt")
        )
        message_bank = any(
            is_nonempty_file(data / name)
            for name in ("messages.dat", "messages_game.dat", "messages_core.dat")
        )
        graphics = root / "Graphics"
        pokemon_graphics = not _is_link_or_junction(graphics) and any(
            path.is_dir() and not _is_link_or_junction(path)
            for path in (root / "Graphics" / "Pokemon", root / "Graphics" / "Battlers")
        )

        redirected_markers = [
            relative
            for relative, path in (
                ("Game.exe", root / "Game.exe"),
                ("Game.ini", root / "Game.ini"),
                ("mkxp.json", root / "mkxp.json"),
                ("Data/", data),
                ("Data/System.rxdata", data / "System.rxdata"),
                ("Data/MapInfos.rxdata", data / "MapInfos.rxdata"),
                ("Data/messages.dat", data / "messages.dat"),
                ("Data/messages_game.dat", data / "messages_game.dat"),
                ("Data/messages_core.dat", data / "messages_core.dat"),
                ("Data/Scripts.rxdata", data / "Scripts.rxdata"),
                ("Data/PluginScripts.rxdata", data / "PluginScripts.rxdata"),
                ("PBS/", pbs),
                ("Graphics/", graphics),
                ("Graphics/Pokemon/", graphics / "Pokemon"),
                ("Graphics/Battlers/", graphics / "Battlers"),
            )
            if _is_link_or_junction(path)
        ]
        redirected_markers.extend(
            f"PBS/{name}"
            for name in ("pokemon.txt", "moves.txt", "items.txt", "metadata.txt", "types.txt")
            if _is_link_or_junction(pbs / name)
        )
        if data_dir:
            redirected_markers.extend(
                path.relative_to(root).as_posix()
                for path in data.glob("Map*.rxdata")
                if _is_link_or_junction(path)
            )
        if redirected_markers:
            warnings = [
                "Indices Essentials redirigés par lien ou jonction : "
                + ", ".join(sorted(set(redirected_markers), key=str.casefold))
                + "."
            ]
        else:
            warnings = []

        inspection = inspect_essentials_static(root) if not redirected_markers else None
        if inspection is not None:
            warnings.extend(inspection.warnings)
        plugin_scripts = bool(inspection and inspection.plugin_scripts_meaningful)

        add("game_exe", "Game.exe", game_exe, 10, "Exécutable RPG Maker XP détecté.")
        add("game_ini", "Game.ini", game_ini, 10, "Configuration RPG Maker détectée.")
        add("data_dir", "Data/", data_dir, 10, "Dossier de données présent.")
        add("rmxp_database", "Data/System.rxdata ou Data/MapInfos.rxdata", rmxp_database, 15, "Base RPG Maker XP détectée.")
        add("maps", "Data/MapXXX.rxdata", map_count > 0, 15, f"{map_count} carte(s) classique(s) détectée(s).")
        add("pbs", "PBS/", pbs_content, 20, "Données PBS Pokémon Essentials détectées.")
        add("message_bank", "Data/messages*.dat", message_bank, 15, "Banque de messages Essentials détectée.")
        add("pokemon_graphics", "Graphics/Pokemon ou Graphics/Battlers", pokemon_graphics, 10, "Arborescence graphique Pokémon détectée.")
        add("plugin_scripts", "Data/PluginScripts.rxdata", plugin_scripts, 5, "Table de plugins Essentials non vide détectée.")
        if inspection and inspection.plugin_scripts_present and not plugin_scripts:
            evidence.append(
                DetectionEvidence(
                    evidence_id="plugin_scripts_empty",
                    relative_path="Data/PluginScripts.rxdata",
                    observed="table vide",
                    weight=0,
                    explanation="Conteneur présent mais vide : aucune présence de plugin n'est déduite.",
                )
            )
        if inspection:
            add(
                "mkxp_config",
                "mkxp.json",
                inspection.mkxp_present,
                5,
                "Configuration mkxp-z détectée statiquement.",
            )
            add(
                "essentials_script_version",
                "Data/Scripts.rxdata",
                bool(inspection.script_version),
                10,
                "Constante Essentials::VERSION lue dans Settings sans exécution Ruby.",
            )

        core_detected = game_exe and game_ini and data_dir and rmxp_database
        essentials_markers = sum((pbs_content, message_bank, plugin_scripts, pokemon_graphics))
        family_detected = (
            core_detected
            and (message_bank or plugin_scripts)
            and essentials_markers >= 2
            and not redirected_markers
        )
        confidence = min(100, sum(item.weight for item in evidence))
        if not core_detected:
            warnings.append("Structure RPG Maker XP classique incomplète.")
        if essentials_markers < 2:
            warnings.append("Indices Pokémon Essentials insuffisants ou ambigus.")

        declared_version = inspection.declared_version if inspection else ""
        version_method = inspection.version_detection_method if inspection else ""
        modern_layout = bool(
            inspection
            and (
                inspection.mkxp_present
                or is_nonempty_file(data / "messages_core.dat")
                or any(root.glob("*ruby3*.dll"))
                or any(root.glob("*ruby310*.dll"))
            )
        )
        v21_structure_confirmed = bool(
            family_detected
            and inspection
            and not inspection.warnings
            and not inspection.version_conflict
            and declared_version == "21.1"
            and inspection.script_version == "21.1"
            and inspection.mkxp_present
            and len(inspection.modern_script_markers) >= 2
        )
        if not family_detected:
            profile = ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE
        elif v21_structure_confirmed:
            profile = ESSENTIALS_V21_1_READONLY_PROFILE
        elif inspection and (
            inspection.version_conflict
            or bool(declared_version)
            or modern_layout
        ):
            profile = ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE
        else:
            profile = ESSENTIALS_LEGACY_PROFILE

        if family_detected and profile == ESSENTIALS_MODIFIED_OR_UNKNOWN_PROFILE:
            warnings.append(
                "Famille Pokémon Essentials détectée, mais version ou profil structurel "
                "non validé : extraction, traduction et reconstruction bloquées."
            )
        if profile == ESSENTIALS_V21_1_READONLY_PROFILE:
            warnings.append(
                "Profil Essentials v21.1 confirmé : reconstruction volontairement bloquée "
                "jusqu'à un round-trip validé sur une copie de travail."
            )

        legacy = family_detected and profile == ESSENTIALS_LEGACY_PROFILE
        v21_readonly = family_detected and profile == ESSENTIALS_V21_1_READONLY_PROFILE
        if legacy:
            capabilities = frozenset(
                {
                    GameCapability.ANALYZE,
                    GameCapability.DEEP_ANALYZE,
                    GameCapability.EXTRACT,
                    GameCapability.TRANSLATE,
                    GameCapability.RECONSTRUCT,
                }
            )
            display_name = "Pokémon Essentials classique (RMXP)"
        elif v21_readonly:
            capabilities = frozenset(
                {
                    GameCapability.ANALYZE,
                    GameCapability.DEEP_ANALYZE,
                    GameCapability.EXTRACT,
                    GameCapability.TRANSLATE,
                }
            )
            display_name = "Pokémon Essentials v21.1 (jeu en lecture seule)"
        else:
            capabilities = frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE})
            display_name = "Pokémon Essentials modifié ou version inconnue"
        return DetectionResult(
            adapter_id=self.adapter_id,
            display_name=display_name,
            confidence=confidence,
            capabilities=capabilities,
            evidence=tuple(evidence),
            warnings=tuple(warnings),
            recognized_version=declared_version or "inconnue",
            adapter_recognized=family_detected,
            write_actions_allowed=legacy,
            engine_family="pokemon_essentials" if family_detected else "",
            declared_version=declared_version,
            version_detection_method=version_method,
            structural_profile=profile,
            analysis_compatible=family_detected,
            extraction_compatible=legacy or v21_readonly,
            translation_compatible=legacy or v21_readonly,
            game_write_compatible=legacy,
            reconstruction_validated=legacy,
        )
