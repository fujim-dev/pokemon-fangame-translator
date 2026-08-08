# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation indépendante d'un CSV Flux, sans import ni reconstruction."""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from adapters import GameCapability, PokemonFluxAdapter, authorize_adapter_operation
from adapters.pokemon_flux import locate_flux_fpk, sha256_stable_file
from project_identity import ProjectIdentityError, read_project_identity
from repair.safe_fixes import extract_protected


MAX_CSV_SIZE = 128 * 1024 * 1024
ACCEPTED_IMPORT_STATUSES = frozenset({"Accepté"})
FLUX_IMMUTABLE_FIELDS = (
    "id_stable",
    "type",
    "fichier",
    "carte_id",
    "carte_nom",
    "evenement_id",
    "evenement_nom",
    "page",
    "commande",
    "sous_index",
    "texte_source",
    "codes_proteges",
    "adaptateur",
    "conteneur",
    "source_flux",
    "chemin_structurel",
    "empreinte_source",
    "empreinte_texte_csv",
    "empreinte_valeur_actuelle",
)
FLUX_REQUIRED_FIELDS = frozenset((*FLUX_IMMUTABLE_FIELDS, "traduction_fr", "statut"))


class FluxImportValidationError(RuntimeError):
    """Le validateur ne peut pas établir un contexte Flux fiable."""


@dataclass(frozen=True)
class FluxImportIssue:
    code: str
    severity: str
    message: str
    row_id: str = ""
    field: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "erreur"


@dataclass(frozen=True)
class FluxImportValidationReport:
    adapter_version: str
    csv_sha256: str
    fpk_sha256_before: str
    fpk_sha256_after: str
    csv_rows: int
    expected_occurrences: int
    matched_occurrences: int
    eligible_translations: int
    review_required: int
    untranslated: int
    extraction_warnings: tuple[str, ...]
    issues: tuple[FluxImportIssue, ...]

    @property
    def original_fpk_unchanged(self) -> bool:
        return self.fpk_sha256_before == self.fpk_sha256_after

    @property
    def structurally_valid(self) -> bool:
        return self.original_fpk_unchanged and not any(issue.blocking for issue in self.issues)

    @property
    def ready_for_future_import(self) -> bool:
        return (
            self.structurally_valid
            and self.eligible_translations > 0
            and not self.extraction_warnings
        )


def _is_redirected(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x0400)
    except OSError:
        return False


def _read_stable_csv(path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    source = path.expanduser()
    if _is_redirected(source) or _is_redirected(source.parent):
        raise FluxImportValidationError("Le CSV Flux ou son dossier est redirigé.")
    if not source.is_file():
        raise FluxImportValidationError("Le CSV Flux est introuvable.")
    try:
        before = source.stat()
        if before.st_size > MAX_CSV_SIZE:
            raise FluxImportValidationError("Le CSV Flux dépasse la taille de sécurité autorisée.")
        raw = source.read_bytes()
        after = source.stat()
    except FluxImportValidationError:
        raise
    except OSError as exc:
        raise FluxImportValidationError("Le CSV Flux ne peut pas être lu.") from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise FluxImportValidationError("Le CSV Flux a changé pendant sa lecture.")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(
            text.splitlines(keepends=True),
            delimiter=";",
            strict=True,
        )
        fields = list(reader.fieldnames or [])
        if len(fields) != len(set(fields)):
            raise FluxImportValidationError("Le CSV Flux contient des colonnes dupliquées.")
        missing = sorted(FLUX_REQUIRED_FIELDS - set(fields))
        if missing:
            raise FluxImportValidationError(
                "Le CSV Flux est incomplet, colonnes manquantes : " + ", ".join(missing)
            )
        rows = [dict(row) for row in reader]
    except FluxImportValidationError:
        raise
    except Exception as exc:
        raise FluxImportValidationError("Le CSV Flux est invalide ou mal encodé.") from exc
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise FluxImportValidationError("Le CSV Flux contient une ligne avec un nombre de champs invalide.")
    return fields, rows, hashlib.sha256(raw).hexdigest()


def validate_flux_import(
    game_root: Path,
    csv_path: Path,
    *,
    adapter: PokemonFluxAdapter | None = None,
    progress=None,
    logger=None,
) -> FluxImportValidationReport:
    """Compare le CSV à une nouvelle extraction fidèle, sans rien réinjecter."""
    root_input = game_root.expanduser()
    if _is_redirected(root_input):
        raise FluxImportValidationError("Le dossier Flux ne peut pas être un lien ou une jonction.")
    root = root_input.resolve()
    flux_adapter = adapter or PokemonFluxAdapter()
    try:
        detection = authorize_adapter_operation(
            root,
            expected_adapter_id=flux_adapter.adapter_id,
            capability=GameCapability.VALIDATE_IMPORT,
            adapter=flux_adapter,
        )
    except Exception as exc:
        raise FluxImportValidationError(
            "Validation d'import bloquée : version Flux non homologuée ou détection ambiguë."
        ) from exc

    csv_input = csv_path.expanduser()
    if _is_redirected(csv_input) or _is_redirected(csv_input.parent):
        raise FluxImportValidationError("Le CSV Flux ou son dossier est redirigé.")
    csv_file = csv_input.resolve()
    try:
        identity = read_project_identity(
            csv_file,
            root,
            expected_adapter_id=flux_adapter.adapter_id,
        )
    except ProjectIdentityError as exc:
        raise FluxImportValidationError(f"Projet Flux refusé : {exc}") from exc
    if identity.adapter_version != detection.recognized_version:
        raise FluxImportValidationError(
            "La version Flux du projet diffère de la version actuellement détectée."
        )

    _fields, csv_rows, csv_sha256 = _read_stable_csv(csv_file)
    fpk, fpk_warnings = locate_flux_fpk(root)
    if fpk is None:
        raise FluxImportValidationError("Archive Data_0.fpk Flux unique introuvable.")
    fpk_before = sha256_stable_file(fpk)
    try:
        expected_rows, extraction_warnings = flux_adapter.extract(
            root,
            progress=progress,
            logger=logger,
        )
    except Exception as exc:
        raise FluxImportValidationError("Réextraction Flux de contrôle impossible.") from exc
    fpk_after = sha256_stable_file(fpk)
    if fpk_before != fpk_after:
        raise FluxImportValidationError("Le FPK original a changé pendant la validation d'import.")

    issues: list[FluxImportIssue] = []
    expected_ids = [row.get("id_stable", "") for row in expected_rows]
    if any(not re.fullmatch(r"[0-9a-f]{64}", row_id) for row_id in expected_ids):
        raise FluxImportValidationError(
            "La réextraction Flux de contrôle contient un identifiant invalide."
        )
    if len(expected_ids) != len(set(expected_ids)):
        raise FluxImportValidationError(
            "La réextraction Flux de contrôle contient des identifiants dupliqués."
        )
    if any(
        any(field not in row for field in FLUX_IMMUTABLE_FIELDS)
        for row in expected_rows
    ):
        raise FluxImportValidationError(
            "La réextraction Flux de contrôle est structurellement incomplète."
        )
    expected_by_id = {row["id_stable"]: row for row in expected_rows}
    csv_by_id: dict[str, dict[str, str]] = {}
    duplicate_ids: set[str] = set()
    for row in csv_rows:
        row_id = row.get("id_stable", "")
        if not re.fullmatch(r"[0-9a-f]{64}", row_id):
            issues.append(FluxImportIssue(
                "invalid_id", "erreur", "Identifiant Flux invalide.", row_id=row_id
            ))
        if row_id in csv_by_id:
            duplicate_ids.add(row_id)
            issues.append(FluxImportIssue(
                "duplicate_id", "erreur", "Identifiant Flux dupliqué.", row_id=row_id
            ))
            continue
        csv_by_id[row_id] = row

    for row_id in sorted(set(expected_by_id) - set(csv_by_id)):
        issues.append(FluxImportIssue(
            "missing_occurrence", "erreur", "Occurrence Flux absente du CSV.", row_id=row_id
        ))
    for row_id in sorted(set(csv_by_id) - set(expected_by_id)):
        issues.append(FluxImportIssue(
            "unexpected_occurrence", "erreur", "Occurrence inconnue ajoutée au CSV.", row_id=row_id
        ))

    matched = eligible = review_required = untranslated = 0
    for row_id in sorted(set(expected_by_id) & set(csv_by_id)):
        expected = expected_by_id[row_id]
        row = csv_by_id[row_id]
        faithful = row_id not in duplicate_ids
        for field in FLUX_IMMUTABLE_FIELDS:
            if row.get(field, "") != expected.get(field, ""):
                faithful = False
                issues.append(FluxImportIssue(
                    "immutable_field_changed",
                    "erreur",
                    "Un champ structurel Flux a été modifié.",
                    row_id=row_id,
                    field=field,
                ))
        if faithful:
            matched += 1

        translation = row.get("traduction_fr", "")
        status = row.get("statut", "").strip()
        if not translation.strip():
            untranslated += 1
            if status in ACCEPTED_IMPORT_STATUSES:
                issues.append(FluxImportIssue(
                    "accepted_empty_translation",
                    "erreur",
                    "Une traduction vide ne peut pas être acceptée.",
                    row_id=row_id,
                ))
            continue
        translation_safe = True
        if "\x00" in translation:
            translation_safe = False
            issues.append(FluxImportIssue(
                "nul_in_translation",
                "erreur",
                "La traduction contient un caractère NUL interdit.",
                row_id=row_id,
            ))
        if extract_protected(expected["texte_source"]) != extract_protected(translation):
            translation_safe = False
            issues.append(FluxImportIssue(
                "protected_commands_changed",
                "erreur",
                "Les commandes ou balises protégées ont changé.",
                row_id=row_id,
            ))
        if status in ACCEPTED_IMPORT_STATUSES and faithful and translation_safe:
            eligible += 1
        else:
            review_required += 1
            if status not in ACCEPTED_IMPORT_STATUSES:
                issues.append(FluxImportIssue(
                    "translation_not_accepted",
                    "avertissement",
                    "Traduction présente mais non acceptée ; elle resterait exclue.",
                    row_id=row_id,
                ))

    warnings = tuple((*fpk_warnings, *extraction_warnings))
    return FluxImportValidationReport(
        adapter_version=detection.recognized_version,
        csv_sha256=csv_sha256,
        fpk_sha256_before=fpk_before,
        fpk_sha256_after=fpk_after,
        csv_rows=len(csv_rows),
        expected_occurrences=len(expected_rows),
        matched_occurrences=matched,
        eligible_translations=eligible,
        review_required=review_required,
        untranslated=untranslated,
        extraction_warnings=warnings,
        issues=tuple(issues),
    )
