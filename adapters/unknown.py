from __future__ import annotations

from pathlib import Path

from .base import AdapterOperationBlocked, DetectionResult, GameCapability
from analysis.deep_analyzer import analyze_game


class UnknownAdapter:
    adapter_id = "unknown"
    display_name = "Structure inconnue"

    def analyze(self, root: Path, detection: DetectionResult, mode="complete", progress=None):
        return analyze_game(
            root,
            adapter_id=self.adapter_id,
            adapter_display_name=self.display_name,
            adapter_confidence=detection.confidence,
            mode=mode,
            progress=progress,
        )

    def extract(self, root: Path, progress=None, logger=None) -> tuple[list[dict], list[str]]:
        del root, progress, logger
        raise AdapterOperationBlocked(
            "Extraction interdite : aucun adaptateur compatible n'a été identifié."
        )

    def probe(self, root: Path) -> DetectionResult:
        del root
        return DetectionResult(
            adapter_id=self.adapter_id,
            display_name=self.display_name,
            confidence=0,
            capabilities=frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            warnings=(
                "Structure non reconnue : traduction et reconstruction désactivées.",
            ),
            adapter_recognized=False,
            write_actions_allowed=False,
        )
