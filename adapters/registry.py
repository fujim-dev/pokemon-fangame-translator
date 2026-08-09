from __future__ import annotations

import threading
from pathlib import Path

from .base import AdapterOperationBlocked, DetectionResult, GameAdapter, GameCapability
from .pokemon_essentials import PokemonEssentialsAdapter
from .pokemon_flux import PokemonFluxAdapter
from .probe_isolation import IsolatedProbeRunner
from .unknown import UnknownAdapter


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


class AdapterRegistry:
    def __init__(
        self,
        adapters: tuple[GameAdapter, ...],
        *,
        confidence_threshold: int = 60,
        ambiguity_margin: int = 8,
        probe_runner=None,
    ):
        self.adapters = adapters
        self.confidence_threshold = confidence_threshold
        self.ambiguity_margin = ambiguity_margin
        self.probe_runner = probe_runner or IsolatedProbeRunner()

    def _unknown_from(
        self,
        candidate: DetectionResult | None,
        *,
        ambiguous: bool = False,
        warning: str = "",
    ) -> DetectionResult:
        base = UnknownAdapter().probe(Path("."))
        if candidate is None:
            if not warning:
                return base
            return DetectionResult(
                adapter_id=base.adapter_id,
                display_name=base.display_name,
                confidence=0,
                capabilities=base.capabilities,
                warnings=(warning, *base.warnings),
                adapter_recognized=False,
                write_actions_allowed=False,
            )
        primary_warning = warning or (
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
            warnings=(primary_warning, *candidate.warnings),
            recognized_version=candidate.recognized_version,
            adapter_recognized=False,
            write_actions_allowed=False,
            ambiguous=ambiguous,
        )

    def detect(
        self,
        root: Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> DetectionResult:
        root_input = root.expanduser()
        if not root_input.is_dir():
            return self._unknown_from(
                None,
                warning="Dossier de fangame absent ou inaccessible : actions d'écriture bloquées.",
            )
        if _is_link_or_junction(root_input):
            return self._unknown_from(
                None,
                warning=(
                    "Dossier de fangame redirigé par un lien ou une jonction : "
                    "actions d'écriture bloquées."
                ),
            )
        if cancel_event is None:
            outcome = self.probe_runner.run(self.adapters, root)
        else:
            outcome = self.probe_runner.run(
                self.adapters,
                root,
                cancel_event=cancel_event,
            )
        results = list(outcome.results)
        probe_failures = [
            f"{failure.display_name} ({failure.error_type})"
            for failure in outcome.failures
        ]
        results.sort(key=lambda result: result.confidence, reverse=True)
        if outcome.cancelled:
            candidate = results[0] if results else None
            return self._unknown_from(
                candidate,
                warning=(
                    "Détection annulée avant sa conclusion : diagnostic en lecture seule, "
                    "actions d'écriture bloquées."
                ),
            )
        if probe_failures:
            candidate = results[0] if results else None
            if any(failure.timed_out for failure in outcome.failures):
                return self._unknown_from(
                    candidate,
                    warning=(
                        "Détection incomplète : une ou plusieurs sondes d'adaptateur "
                        "ont expiré ou échoué "
                        f"({', '.join(probe_failures)}). Actions d'écriture bloquées."
                    ),
                )
            return self._unknown_from(
                candidate,
                warning=(
                    "Détection incomplète : une ou plusieurs sondes d'adaptateur ont échoué "
                    f"({', '.join(probe_failures)}). Actions d'écriture bloquées."
                ),
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

    def cancel_active(self) -> None:
        """Annule et détruit tous les workers de sonde encore actifs."""
        self.probe_runner.cancel_all()

    @property
    def active_probe_count(self) -> int:
        return int(getattr(self.probe_runner, "active_count", 0))

    def adapter_for(self, result: DetectionResult) -> GameAdapter:
        if result.adapter_id == UnknownAdapter.adapter_id or not result.adapter_recognized:
            return UnknownAdapter()
        for adapter in self.adapters:
            if adapter.adapter_id == result.adapter_id:
                return adapter
        return UnknownAdapter()


def create_default_registry(*, replacement: GameAdapter | None = None) -> AdapterRegistry:
    adapters: list[GameAdapter] = [PokemonEssentialsAdapter(), PokemonFluxAdapter()]
    if replacement is not None:
        adapters = [
            replacement if adapter.adapter_id == replacement.adapter_id else adapter
            for adapter in adapters
        ]
    return AdapterRegistry(tuple(adapters))


def authorize_adapter_operation(
    root: Path,
    *,
    expected_adapter_id: str,
    capability: GameCapability,
    adapter: GameAdapter | None = None,
    require_write_authorization: bool = False,
) -> DetectionResult:
    """Applique la décision multi-adaptateurs même lors d'un appel direct."""
    detection = create_default_registry(replacement=adapter).detect(root)
    allowed = (
        detection.adapter_id == expected_adapter_id
        and detection.adapter_recognized
        and not detection.ambiguous
        and detection.can(capability)
        and (detection.write_actions_allowed or not require_write_authorization)
    )
    if not allowed:
        detail = detection.warnings[0] if detection.warnings else "structure non reconnue"
        raise AdapterOperationBlocked(
            f"Opération {capability.value} bloquée pour l'adaptateur "
            f"{expected_adapter_id} : {detail}"
        )
    return detection
