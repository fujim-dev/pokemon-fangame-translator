from __future__ import annotations

from pathlib import Path

from .base import AdapterOperationBlocked, DetectionResult, GameCapability


class UnknownAdapter:
    adapter_id = "unknown"
    display_name = "Structure inconnue"

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
            capabilities=frozenset({GameCapability.ANALYZE}),
            warnings=(
                "Structure non reconnue : traduction et reconstruction désactivées.",
            ),
            write_actions_allowed=False,
        )
