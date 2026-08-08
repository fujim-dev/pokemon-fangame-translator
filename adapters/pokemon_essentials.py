from __future__ import annotations

import re
from pathlib import Path

from .base import AdapterOperationBlocked, DetectionEvidence, DetectionResult, GameCapability
from structured_extractor import extract_structured
from analysis.deep_analyzer import analyze_game


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x0400)
    except OSError:
        return False


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
            if (
                not path.is_file()
                or _is_link_or_junction(path)
                or _is_link_or_junction(path.parent)
            ):
                continue
            try:
                raw = path.read_bytes()[:8_000_000]
            except OSError:
                continue
            for pattern in patterns:
                match = pattern.search(raw)
                if match:
                    return match.group(1).decode("ascii", "ignore")
        pbs = root / "PBS"
        return (
            "inconnue (structure PBS détectée)"
            if pbs.is_dir() and not _is_link_or_junction(pbs)
            else "inconnue"
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
        plugin_scripts = is_nonempty_file(data / "PluginScripts.rxdata")
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
        recognized = (
            core_detected
            and strong_markers >= 1
            and essentials_markers >= 2
            and not redirected_markers
        )
        confidence = min(100, sum(item.weight for item in evidence))
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
