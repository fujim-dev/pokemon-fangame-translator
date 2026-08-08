# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation locale privée Flux ; ne copie jamais de données dans le dépôt."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import GameCapability, PokemonFluxAdapter  # noqa: E402
from flux_import_plan import build_flux_import_plan  # noqa: E402
from flux_reinjection import (  # noqa: E402
    build_flux_candidate_archive,
    create_flux_working_copy,
    validate_candidate_on_working_copy,
)
from project_identity import write_project_identity  # noqa: E402
from repair.safe_fixes import extract_protected  # noqa: E402
from safe_io import atomic_text_writer, atomic_write_text  # noqa: E402


MANIFEST_MEMBER = (
    "PFT_Flux_Handoff_Codex/reference_code/"
    "Pokemon_Flux_FR_Patcher_v3.6-beta/src/data/translation_manifest.json.gz"
)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_private_manifest(zip_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(zip_path, "r") as archive:
        compressed = archive.read(MANIFEST_MEMBER)
    payload = json.loads(gzip.decompress(compressed).decode("utf-8"))
    translations = payload.get("translations") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 1
        or not isinstance(translations, dict)
        or not translations
    ):
        raise RuntimeError("Manifest privé Flux absent ou invalide.")
    return {str(key): str(value) for key, value in translations.items()}


def _choose_reference_translation(
    rows: list[dict[str, str]],
    manifest: dict[str, str],
) -> tuple[dict[str, str], str]:
    for row in rows:
        if row.get("source_flux") != "messages_game":
            continue
        source = row.get("texte_source", "")
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        translation = manifest.get(digest, "")
        if (
            translation
            and translation != source
            and "\x00" not in translation
            and extract_protected(source) == extract_protected(translation)
        ):
            return row, translation
    raise RuntimeError(
        "Aucune occurrence messages_game sûre ne correspond au manifest privé v3.6-beta."
    )


def _write_private_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with atomic_text_writer(path, encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def validate_private_copy(game_root: Path, handoff_zip: Path, work_root: Path) -> Path:
    game = game_root.expanduser().resolve()
    package = handoff_zip.expanduser().resolve()
    work = work_root.expanduser().resolve()
    if not game.is_dir() or not package.is_file():
        raise RuntimeError("Jeu Flux ou paquet privé introuvable.")
    if work.exists():
        raise RuntimeError("Le dossier privé de validation existe déjà.")
    if _is_inside(work, game) or _is_inside(work, ROOT):
        raise RuntimeError("Le dossier privé doit rester hors du jeu et du dépôt public.")

    adapter = PokemonFluxAdapter()
    detection = adapter.probe(game)
    if (
        detection.recognized_version != "2.1.0"
        or detection.confidence != 100
        or GameCapability.RECONSTRUCT in detection.capabilities
    ):
        raise RuntimeError("La porte de détection Flux privée n'est pas satisfaite.")
    rows, warnings = adapter.extract(game)
    if warnings:
        raise RuntimeError("L'extraction privée Flux contient des avertissements bloquants.")
    manifest = _load_private_manifest(package)
    selected, translation = _choose_reference_translation(rows, manifest)
    selected_digest = hashlib.sha256(selected["texte_source"].encode("utf-8")).hexdigest()
    selected["traduction_fr"] = translation
    selected["statut"] = "Accepté"

    work.mkdir(parents=True)
    try:
        atomic_write_text(
            work / "NE_PAS_PUBLIER_CE_DOSSIER.txt",
            "Ce dossier privé contient une copie et des données de Pokémon Flux.\n"
            "Ne le copiez pas dans le dépôt GitHub et ne le partagez pas publiquement.\n",
            encoding="utf-8",
        )
        project = work / "Projet_prive"
        candidate_dir = work / "Candidat"
        backup_dir = work / "Sauvegardes"
        project.mkdir()
        candidate_dir.mkdir()
        backup_dir.mkdir()
        csv_path = project / "textes_structures.csv"
        _write_private_csv(csv_path, rows)
        write_project_identity(
            project,
            game,
            adapter_id="pokemon_flux",
            adapter_version="2.1.0",
        )
        plan = build_flux_import_plan(game, csv_path, adapter=adapter)
        if len(plan.applicable_items) != 1:
            raise RuntimeError("Le plan privé doit contenir exactement une occurrence test.")

        candidate = build_flux_candidate_archive(
            plan,
            candidate_dir / "Data_0.fpk",
            archive_reader=adapter.archive_reader,
        )
        working_copy = create_flux_working_copy(plan, work / "Copie_de_travail")
        working_result = validate_candidate_on_working_copy(
            plan,
            candidate,
            working_copy,
            backup_dir / "Data_0_avant_test.fpk",
            archive_reader=adapter.archive_reader,
        )
        final_detection = adapter.probe(game)
        if GameCapability.RECONSTRUCT in final_detection.capabilities:
            raise RuntimeError("RECONSTRUCT a été activée contrairement à la politique Flux.")

        report = {
            "schema": "pft_private_flux_validation_v1",
            "version_flux": detection.recognized_version,
            "confiance": detection.confidence,
            "occurrences_extraites": len(rows),
            "sources": dict(sorted(Counter(row["source_flux"] for row in rows).items())),
            "avertissements_extraction": len(warnings),
            "occurrences_planifiees": len(plan.applicable_items),
            "occurrence_test_sha256": selected_digest,
            "plan_sha256": plan.fingerprint,
            "fpk_original_sha256": plan.source_fpk_sha256,
            "fpk_candidat_sha256": candidate.candidate_fpk_sha256,
            "membres_modifies": list(candidate.changed_members),
            "membres_controles": candidate.verified_members,
            "fichiers_jeu_controles": working_result.source_files_verified,
            "rollback_verifie": working_result.rollback_verified,
            "fpk_restaure_sha256": working_result.restored_sha256,
            "reconstruct_active": False,
            "contenu_prive_dans_depot": False,
        }
        report_path = work / "RAPPORT_VALIDATION_PRIVEE_FLUX.json"
        atomic_write_text(
            report_path,
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_path
    except Exception:
        try:
            atomic_write_text(
                work / "VALIDATION_PRIVEE_INCOMPLETE.txt",
                "La validation privée n'est pas terminée. N'utilisez pas la copie de travail.\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_root", type=Path)
    parser.add_argument("handoff_zip", type=Path)
    parser.add_argument("work_root", type=Path)
    args = parser.parse_args()
    report = validate_private_copy(args.game_root, args.handoff_zip, args.work_root)
    print(f"Validation privée Flux réussie : {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
