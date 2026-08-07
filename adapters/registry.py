from __future__ import annotations

from pathlib import Path

from .base import DetectionResult, GameAdapter, GameCapability
from .pokemon_essentials import PokemonEssentialsAdapter
from .pokemon_flux import PokemonFluxAdapter
from .unknown import UnknownAdapter


class AdapterRegistry:
    def __init__(
        self,
        adapters: tuple[GameAdapter, ...],
        *,
        confidence_threshold: int = 60,
        ambiguity_margin: int = 8,
    ):
        self.adapters = adapters
        self.confidence_threshold = confidence_threshold
        self.ambiguity_margin = ambiguity_margin

    def _unknown_from(self, candidate: DetectionResult | None, *, ambiguous: bool = False) -> DetectionResult:
        base = UnknownAdapter().probe(Path("."))
        if candidate is None:
            return base
        warning = (
            "Détection ambiguë : plusieurs adaptateurs ont des scores trop proches."
            if ambiguous
            else "Confiance insuffisante : actions d'écriture bloquées."
        )
        return DetectionResult(
            adapter_id=base.adapter_id,
            display_name=base.display_name,
            confidence=candidate.confidence,
            capabilities=frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            evidence=candidate.evidence,
            warnings=(warning, *candidate.warnings),
            recognized_version=candidate.recognized_version,
            adapter_recognized=False,
            write_actions_allowed=False,
            ambiguous=ambiguous,
        )

    def detect(self, root: Path) -> DetectionResult:
        results = sorted(
            (adapter.probe(root) for adapter in self.adapters),
            key=lambda result: result.confidence,
            reverse=True,
        )
        if not results:
            return self._unknown_from(None)

        top = results[0]
        eligible = top.adapter_recognized and top.confidence >= self.confidence_threshold
        if not eligible:
            return self._unknown_from(top)

        if len(results) >= 2:
            second = results[1]
            if (
                second.adapter_recognized
                and second.confidence >= self.confidence_threshold
                and top.confidence - second.confidence < self.ambiguity_margin
            ):
                return self._unknown_from(top, ambiguous=True)
        return top

    def adapter_for(self, result: DetectionResult) -> GameAdapter:
        if result.adapter_id == UnknownAdapter.adapter_id or not result.adapter_recognized:
            return UnknownAdapter()
        for adapter in self.adapters:
            if adapter.adapter_id == result.adapter_id:
                return adapter
        return UnknownAdapter()


def create_default_registry() -> AdapterRegistry:
    return AdapterRegistry((PokemonEssentialsAdapter(), PokemonFluxAdapter()))
