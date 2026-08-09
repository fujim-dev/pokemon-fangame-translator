# SPDX-License-Identifier: GPL-3.0-or-later
"""Réparation/restauration Essentials sous la transaction du projet Studio."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from safe_io import read_stable_file
from translation_project import (
    TranslationProjectError,
    TranslationProjectSession,
    inspect_csv_structure,
)

from .engine import build_repair_candidate
from .models import RepairError, RepairPlan, RepairResult, RestorationResult
from .planner import assert_saved_plan, sha256_bytes
from .rollback import timestamp_token


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.expanduser().resolve())) == os.path.normcase(
        str(right.expanduser().resolve())
    )


def _inactive_resume(reason: str) -> dict[str, object]:
    return {
        "version": "1.0",
        "active": False,
        "total": 0,
        "completed": 0,
        "remaining": 0,
        "reason": reason,
    }


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _session_snapshot(session: TranslationProjectSession):
    snapshot = session.snapshot
    if not session.writable or snapshot is None:
        raise RepairError(
            session.read_only_reason
            or "La provenance du projet n'est pas démontrée. Une nouvelle extraction est requise."
        )
    return snapshot


def apply_repair_plan_transactional(
    session: TranslationProjectSession,
    plan: RepairPlan,
) -> RepairResult:
    """Publie réparation, état, reprise, sauvegarde et journal comme un seul lot."""
    snapshot = _session_snapshot(session)
    if not _same_path(Path(plan.csv_path), session.csv_path):
        raise RepairError("Le plan de réparation appartient à un autre CSV.")
    plan_state = assert_saved_plan(plan)
    plan_path = Path(plan.saved_path).expanduser().resolve()
    try:
        current_payload = session.read_csv_payload()
    except TranslationProjectError as exc:
        raise RepairError(str(exc)) from exc
    if sha256_bytes(current_payload) != plan.csv_sha256:
        raise RepairError("Le CSV a changé depuis la création du plan. Relancez l'analyse.")
    candidate = build_repair_candidate(plan, current_payload)
    candidate_sha256 = sha256_bytes(candidate)
    token = timestamp_token()
    backup_path = (
        session.project_dir
        / "Sauvegardes"
        / f"avant_reparation_{plan.plan_id}_{token}.csv"
    )
    journal_path = (
        session.project_dir / "Rapports" / f"JOURNAL_REPARATION_{token}.json"
    )
    journal = _json_bytes(
        {
            "version": "1.1",
            "operation": "reparation_commandes_protegees",
            "mode": "transaction_provenance_essentials",
            "plan_id": plan.plan_id,
            "plan_sha256": plan_state.sha256,
            "statut": "reussie",
            "csv": session.csv_path.name,
            "sauvegarde": backup_path.name,
            "sha256_avant": plan.csv_sha256,
            "sha256_apres": candidate_sha256,
            "source_manifest_sha256": snapshot.source_manifest_sha256,
            "extraction_id": snapshot.extraction_id,
            "revision_attendue": snapshot.revision + 1,
            "reparations_appliquees": len(plan.safe_actions),
            "verification_humaine_requise": len(plan.human_actions),
            "actions": [
                {
                    "action_id": action.action_id,
                    "ligne_id": action.row_id,
                    "type": action.repair_type,
                    "actions_techniques": list(action.technical_actions),
                }
                for action in plan.safe_actions
            ],
            "confidentialite": "Aucun dialogue complet n'est enregistré.",
        }
    )
    try:
        session.save(
            candidate,
            resume_state=_inactive_resume("reparation_csv_transactionnelle"),
            synchronized_artifacts={
                backup_path: current_payload,
                journal_path: journal,
            },
            guarded_artifacts={plan_path: plan_state},
        )
    except TranslationProjectError as exc:
        raise RepairError(str(exc)) from exc
    return RepairResult(
        plan_id=plan.plan_id,
        csv_path=str(session.csv_path),
        backup_path=str(backup_path),
        journal_path=str(journal_path),
        applied=len(plan.safe_actions),
        human_required=len(plan.human_actions),
        rolled_back=False,
        sha256_before=plan.csv_sha256,
        sha256_after=candidate_sha256,
    )


def restore_csv_backup_transactional(
    session: TranslationProjectSession,
    backup_path: Path,
) -> RestorationResult:
    """Restaure un point du même projet sans désynchroniser sa provenance."""
    snapshot = _session_snapshot(session)
    allowed_root = session.project_dir / "Sauvegardes"
    requested = Path(os.path.abspath(str(backup_path.expanduser())))
    if os.path.normcase(str(requested.parent)) != os.path.normcase(str(allowed_root)):
        raise RepairError("La sauvegarde choisie n'appartient pas au projet courant.")
    if not requested.name.startswith("avant_"):
        raise RepairError("La sauvegarde choisie n'est pas un point de restauration reconnu.")
    try:
        restored_payload, backup_state = read_stable_file(requested)
    except OSError as exc:
        raise RepairError("La sauvegarde choisie est absente, redirigée ou instable.") from exc

    try:
        current_payload = session.read_csv_payload()
        candidate = inspect_csv_structure(restored_payload)
    except TranslationProjectError as exc:
        raise RepairError(str(exc)) from exc
    if candidate.immutable_sha256 != snapshot.immutable_rows_sha256:
        raise RepairError(
            "La sauvegarde appartient à une autre extraction ou ses données sources ont changé."
        )
    token = timestamp_token()
    safety_backup_path = (
        allowed_root / f"avant_restauration_transactionnelle_{token}.csv"
    )
    report_path = (
        session.project_dir / "Rapports" / f"RAPPORT_RESTAURATION_{token}.json"
    )
    restored_sha256 = hashlib.sha256(restored_payload).hexdigest()
    report = _json_bytes(
        {
            "version": "1.1",
            "operation": "restauration_csv",
            "mode": "transaction_provenance_essentials",
            "statut": "reussie",
            "csv": session.csv_path.name,
            "sauvegarde_restauree": requested.name,
            "sauvegarde_restauree_sha256": backup_state.sha256,
            "sauvegarde_de_securite": safety_backup_path.name,
            "sha256_avant": hashlib.sha256(current_payload).hexdigest(),
            "sha256_restaure": restored_sha256,
            "source_manifest_sha256": snapshot.source_manifest_sha256,
            "extraction_id": snapshot.extraction_id,
            "revision_attendue": snapshot.revision + 1,
            "confidentialite": "Aucun dialogue complet n'est enregistré.",
        }
    )
    try:
        session.save(
            restored_payload,
            resume_state=_inactive_resume("restauration_csv_transactionnelle"),
            synchronized_artifacts={
                safety_backup_path: current_payload,
                report_path: report,
            },
            guarded_artifacts={requested: backup_state},
        )
    except TranslationProjectError as exc:
        raise RepairError(str(exc)) from exc
    return RestorationResult(
        csv_path=str(session.csv_path),
        restored_backup_path=str(requested),
        safety_backup_path=str(safety_backup_path),
        report_path=str(report_path),
        sha256_restored=restored_sha256,
    )
