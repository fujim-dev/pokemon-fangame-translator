# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from pathlib import Path

from .models import RepairError, RestorationResult
from .planner import REQUIRED_FIELDS, sha256_bytes, sha256_file
from .project_guard import assert_legacy_direct_write_allowed
from safe_io import atomic_write_bytes, read_stable_file


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
    try:
        payload, source_state = read_stable_file(source)
    except OSError as exc:
        raise RepairError("Le CSV à sauvegarder est instable ou redirigé.") from exc
    atomic_write_bytes(destination, payload)
    try:
        _destination_payload, destination_state = read_stable_file(destination)
        _source_again, source_state_after = read_stable_file(source)
    except OSError as exc:
        raise RepairError("Le point de restauration ne peut pas être vérifié.") from exc
    if destination_state.sha256 != source_state.sha256 or source_state_after != source_state:
        raise RepairError("La vérification du point de restauration a échoué.")
    return destination


def restore_exact_backup(csv_path: Path, backup_path: Path) -> None:
    try:
        payload, _backup_state = read_stable_file(backup_path)
    except OSError as exc:
        raise RepairError("La sauvegarde exacte est instable ou redirigée.") from exc
    expected_hash = sha256_bytes(payload)
    atomic_write_bytes(csv_path, payload)
    if sha256_file(csv_path) != expected_hash:
        raise RepairError("La restauration exacte du CSV a échoué.")


def _validate_csv_structure(path: Path) -> None:
    try:
        payload, _state = read_stable_file(path)
        with io.StringIO(payload.decode("utf-8-sig"), newline="") as handle:
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
    assert_legacy_direct_write_allowed(csv_resolved)
    backup_resolved = Path(os.path.abspath(str(backup_path.expanduser())))
    allowed_root = Path(os.path.abspath(str(backup_dir.expanduser())))
    if os.path.normcase(str(backup_resolved.parent)) != os.path.normcase(
        str(allowed_root)
    ):
        raise RepairError("La sauvegarde choisie n'appartient pas au dossier autorisé.")
    if not backup_resolved.is_file() or csv_resolved == backup_resolved:
        raise RepairError("La sauvegarde choisie est introuvable ou invalide.")
    _validate_csv_structure(backup_resolved)
    try:
        _backup_payload, backup_state = read_stable_file(backup_resolved)
    except OSError as exc:
        raise RepairError("La sauvegarde choisie est instable ou redirigée.") from exc
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
        _backup_after, backup_state_after = read_stable_file(backup_resolved)
        if backup_state_after != backup_state:
            raise RepairError("La sauvegarde choisie a changé pendant la restauration.")
    except Exception as exc:
        report_cleanup_error: OSError | None = None
        try:
            report_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            report_cleanup_error = cleanup_exc
        try:
            restore_exact_backup(csv_resolved, safety_backup)
        except Exception as rollback_exc:
            raise RepairError(
                "La restauration et son rollback ont échoué. N'utilisez plus ce CSV ; "
                f"la sauvegarde exacte se trouve ici : {safety_backup}"
            ) from rollback_exc
        if report_cleanup_error is not None:
            raise RepairError(
                "La restauration a été annulée et le CSV restauré, mais un rapport de succès "
                f"invalide n'a pas pu être retiré : {report_path}"
            ) from report_cleanup_error
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
