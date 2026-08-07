from .base import AdapterOperationBlocked, DetectionEvidence, DetectionResult, GameAdapter, GameCapability
from .pokemon_essentials import PokemonEssentialsAdapter
from .registry import AdapterRegistry, create_default_registry
from .unknown import UnknownAdapter

__all__ = [
    "AdapterRegistry",
    "AdapterOperationBlocked",
    "DetectionEvidence",
    "DetectionResult",
    "GameAdapter",
    "GameCapability",
    "PokemonEssentialsAdapter",
    "UnknownAdapter",
    "create_default_registry",
]
