from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters import GameCapability, PokemonEssentialsAdapter  # noqa: E402
from analysis.integrity import compare_snapshots, snapshot_tree  # noqa: E402
from essentials_trainer import (  # noqa: E402
    COMPILED_TRAINER_FILE,
    TRAINER_MESSAGES_FILE,
    TRAINER_PBS_FILE,
    build_trainer_entry_proofs,
    extract_trainer_target_texts,
)
from extraction_project import (  # noqa: E402
    BASELINE_CSV_NAME,
    EXTRACTION_MANIFEST_NAME,
    EXTRACTION_REPORT_NAME,
    PROJECT_CSV_NAME,
    build_extraction_manifest_bytes,
    extraction_id,
)
from Pokemon_Fangame_Translator import (  # noqa: E402
    merge_project_rows,
    serialize_project_csv,
)
from project_identity import (  # noqa: E402
    PROJECT_METADATA_NAME,
    build_project_identity_bytes,
)
from reconstruction_engine import (  # noqa: E402
    ReconstructionError,
    build_plan,
    build_v21_1_trainer_lose_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from safe_io import atomic_write_bundle  # noqa: E402
from translation_project import (  # noqa: E402
    RESUME_STATE_NAME,
    TRANSLATION_STATE_NAME,
    TranslationProjectSession,
    build_resume_state_bytes,
    build_translation_state_bytes,
    inspect_csv_structure,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validation privée bornée d'un LoseText Essentials v21.1."
    )
    parser.add_argument("reference", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("project", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--section", required=True)
    parser.add_argument("--marker", required=True)
    return parser.parse_args()


def _publish_project(
    extraction,
    detection,
    work: Path,
    project: Path,
) -> tuple[list[dict[str, str]], list[str]]:
    rows, preserved, fields = merge_project_rows(
        [dict(row) for row in extraction.rows],
        None,
    )
    if preserved:
        raise RuntimeError("Une traduction privée inattendue a été réutilisée.")
    csv_payload = serialize_project_csv(rows, fields)
    csv_sha256 = hashlib.sha256(csv_payload).hexdigest()
    report_payload = (
        "PFT v21.1 — validation privée LoseText\n"
        f"Occurrences structurées : {len(rows)}\n"
        "Aucun texte extrait n'est reproduit dans ce rapport.\n"
    ).encode("utf-8")
    report_sha256 = hashlib.sha256(report_payload).hexdigest()
    manifest_payload = build_extraction_manifest_bytes(
        extraction,
        game_root=work,
        adapter_version=detection.recognized_version,
        csv_sha256=csv_sha256,
        report_sha256=report_sha256,
        row_count=len(rows),
    )
    manifest_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    run_id = extraction_id(extraction.source_manifest_sha256, csv_sha256)
    identity_payload = build_project_identity_bytes(
        work,
        adapter_id="pokemon_essentials",
        adapter_version=detection.recognized_version,
        adapter_profile=extraction.essentials_profile,
        source_manifest_sha256=extraction.source_manifest_sha256,
        extraction_manifest_name=EXTRACTION_MANIFEST_NAME,
        extraction_manifest_sha256=manifest_sha256,
        extraction_id=run_id,
        extracted_csv_sha256=csv_sha256,
    )
    state_payload = build_translation_state_bytes(
        revision=1,
        csv_name=PROJECT_CSV_NAME,
        csv_sha256=csv_sha256,
        identity_sha256=hashlib.sha256(identity_payload).hexdigest(),
        manifest_sha256=manifest_sha256,
        baseline_sha256=csv_sha256,
        report_sha256=report_sha256,
        source_manifest_sha256=extraction.source_manifest_sha256,
        extraction_id=run_id,
        immutable_rows_sha256=inspect_csv_structure(csv_payload).immutable_sha256,
    )
    resume_payload = build_resume_state_bytes(
        {
            "version": "1.0",
            "active": False,
            "total": 0,
            "completed": 0,
            "remaining": 0,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        },
        csv_name=PROJECT_CSV_NAME,
        csv_sha256=csv_sha256,
        source_manifest_sha256=extraction.source_manifest_sha256,
        extraction_id=run_id,
    )
    atomic_write_bundle(
        {
            project / PROJECT_CSV_NAME: csv_payload,
            project / BASELINE_CSV_NAME: csv_payload,
            project / EXTRACTION_REPORT_NAME: report_payload,
            project / EXTRACTION_MANIFEST_NAME: manifest_payload,
            project / PROJECT_METADATA_NAME: identity_payload,
            project / TRANSLATION_STATE_NAME: state_payload,
            project / RESUME_STATE_NAME: resume_payload,
        }
    )
    return rows, fields


def main() -> int:
    args = _arguments()
    reference = args.reference.expanduser().resolve()
    work = args.work.expanduser().resolve()
    project = args.project.expanduser().resolve()
    candidate = args.candidate.expanduser().resolve()
    reports = args.reports.expanduser().resolve()
    for path in (project, candidate, reports):
        if path.exists():
            raise RuntimeError(f"La destination privée existe déjà : {path}")

    reference_before = snapshot_tree(reference)
    work_before = snapshot_tree(work)
    initial_copy = compare_snapshots(reference_before, work_before)
    if not initial_copy.passed:
        raise RuntimeError("La copie de travail ne correspond pas à la référence.")

    adapter = PokemonEssentialsAdapter()
    detection = adapter.probe(work)
    if detection.can(GameCapability.RECONSTRUCT) or detection.write_actions_allowed:
        raise RuntimeError("La reconstruction publique v21.1 est devenue active.")
    extraction = adapter.extract_with_provenance(work)
    rows, fields = _publish_project(extraction, detection, work, project)
    trainer_rows = [row for row in rows if row["fichier"] == TRAINER_PBS_FILE]
    lose_text_rows = [row for row in trainer_rows if row["commande"] == "LoseText"]
    selected = next(
        row
        for row in lose_text_rows
        if row["evenement_id"] == args.section
    )
    selected["traduction_fr"] = selected["texte_source"] + args.marker
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_privee_v21_1_trainer_lose"
    translated_payload = serialize_project_csv(rows, fields)
    csv_path = project / PROJECT_CSV_NAME
    with TranslationProjectSession(
        csv_path,
        game_root=work,
        expected_adapter_id="pokemon_essentials",
    ) as session:
        if not session.writable:
            raise RuntimeError(session.read_only_reason)
        session.save(translated_payload)
    with TranslationProjectSession(
        csv_path,
        game_root=work,
        expected_adapter_id="pokemon_essentials",
    ) as reopened:
        reopened.check_current()

    try:
        build_plan(work, csv_path, mode="accepted")
    except ReconstructionError:
        public_reconstruction_refused = True
    else:
        raise RuntimeError("La porte publique a accepté le projet v21.1.")

    plan = build_v21_1_trainer_lose_validation_plan(work, csv_path)
    simulate_plan(plan)
    if plan.counts().get("applicable") != 1:
        raise RuntimeError("La simulation privée ne conserve pas sa cible unique.")
    result = reconstruct_copy(plan, candidate, reports)
    expected_translation = selected["traduction_fr"]
    reextracted = extract_trainer_target_texts(
        (candidate / TRAINER_PBS_FILE).read_bytes(),
        (candidate / COMPILED_TRAINER_FILE).read_bytes(),
        (candidate / TRAINER_MESSAGES_FILE).read_bytes(),
        section=selected["evenement_id"],
    )
    if reextracted != (expected_translation,) * 3:
        raise RuntimeError("La réextraction privée ne retrouve pas les trois textes.")
    structural_lose_texts = len(
        build_trainer_entry_proofs(
            (work / TRAINER_PBS_FILE).read_bytes(),
            (work / COMPILED_TRAINER_FILE).read_bytes(),
            (work / TRAINER_MESSAGES_FILE).read_bytes(),
        )
    )

    reference_after = snapshot_tree(reference)
    work_after = snapshot_tree(work)
    candidate_after = snapshot_tree(candidate)
    original_unchanged = compare_snapshots(reference_before, reference_after)
    work_unchanged = compare_snapshots(work_before, work_after)
    candidate_comparison = compare_snapshots(
        work_after,
        candidate_after,
        allowed_changed={
            TRAINER_PBS_FILE,
            COMPILED_TRAINER_FILE,
            TRAINER_MESSAGES_FILE,
        },
    )
    if not original_unchanged.passed or not work_unchanged.passed:
        raise RuntimeError("La référence ou la copie de travail a changé.")
    if (
        candidate_comparison.missing_files
        or candidate_comparison.changed_files
        or candidate_comparison.emptied_files
    ):
        raise RuntimeError("Le candidat contient une modification hors plan.")

    print(
        json.dumps(
            {
                "profile": detection.structural_profile,
                "version": detection.declared_version,
                "confidence": detection.confidence,
                "public_reconstruct": detection.can(GameCapability.RECONSTRUCT),
                "public_reconstruction_refused": public_reconstruction_refused,
                "total_rows": len(rows),
                "trainer_pbs_rows": len(trainer_rows),
                "trainer_lose_rows": len(lose_text_rows),
                "trainer_lose_structural_occurrences": structural_lose_texts,
                "selected_section": selected["evenement_id"],
                "selected_occurrence": int(selected["sous_index"]),
                "marker": args.marker.strip(),
                "plan_scope": plan.validation_scope,
                "plan_hash_files": sorted(plan.source_hashes),
                "modified_files": result.modified_files,
                "applied": result.applied,
                "integrity": result.integrity_valid,
                "original_unchanged": original_unchanged.passed,
                "work_unchanged": work_unchanged.passed,
                "candidate_generated": list(candidate_comparison.unexpected_files),
                "reextracted_equal": reextracted == (expected_translation,) * 3,
                "candidate_game_exe": str(candidate / "Game.exe"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
