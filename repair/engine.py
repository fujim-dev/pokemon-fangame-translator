# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

from .models import RepairError, RepairPlan, RepairResult
from .planner import (
    assert_saved_plan,
    read_project_csv,
    sha256_file,
    sha256_text,
)
from .rollback import (
    atomic_write_bytes,
    atomic_write_json,
    create_exact_backup,
    restore_exact_backup,
    timestamp_token,
)
from .safe_fixes import extract_protected, restore_simple_commands
from safe_io import atomic_text_writer


REPAIR_METADATA_FIELDS = [
    "niveau_relecture",
    "alertes_relecture",
    "origine_traduction",
]


def _write_project_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with atomic_text_writer(path, encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _validate_applied_rows(
    path: Path,
    original_rows: list[dict[str, str]],
    plan: RepairPlan,
) -> None:
    _fields, written_rows = read_project_csv(path)
    if len(written_rows) != len(original_rows) or len(written_rows) != plan.row_count:
        raise RepairError("Le nombre de lignes a changé pendant la réparation.")
    safe_by_index = {action.row_index: action for action in plan.safe_actions}
    allowed_fields = {
        "traduction_fr",
        "statut",
        "niveau_relecture",
        "alertes_relecture",
        "origine_traduction",
    }
    for index, (before, after) in enumerate(zip(original_rows, written_rows)):
        action = safe_by_index.get(index)
        if not action:
            for field, value in before.items():
                if after.get(field, "") != (value or ""):
                    raise RepairError(f"Une ligne hors plan a changé : {index + 1}.")
            continue
        for field, value in before.items():
            if field not in allowed_fields and after.get(field, "") != (value or ""):
                raise RepairError(f"Un champ hors plan a changé : {action.row_id}.")
        if sha256_text(after.get("texte_source", "")) != action.source_hash:
            raise RepairError(f"La source a changé : {action.row_id}.")
        if sha256_text(after.get("traduction_fr", "")) != action.translation_hash_after:
            raise RepairError(f"La réparation relue diffère du plan : {action.row_id}.")
        if tuple(extract_protected(after.get("traduction_fr", ""))) != action.commands_expected:
            raise RepairError(f"Les commandes restent invalides : {action.row_id}.")


def _failure_report(
    report_dir: Path,
    plan: RepairPlan,
    backup: Path,
    error: Exception,
) -> Path:
    path = report_dir / f"RAPPORT_REPARATION_ECHEC_{timestamp_token()}.json"
    atomic_write_json(
        path,
        {
            "version": "1.1",
            "operation": "reparation_commandes_protegees",
            "plan_id": plan.plan_id,
            "statut": "echec_avec_rollback",
            "sauvegarde_restauree": str(backup),
            "erreur_type": type(error).__name__,
            "actions_prevues": len(plan.safe_actions),
            "confidentialite": "Aucun dialogue complet n'est enregistré.",
        },
    )
    return path


def apply_repair_plan(
    plan: RepairPlan,
    *,
    backup_dir: Path,
    report_dir: Path,
    post_write_validator: Callable[[Path], None] | None = None,
) -> RepairResult:
    assert_saved_plan(plan)
    if not plan.safe_actions:
        raise RepairError("Le plan ne contient aucune réparation sûre à appliquer.")
    csv_path = Path(plan.csv_path).expanduser().resolve()
    if sha256_file(csv_path) != plan.csv_sha256:
        raise RepairError("Le CSV a changé depuis la création du plan. Relancez l'analyse.")

    fields, original_rows = read_project_csv(csv_path)
    if len(original_rows) != plan.row_count:
        raise RepairError("Le CSV ne correspond plus au plan de réparation.")
    if sha256_file(csv_path) != plan.csv_sha256:
        raise RepairError("Le CSV a changé pendant sa lecture. Relancez l'analyse.")
    for field in REPAIR_METADATA_FIELDS:
        if field not in fields:
            fields.append(field)
    rows = [dict(row) for row in original_rows]
    backup = create_exact_backup(
        csv_path,
        backup_dir,
        f"avant_reparation_{plan.plan_id}",
    )
    if sha256_file(backup) != plan.csv_sha256 or sha256_file(csv_path) != plan.csv_sha256:
        raise RepairError("Le CSV a changé avant l'application. Aucune réparation n'a été appliquée.")

    try:
        for action in sorted(plan.safe_actions, key=lambda item: item.row_index):
            if not 0 <= action.row_index < len(rows):
                raise RepairError("Une réparation vise une ligne inexistante.")
            row = rows[action.row_index]
            row_id = (row.get("id_stable") or f"ligne-{action.row_index + 1}").strip()
            source = row.get("texte_source") or ""
            translation = row.get("traduction_fr") or ""
            if row_id != action.row_id:
                raise RepairError("L'identifiant d'une ligne a changé depuis le plan.")
            if sha256_text(source) != action.source_hash:
                raise RepairError(f"La source a changé : {action.row_id}.")
            if sha256_text(translation) != action.translation_hash_before:
                raise RepairError(f"La traduction a changé : {action.row_id}.")
            repaired, _technical_actions, success = restore_simple_commands(source, translation)
            if not success or sha256_text(repaired) != action.translation_hash_after:
                raise RepairError(f"La correction n'est plus déterministe : {action.row_id}.")
            row["traduction_fr"] = repaired
            row["statut"] = "À vérifier"
            row["niveau_relecture"] = "verifier"
            row["alertes_relecture"] = "Commandes techniques restaurées ; relecture humaine requise"
            row["origine_traduction"] = "reparation_commandes_sure"

        _write_project_csv(csv_path, fields, rows)
        _validate_applied_rows(csv_path, original_rows, plan)
        if post_write_validator:
            post_write_validator(csv_path)
    except Exception as exc:
        try:
            restore_exact_backup(csv_path, backup)
        except Exception as rollback_exc:
            raise RepairError(
                "La validation et le rollback ont échoué. N'utilisez plus ce CSV ; "
                f"la sauvegarde exacte se trouve ici : {backup}"
            ) from rollback_exc
        try:
            _failure_report(report_dir.expanduser().resolve(), plan, backup, exc)
        except Exception:
            pass
        raise RepairError(
            "La validation a échoué ; rollback effectué depuis le point de restauration."
        ) from exc

    try:
        after_hash = sha256_file(csv_path)
        journal_path = report_dir.expanduser().resolve() / f"JOURNAL_REPARATION_{timestamp_token()}.json"
        atomic_write_json(
            journal_path,
            {
                "version": "1.1",
                "operation": "reparation_commandes_protegees",
                "plan_id": plan.plan_id,
                "statut": "reussie",
                "csv": str(csv_path),
                "sauvegarde": str(backup),
                "sha256_avant": plan.csv_sha256,
                "sha256_apres": after_hash,
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
            },
        )
    except Exception as exc:
        try:
            restore_exact_backup(csv_path, backup)
        except Exception as rollback_exc:
            raise RepairError(
                "Le journal et le rollback ont échoué. Restaurez manuellement la sauvegarde : "
                f"{backup}"
            ) from rollback_exc
        try:
            _failure_report(report_dir.expanduser().resolve(), plan, backup, exc)
        except Exception:
            pass
        raise RepairError(
            "Le journal n'a pas pu être validé ; rollback effectué."
        ) from exc
    return RepairResult(
        plan_id=plan.plan_id,
        csv_path=str(csv_path),
        backup_path=str(backup),
        journal_path=str(journal_path),
        applied=len(plan.safe_actions),
        human_required=len(plan.human_actions),
        rolled_back=False,
        sha256_before=plan.csv_sha256,
        sha256_after=after_hash,
    )
