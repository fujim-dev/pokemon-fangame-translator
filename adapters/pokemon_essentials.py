from __future__ import annotations

import re
from pathlib import Path

from .base import AdapterOperationBlocked, DetectionEvidence, DetectionResult, GameCapability
from structured_extractor import extract_structured
from analysis.deep_analyzer import analyze_game


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
        from .registry import authorize_adapter_operation

        authorize_adapter_operation(
            root,
            expected_adapter_id=self.adapter_id,
            capability=GameCapability.EXTRACT,
            adapter=self,
        )
        return extract_structured(root, progress=progress, logger=logger)

    @staticmethod
    def _detect_version(root: Path) -> str:
        candidates = (
            root / "Data" / "Scripts.rxdata",
            root / "Data" / "PluginScripts.rxdata",
            root / "PBS" / "metadata.txt",
            root / "PBS" / "pokemon.txt",
            root / "Scripts" / "Settings.rb",
            root / "Data" / "messages.dat",
        )
        patterns = (
            re.compile(rb"Essentials\s+v?(\d+(?:\.\d+)*)", re.I),
            re.compile(rb"Pokemon Essentials\s+v?(\d+(?:\.\d+)*)", re.I),
            re.compile(rb"ESSENTIALS_VERSION.{0,30}?(\d+(?:\.\d+)*)", re.I),
        )
        for path in candidates:
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()[:8_000_000]
            except OSError:
                continue
            for pattern in patterns:
                match = pattern.search(raw)
                if match:
                    return match.group(1).decode("ascii", "ignore")
        return "inconnue (structure PBS détectée)" if (root / "PBS").is_dir() else "inconnue"

    def probe(self, root: Path) -> DetectionResult:
        root = root.expanduser().resolve()
        data = root / "Data"
        evidence: list[DetectionEvidence] = []

        def is_nonempty_file(path: Path) -> bool:
            try:
                return path.is_file() and path.stat().st_size > 0
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

        game_exe = (root / "Game.exe").is_file()
        game_ini = (root / "Game.ini").is_file()
        data_dir = data.is_dir()
        rmxp_database = (data / "System.rxdata").is_file() or (data / "MapInfos.rxdata").is_file()
        map_count = (
            sum(
                1
                for path in data.glob("Map*.rxdata")
                if path.is_file() and re.fullmatch(r"Map\d{3,4}\.rxdata", path.name, re.I)
            )
            if data_dir
            else 0
        )
        pbs = root / "PBS"
        pbs_dir = pbs.is_dir()
        pbs_content = pbs_dir and any(
            is_nonempty_file(pbs / name)
            for name in ("pokemon.txt", "moves.txt", "items.txt", "metadata.txt", "types.txt")
        )
        message_bank = any(
            is_nonempty_file(data / name)
            for name in ("messages.dat", "messages_game.dat", "messages_core.dat")
        )
        plugin_scripts = is_nonempty_file(data / "PluginScripts.rxdata")
        pokemon_graphics = any(
            path.is_dir()
            for path in (root / "Graphics" / "Pokemon", root / "Graphics" / "Battlers")
        )

        add("game_exe", "Game.exe", game_exe, 10, "Exécutable RPG Maker XP détecté.")
        add("game_ini", "Game.ini", game_ini, 10, "Configuration RPG Maker détectée.")
        add("data_dir", "Data/", data_dir, 10, "Dossier de données présent.")
        add("rmxp_database", "Data/System.rxdata ou Data/MapInfos.rxdata", rmxp_database, 15, "Base RPG Maker XP détectée.")
        add("maps", "Data/MapXXX.rxdata", map_count > 0, 15, f"{map_count} carte(s) classique(s) détectée(s).")
        add("pbs", "PBS/", pbs_content, 20, "Données PBS Pokémon Essentials détectées.")
        add("message_bank", "Data/messages*.dat", message_bank, 15, "Banque de messages Essentials détectée.")
        add("pokemon_graphics", "Graphics/Pokemon ou Graphics/Battlers", pokemon_graphics, 10, "Arborescence graphique Pokémon détectée.")
        add("plugin_scripts", "Data/PluginScripts.rxdata", plugin_scripts, 5, "Plugins Essentials détectés.")

        core_detected = game_exe and game_ini and data_dir and rmxp_database
        essentials_markers = sum((pbs_content, message_bank, plugin_scripts, pokemon_graphics))
        strong_markers = sum((pbs_content, message_bank, plugin_scripts))
        recognized = core_detected and strong_markers >= 1 and essentials_markers >= 2
        confidence = min(100, sum(item.weight for item in evidence))
        warnings: list[str] = []
        if not core_detected:
            warnings.append("Structure RPG Maker XP classique incomplète.")
        if essentials_markers < 2:
            warnings.append("Indices Pokémon Essentials insuffisants ou ambigus.")

        capabilities = (
            frozenset(
                {
                    GameCapability.ANALYZE,
                    GameCapability.DEEP_ANALYZE,
                    GameCapability.EXTRACT,
                    GameCapability.TRANSLATE,
                    GameCapability.RECONSTRUCT,
                }
            )
            if recognized
            else frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE})
        )
        return DetectionResult(
            adapter_id=self.adapter_id,
            display_name=self.display_name,
            confidence=confidence,
            capabilities=capabilities,
            evidence=tuple(evidence),
            warnings=tuple(warnings),
            recognized_version=self._detect_version(root),
            adapter_recognized=recognized,
            write_actions_allowed=recognized,
        )
