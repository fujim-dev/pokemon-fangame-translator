# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from .models import RepairAction, RepairError, RepairPlan
from .safe_fixes import extract_protected, restore_simple_commands
from safe_io import StableFileState, atomic_write_bundle, read_stable_file


REQUIRED_FIELDS = {"id_stable", "texte_source", "traduction_fr", "statut"}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes((value or "").encode("utf-8"))


def sha256_file(path: Path) -> str:
    try:
        payload, _state = read_stable_file(path)
    except OSError as exc:
        raise RepairError("Lecture stable du projet impossible.") from exc
    return sha256_bytes(payload)


def read_project_csv_payload(payload: bytes) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = payload.decode("utf-8-sig")
        with io.StringIO(text, newline="") as handle:
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


def read_project_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        payload, _state = read_stable_file(path)
    except OSError as exc:
        raise RepairError(f"Lecture du projet impossible : {type(exc).__name__}") from exc
    return read_project_csv_payload(payload)


def _action_id(index: int, row_id: str, source_hash: str, translation_hash: str) -> str:
    payload = f"{index}\x1f{row_id}\x1f{source_hash}\x1f{translation_hash}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def plan_csv_repairs_payload(csv_path: Path, payload: bytes) -> RepairPlan:
    resolved = csv_path.expanduser().resolve()
    _fields, rows = read_project_csv_payload(payload)
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

    csv_hash = sha256_bytes(payload)
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


def plan_csv_repairs(csv_path: Path) -> RepairPlan:
    resolved = csv_path.expanduser().resolve()
    if not resolved.is_file():
        raise RepairError("Le fichier CSV du projet est introuvable.")
    try:
        payload, state = read_stable_file(resolved)
    except OSError as exc:
        raise RepairError("Le fichier CSV du projet ne peut pas être lu de manière stable.") from exc
    plan = plan_csv_repairs_payload(resolved, payload)
    try:
        _payload_again, state_again = read_stable_file(resolved)
    except OSError as exc:
        raise RepairError("Le CSV a disparu pendant la préparation du plan.") from exc
    if state_again != state:
        raise RepairError("Le CSV a changé pendant la préparation du plan. Relancez l'analyse.")
    return plan


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


def save_repair_plan(plan: RepairPlan, path: Path) -> RepairPlan:
    destination = Path(os.path.abspath(str(path.expanduser())))
    if os.path.normcase(str(destination)) == os.path.normcase(
        str(Path(plan.csv_path).resolve())
    ):
        raise RepairError("Le plan de réparation ne peut pas remplacer le CSV.")
    saved = replace(plan, saved_path=str(destination))
    payload = json.dumps(_plan_payload(saved), ensure_ascii=False, indent=2).encode("utf-8")
    try:
        atomic_write_bundle(
            {destination: payload},
            expected_existing_sha256={destination: None},
            expected_existing_signatures={destination: None},
        )
    except (OSError, ValueError) as exc:
        raise RepairError(
            "Le plan n'a pas pu être publié sans écraser un artefact existant."
        ) from exc
    return saved


def assert_saved_plan(plan: RepairPlan) -> StableFileState:
    if not plan.saved_path:
        raise RepairError("Le plan doit être enregistré avant toute application.")
    path = Path(plan.saved_path)
    try:
        raw_payload, plan_state = read_stable_file(path)
        payload = json.loads(raw_payload.decode("utf-8-sig"))
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
    return plan_state
