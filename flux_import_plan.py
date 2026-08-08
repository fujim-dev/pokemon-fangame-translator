# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plan d'import Pokémon Flux déterministe et strictement en mémoire."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from adapters import PokemonFluxAdapter
from flux_import_validator import (
    ACCEPTED_IMPORT_STATUSES,
    FluxImportValidationContext,
    validate_flux_import_context,
)


MAX_TRANSLATION_BYTES = 1024 * 1024
SUPPORTED_SOURCE_KINDS = frozenset(
    {
        "messages_game",
        "messages",
        "map_events",
        "common_events",
    }
)


class FluxImportPlanError(RuntimeError):
    """Le CSV ne permet pas de construire un plan Flux non ambigu."""


@dataclass(frozen=True)
class FluxImportPlanItem:
    id_stable: str
    source_kind: str
    internal_path: str
    structural_path: tuple[object, ...]
    source_sha256: str
    current_value_sha256: str
    replacement_parts: tuple[bytes, ...]
    decision: str
    reason: str

    @property
    def applicable(self) -> bool:
        return self.decision == "applicable"

    @property
    def replacement_sha256(self) -> str:
        digest = hashlib.sha256()
        for part in self.replacement_parts:
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
        return digest.hexdigest()


@dataclass(frozen=True)
class FluxImportPlan:
    game_root: Path
    fpk_path: Path
    csv_path: Path
    adapter_version: str
    source_fpk_sha256: str
    source_csv_sha256: str
    items: tuple[FluxImportPlanItem, ...]
    fingerprint: str

    @property
    def applicable_items(self) -> tuple[FluxImportPlanItem, ...]:
        return tuple(item for item in self.items if item.applicable)

    @property
    def excluded_items(self) -> tuple[FluxImportPlanItem, ...]:
        return tuple(item for item in self.items if not item.applicable)


def _parse_structural_path(value: str) -> tuple[object, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise FluxImportPlanError("Chemin structurel Flux illisible.") from exc
    if not isinstance(parsed, list) or not parsed:
        raise FluxImportPlanError("Chemin structurel Flux vide ou invalide.")
    result: list[object] = []
    for token in parsed:
        if isinstance(token, bool) or not isinstance(token, (str, int)):
            raise FluxImportPlanError("Jeton de chemin structurel Flux non pris en charge.")
        if isinstance(token, str) and (not token or "\x00" in token):
            raise FluxImportPlanError("Jeton de chemin structurel Flux invalide.")
        result.append(token)
    return tuple(result)


def _validate_internal_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized.startswith("Data/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\x00" in normalized
    ):
        raise FluxImportPlanError("Chemin interne Flux non sûr.")
    return normalized


def _replacement_parts(row: dict[str, str]) -> tuple[bytes, ...]:
    translation = row.get("traduction_fr", "")
    if not translation.strip():
        raise FluxImportPlanError("Une ligne applicable possède une traduction vide.")
    if row.get("type") == "Dialogue":
        match = re.fullmatch(r"lignes:(\d+)", row.get("sous_index", ""))
        if match is None:
            raise FluxImportPlanError("Nombre de lignes du dialogue Flux introuvable.")
        expected_count = int(match.group(1))
        source_parts = row.get("texte_source", "").split(r"\n")
        translated_parts = translation.split(r"\n")
        if expected_count < 1 or len(source_parts) != expected_count:
            raise FluxImportPlanError("Structure source du dialogue Flux incohérente.")
        if len(translated_parts) != expected_count:
            raise FluxImportPlanError(
                "La traduction Flux ne conserve pas le nombre de lignes du dialogue."
            )
    else:
        translated_parts = [translation]
    encoded = tuple(part.encode("utf-8", errors="strict") for part in translated_parts)
    if any(len(part) > MAX_TRANSLATION_BYTES for part in encoded):
        raise FluxImportPlanError("Une traduction Flux dépasse la limite de sécurité.")
    return encoded


def _plan_fingerprint(
    context: FluxImportValidationContext,
    items: tuple[FluxImportPlanItem, ...],
) -> str:
    digest = hashlib.sha256()
    for value in (
        "pft_flux_import_plan_v1",
        context.report.adapter_version,
        context.report.fpk_sha256_before,
        context.report.csv_sha256,
    ):
        payload = value.encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    for item in items:
        for value in (
            item.id_stable,
            item.source_kind,
            item.internal_path,
            json.dumps(item.structural_path, ensure_ascii=False, separators=(",", ":")),
            item.source_sha256,
            item.current_value_sha256,
            item.decision,
            item.reason,
            item.replacement_sha256,
        ):
            payload = value.encode("utf-8")
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def build_flux_import_plan(
    game_root: Path,
    csv_path: Path,
    *,
    adapter: PokemonFluxAdapter | None = None,
    progress=None,
    logger=None,
) -> FluxImportPlan:
    """Valide puis fige les remplacements Flux sans écrire aucun fichier."""
    context = validate_flux_import_context(
        game_root,
        csv_path,
        adapter=adapter,
        progress=progress,
        logger=logger,
    )
    report = context.report
    if not report.structurally_valid:
        raise FluxImportPlanError("Le CSV Flux contient une anomalie bloquante.")
    if report.extraction_warnings:
        raise FluxImportPlanError(
            "Le plan Flux reste bloqué car la réextraction a produit des avertissements."
        )
    if not report.ready_for_future_import:
        raise FluxImportPlanError("Aucune traduction Flux acceptée et sûre à planifier.")

    csv_by_id = {row["id_stable"]: row for row in context.csv_rows}
    items: list[FluxImportPlanItem] = []
    for expected in sorted(context.expected_rows, key=lambda row: row["id_stable"]):
        row = csv_by_id[expected["id_stable"]]
        source_kind = expected["source_flux"]
        internal_path = _validate_internal_path(expected["fichier"])
        structural_path = _parse_structural_path(expected["chemin_structurel"])
        applicable = (
            row.get("statut", "").strip() in ACCEPTED_IMPORT_STATUSES
            and bool(row.get("traduction_fr", "").strip())
        )
        if applicable and source_kind not in SUPPORTED_SOURCE_KINDS:
            raise FluxImportPlanError(f"Source Flux non prise en charge : {source_kind}")
        if (
            applicable
            and source_kind == "messages_game"
            and not re.fullmatch(r"[0-9a-f]{64}", expected["empreinte_valeur_actuelle"])
        ):
            raise FluxImportPlanError("Valeur courante messages_game non vérifiable.")
        if applicable and source_kind != "messages_game" and (
            "hash_key" in structural_path
            or (
                len(structural_path) >= 3
                and structural_path[-3] == "dict"
                and structural_path[-1] == "key"
            )
        ):
            raise FluxImportPlanError(
                "La modification directe d'une clé Ruby reste volontairement bloquée."
            )
        parts = _replacement_parts(row) if applicable else ()
        items.append(
            FluxImportPlanItem(
                id_stable=expected["id_stable"],
                source_kind=source_kind,
                internal_path=internal_path,
                structural_path=structural_path,
                source_sha256=expected["empreinte_source"],
                current_value_sha256=expected["empreinte_valeur_actuelle"],
                replacement_parts=parts,
                decision="applicable" if applicable else "exclue",
                reason="Traduction acceptée et validée" if applicable else "Traduction non acceptée",
            )
        )

    frozen_items = tuple(items)
    if len(tuple(item for item in frozen_items if item.applicable)) != report.eligible_translations:
        raise FluxImportPlanError("Le décompte du plan Flux diffère du validateur indépendant.")
    return FluxImportPlan(
        game_root=context.game_root,
        fpk_path=context.fpk_path,
        csv_path=context.csv_path,
        adapter_version=report.adapter_version,
        source_fpk_sha256=report.fpk_sha256_before,
        source_csv_sha256=report.csv_sha256,
        items=frozen_items,
        fingerprint=_plan_fingerprint(context, frozen_items),
    )
