from __future__ import annotations

import json
from pathlib import Path

from .base import AdapterOperationBlocked, DetectionEvidence, DetectionResult, GameCapability
from .unknown import UnknownAdapter


class PokemonStudioAdapter:
    """Reconnaissance statique de Pokémon Studio, sans prise en charge de traduction."""

    adapter_id = "pokemon_studio"
    display_name = "Pokémon Studio / PSDK (analyse uniquement)"

    def probe(self, root: Path) -> DetectionResult:
        # Refuser toute redirection de la racine ou des dossiers de données.
        # Une jonction Windows n'est pas toujours signalée par is_symlink().
        def redirected(path: Path) -> bool:
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

        if redirected(root) or redirected(root / "Data"):
            return self._unrecognized("Chemin de projet ou de données redirigé.")
        marker = root / "project.studio"
        studio_data = root / "Data" / "Studio"
        tiled_data = root / "Data" / "Tiled"
        try:
            if not marker.is_file() or redirected(marker):
                return self._unrecognized()
            if marker.stat().st_size > 1024 * 1024:
                return self._unrecognized("Fichier project.studio trop volumineux.")
            metadata = json.loads(marker.read_text(encoding="utf-8-sig"))
            if not isinstance(metadata, dict):
                return self._unrecognized("Métadonnées Studio invalides.")
            version = metadata.get("studioVersion", "")
            if not isinstance(version, str) or not version.strip():
                return self._unrecognized("Version Studio absente.")
            data_dirs = [
                (name, path)
                for name, path in (("Data/Studio", studio_data), ("Data/Tiled", tiled_data))
                if path.is_dir() and not redirected(path)
            ]
            if not data_dirs:
                return self._unrecognized("Données Studio/Tiled absentes.")
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
            return self._unrecognized("Métadonnées Studio illisibles ou malformées.")
        evidence = (
            DetectionEvidence("studio_project", "project.studio", "valide", 50,
                              "Métadonnées Pokémon Studio présentes."),
            *(
                DetectionEvidence("studio_data", name, "présent", 20,
                                  "Dossier de données Studio/Tiled présent.")
                for name, _ in data_dirs
            ),
        )
        return DetectionResult(
            adapter_id=self.adapter_id,
            display_name=self.display_name,
            confidence=min(100, sum(item.weight for item in evidence)),
            capabilities=frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            evidence=tuple(evidence),
            warnings=("Projet Studio reconnu en lecture seule : extraction, traduction et reconstruction non disponibles.",),
            recognized_version=version,
            adapter_recognized=True,
            write_actions_allowed=False,
            engine_family="pokemon_studio_psdk",
            declared_version=version,
            version_detection_method="project.studio",
            structural_profile="studio_project_readonly",
            analysis_compatible=True,
        )

    def _unrecognized(self, warning: str = "Signature Studio insuffisante.") -> DetectionResult:
        return DetectionResult(
            adapter_id=self.adapter_id,
            display_name=self.display_name,
            confidence=0,
            capabilities=frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            warnings=(warning,),
            adapter_recognized=False,
            write_actions_allowed=False,
        )

    def analyze(self, root: Path, detection: DetectionResult, mode="complete", progress=None):
        return UnknownAdapter().analyze(root, detection, mode=mode, progress=progress)

    def extract(self, root: Path, progress=None, logger=None):
        raise AdapterOperationBlocked("Extraction Pokémon Studio non prise en charge.")
