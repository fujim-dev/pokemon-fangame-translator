from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class GameCapability(str, Enum):
    ANALYZE = "analyze"
    DEEP_ANALYZE = "deep_analyze"
    EXTRACT = "extract"
    TRANSLATE = "translate"
    RECONSTRUCT = "reconstruct"


class AdapterOperationBlocked(RuntimeError):
    """Action refusée parce que l'adaptateur ne garantit pas sa compatibilité."""


@dataclass(frozen=True)
class DetectionEvidence:
    evidence_id: str
    relative_path: str
    observed: str
    weight: int
    explanation: str


@dataclass(frozen=True)
class DetectionResult:
    adapter_id: str
    display_name: str
    confidence: int
    capabilities: frozenset[GameCapability]
    evidence: tuple[DetectionEvidence, ...] = ()
    warnings: tuple[str, ...] = ()
    recognized_version: str = ""
    adapter_recognized: bool = True
    write_actions_allowed: bool = False
    ambiguous: bool = False

    def can(self, capability: GameCapability) -> bool:
        return capability in self.capabilities


class GameAdapter(Protocol):
    adapter_id: str
    display_name: str

    def probe(self, root: Path) -> DetectionResult:
        """Analyse uniquement la structure du dossier, sans aucune écriture."""
        ...

    def extract(self, root: Path, progress=None, logger=None) -> tuple[list[dict], list[str]]:
        """Extrait les textes via le chemin propre à l'adaptateur."""
        ...

    def analyze(self, root: Path, detection: DetectionResult, mode="complete", progress=None):
        """Produit une validation analytique statique sans exécuter le jeu."""
        ...
