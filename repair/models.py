# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepairAction:
    action_id: str
    row_index: int
    row_id: str
    repair_type: str
    decision: str
    reason: str
    source_hash: str
    translation_hash_before: str
    translation_hash_after: str
    commands_expected: tuple[str, ...]
    commands_found: tuple[str, ...]
    technical_actions: tuple[str, ...]


@dataclass(frozen=True)
class RepairPlan:
    plan_id: str
    csv_path: str
    csv_sha256: str
    created_at: str
    row_count: int
    actions: tuple[RepairAction, ...]
    saved_path: str = ""

    @property
    def safe_actions(self) -> tuple[RepairAction, ...]:
        return tuple(action for action in self.actions if action.decision == "sure")

    @property
    def human_actions(self) -> tuple[RepairAction, ...]:
        return tuple(
            action
            for action in self.actions
            if action.decision == "verification_humaine_requise"
        )


@dataclass(frozen=True)
class RepairResult:
    plan_id: str
    csv_path: str
    backup_path: str
    journal_path: str
    applied: int
    human_required: int
    rolled_back: bool
    sha256_before: str
    sha256_after: str


@dataclass(frozen=True)
class RestorationResult:
    csv_path: str
    restored_backup_path: str
    safety_backup_path: str
    report_path: str
    sha256_restored: str


class RepairError(RuntimeError):
    pass
