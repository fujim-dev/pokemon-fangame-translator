# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from .models import RepairAction, RepairError, RepairPlan
from .safe_fixes import extract_protected, restore_simple_commands
from safe_io import atomic_write_text


REQUIRED_FIELDS = {"id_stable", "texte_source", "traduction_fr", "statut"}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes((value or "").encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_project_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            fields = list(reader.fieldnames or [])
            missing = sorted(REQUIRED_FIELDS - set(fields))
            if missing:
                raise RepairError("CSV incompatible, colonnes manquantes : " + ", ".join(missing))
            rows = [dict(row) for row in reader]
    except RepairError:
        raise
    except Exception as exc:
        raise RepairError(f"Lecture du projet impossible : {type(exc).__name__}") from exc
    return fields, rows


def _action_id(index: int, row_id: str, source_hash: str, translation_hash: str) -> str:
    payload = f"{index}\x1f{row_id}\x1f{source_hash}\x1f{translation_hash}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def plan_csv_repairs(csv_path: Path) -> RepairPlan:
    resolved = csv_path.expanduser().resolve()
    if not resolved.is_file():
        raise RepairError("Le fichier CSV du projet est introuvable.")
    _fields, rows = read_project_csv(resolved)
    actions: list[RepairAction] = []

    for index, row in enumerate(rows):
        source = row.get("texte_source") or ""
        translation = row.get("traduction_fr") or ""
        if not source or not translation.strip():
            continue
        expected = extract_protected(source)
        found = extract_protected(translation)
        if expected == found:
            continue

        repaired, technical_actions, success = restore_simple_commands(source, translation)
        source_hash = sha256_text(source)
        before_hash = sha256_text(translation)
        row_id = (row.get("id_stable") or f"ligne-{index + 1}").strip()
        safe = success and repaired != translation and extract_protected(repaired) == expected
        decision = "sure" if safe else "verification_humaine_requise"
        reason = (
            "Restauration déterministe de commandes techniques en bordure."
            if safe
            else "Position interne, commande supplémentaire ou cas non déterministe."
        )
        actions.append(
            RepairAction(
                action_id=_action_id(index, row_id, source_hash, before_hash),
                row_index=index,
                row_id=row_id,
                repair_type="restauration_commandes_protegees",
                decision=decision,
                reason=reason,
                source_hash=source_hash,
                translation_hash_before=before_hash,
                translation_hash_after=sha256_text(repaired) if safe else "",
                commands_expected=tuple(expected),
                commands_found=tuple(found),
                technical_actions=tuple(technical_actions),
            )
        )

    csv_hash = sha256_file(resolved)
    plan_seed = json.dumps(
        {
            "csv_sha256": csv_hash,
            "actions": [(action.action_id, action.decision) for action in actions],
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return RepairPlan(
        plan_id=hashlib.sha256(plan_seed).hexdigest()[:20],
        csv_path=str(resolved),
        csv_sha256=csv_hash,
        created_at=datetime.now().isoformat(timespec="seconds"),
        row_count=len(rows),
        actions=tuple(actions),
    )


def _plan_payload(plan: RepairPlan) -> dict[str, object]:
    return {
        "version": "1.1",
        "plan_id": plan.plan_id,
        "date": plan.created_at,
        "csv": plan.csv_path,
        "csv_sha256": plan.csv_sha256,
        "lignes": plan.row_count,
        "reparations_sures": len(plan.safe_actions),
        "verification_humaine_requise": len(plan.human_actions),
        "actions": [asdict(action) for action in plan.actions],
        "plan_enregistre": plan.saved_path,
        "confidentialite": "Aucun dialogue complet n'est enregistré dans ce plan.",
    }


def _atomic_write_text(path: Path, content: str) -> None:
    atomic_write_text(path, content, encoding="utf-8")


def save_repair_plan(plan: RepairPlan, path: Path) -> RepairPlan:
    destination = path.expanduser().resolve()
    if destination == Path(plan.csv_path).resolve():
        raise RepairError("Le plan de réparation ne peut pas remplacer le CSV.")
    saved = replace(plan, saved_path=str(destination))
    _atomic_write_text(
        destination,
        json.dumps(_plan_payload(saved), ensure_ascii=False, indent=2),
    )
    return saved


def assert_saved_plan(plan: RepairPlan) -> None:
    if not plan.saved_path:
        raise RepairError("Le plan doit être enregistré avant toute application.")
    path = Path(plan.saved_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RepairError("Le plan enregistré est introuvable ou illisible.") from exc
    if (
        payload.get("plan_id") != plan.plan_id
        or payload.get("csv_sha256") != plan.csv_sha256
        or payload.get("csv") != plan.csv_path
        or payload.get("plan_enregistre") != plan.saved_path
    ):
        raise RepairError("Le plan enregistré ne correspond plus au plan en mémoire.")
    expected_actions = json.loads(
        json.dumps([asdict(action) for action in plan.actions], ensure_ascii=False)
    )
    if payload.get("actions") != expected_actions:
        raise RepairError("Les réparations du plan enregistré ont changé.")
