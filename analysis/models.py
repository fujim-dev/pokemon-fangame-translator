from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


LANGUAGE_CATEGORIES = (
    "francais_probable",
    "anglais_probable",
    "mixte",
    "ambigu",
    "technique_exclu",
)


@dataclass(frozen=True)
class AnalysisIssue:
    code: str
    severity: str
    category: str
    message: str
    relative_path: str = ""
    blocking: bool = False


@dataclass
class CoverageMetrics:
    line_counts: dict[str, int] = field(
        default_factory=lambda: {category: 0 for category in LANGUAGE_CATEGORIES}
    )
    word_counts: dict[str, int] = field(
        default_factory=lambda: {category: 0 for category in LANGUAGE_CATEGORIES}
    )
    character_counts: dict[str, int] = field(
        default_factory=lambda: {category: 0 for category in LANGUAGE_CATEGORIES}
    )
    incomplete_sources: bool = False
    protected_command_lines: int = 0
    method: str = (
        "Classification déterministe par marqueurs lexicaux français/anglais après "
        "retrait des commandes techniques. Les textes courts et noms propres restent ambigus."
    )

    @property
    def total_lines(self) -> int:
        return sum(self.line_counts.values())

    @property
    def assessable_lines(self) -> int:
        return sum(
            self.line_counts[category]
            for category in ("francais_probable", "anglais_probable", "mixte")
        )

    @property
    def french_line_percent(self) -> float:
        if not self.assessable_lines:
            return 0.0
        return round(
            100.0 * self.line_counts["francais_probable"] / self.assessable_lines,
            2,
        )

    @staticmethod
    def _french_percent(counts: dict[str, int]) -> float:
        assessable = sum(
            counts[category]
            for category in ("francais_probable", "anglais_probable", "mixte")
        )
        if not assessable:
            return 0.0
        return round(100.0 * counts["francais_probable"] / assessable, 2)

    @property
    def french_word_percent(self) -> float:
        return self._french_percent(self.word_counts)

    @property
    def french_character_percent(self) -> float:
        return self._french_percent(self.character_counts)

    @property
    def can_claim_complete_coverage(self) -> bool:
        return (
            not self.incomplete_sources
            and self.assessable_lines > 0
            and self.line_counts["anglais_probable"] == 0
            and self.line_counts["mixte"] == 0
            and self.line_counts["ambigu"] == 0
        )

    def to_dict(self) -> dict:
        return {
            "line_counts": dict(self.line_counts),
            "word_counts": dict(self.word_counts),
            "character_counts": dict(self.character_counts),
            "total_lines": self.total_lines,
            "assessable_lines": self.assessable_lines,
            "french_line_percent": self.french_line_percent,
            "french_word_percent": self.french_word_percent,
            "french_character_percent": self.french_character_percent,
            "incomplete_sources": self.incomplete_sources,
            "protected_command_lines": self.protected_command_lines,
            "can_claim_complete_coverage": self.can_claim_complete_coverage,
            "method": self.method,
        }


@dataclass
class DeepAnalysisReport:
    game_label: str
    adapter_id: str
    adapter_display_name: str
    adapter_confidence: int
    mode: str = "complete"
    analyzed_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    files_seen: int = 0
    bytes_seen: int = 0
    extension_counts: dict[str, int] = field(default_factory=dict)
    map_files_found: int = 0
    maps_analyzed: int = 0
    map_events: int = 0
    map_pages: int = 0
    event_commands: int = 0
    common_events_found: int = 0
    common_events_analyzed: int = 0
    message_banks_found: int = 0
    message_banks_analyzed: int = 0
    pbs_files_found: int = 0
    pbs_files_analyzed: int = 0
    pbs_legacy_encoding_files: int = 0
    ruby_script_files: int = 0
    dynamic_script_commands: int = 0
    static_references_checked: int = 0
    missing_static_references: int = 0
    issues: list[AnalysisIssue] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    coverage: CoverageMetrics = field(default_factory=CoverageMetrics)

    @property
    def unreadable_files(self) -> int:
        return sum(issue.code == "unreadable_file" for issue in self.issues)

    @property
    def status(self) -> str:
        if any(issue.blocking or issue.severity == "error" for issue in self.issues):
            return "rouge"
        if self.issues or self.unverified or self.unsupported or self.coverage.incomplete_sources:
            return "jaune"
        return "vert"

    def to_dict(self) -> dict:
        result = asdict(self)
        result["coverage"] = self.coverage.to_dict()
        result["status"] = self.status
        result["unreadable_files"] = self.unreadable_files
        return result
