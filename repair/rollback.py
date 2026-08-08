# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .models import RepairError, RestorationResult
from .planner import REQUIRED_FIELDS, sha256_bytes, sha256_file
from safe_io import atomic_write_bytes


def timestamp_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    atomic_write_bytes(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def create_exact_backup(csv_path: Path, backup_dir: Path, prefix: str) -> Path:
    source = csv_path.expanduser().resolve()
    destination_dir = backup_dir.expanduser().resolve()
    if not source.is_file():
        raise RepairError("Le CSV à sauvegarder est introuvable.")
    destination = destination_dir / f"{prefix}_{timestamp_token()}.csv"
    atomic_write_bytes(destination, source.read_bytes())
    if sha256_file(destination) != sha256_file(source):
        raise RepairError("La vérification du point de restauration a échoué.")
    return destination


def restore_exact_backup(csv_path: Path, backup_path: Path) -> None:
    payload = backup_path.read_bytes()
    expected_hash = sha256_bytes(payload)
    atomic_write_bytes(csv_path, payload)
    if sha256_file(csv_path) != expected_hash:
        raise RepairError("La restauration exacte du CSV a échoué.")


def _validate_csv_structure(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            fields = set(reader.fieldnames or [])
            if not REQUIRED_FIELDS.issubset(fields):
                raise RepairError("La sauvegarde choisie n'est pas un projet CSV compatible.")
            list(reader)
    except RepairError:
        raise
    except Exception as exc:
        raise RepairError("La sauvegarde choisie est illisible.") from exc


def restore_csv_backup(
    csv_path: Path,
    backup_path: Path,
    *,
    backup_dir: Path,
    report_dir: Path,
) -> RestorationResult:
    csv_resolved = csv_path.expanduser().resolve()
    backup_resolved = backup_path.expanduser().resolve()
    allowed_root = backup_dir.expanduser().resolve()
    try:
        backup_resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise RepairError("La sauvegarde choisie n'appartient pas au dossier autorisé.") from exc
    if not backup_resolved.is_file() or csv_resolved == backup_resolved:
        raise RepairError("La sauvegarde choisie est introuvable ou invalide.")
    _validate_csv_structure(backup_resolved)
    safety_backup = create_exact_backup(
        csv_resolved,
        allowed_root,
        "avant_restauration",
    )
    report_path = report_dir.expanduser().resolve() / f"RAPPORT_RESTAURATION_{timestamp_token()}.json"
    try:
        restore_exact_backup(csv_resolved, backup_resolved)
        _validate_csv_structure(csv_resolved)
        restored_hash = sha256_file(csv_resolved)
        atomic_write_json(
            report_path,
            {
                "version": "1.1",
                "operation": "restauration_csv",
                "statut": "reussie",
                "csv": str(csv_resolved),
                "sauvegarde_restauree": str(backup_resolved),
                "sauvegarde_de_securite": str(safety_backup),
                "sha256_restaure": restored_hash,
                "confidentialite": "Aucun dialogue complet n'est enregistré.",
            },
        )
    except Exception as exc:
        try:
            restore_exact_backup(csv_resolved, safety_backup)
        except Exception as rollback_exc:
            raise RepairError(
                "La restauration et son rollback ont échoué. N'utilisez plus ce CSV ; "
                f"la sauvegarde exacte se trouve ici : {safety_backup}"
            ) from rollback_exc
        raise RepairError(
            "La restauration ou son journal a échoué ; rollback effectué."
        ) from exc
    return RestorationResult(
        csv_path=str(csv_resolved),
        restored_backup_path=str(backup_resolved),
        safety_backup_path=str(safety_backup),
        report_path=str(report_path),
        sha256_restored=restored_hash,
    )
