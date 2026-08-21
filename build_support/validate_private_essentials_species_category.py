from __future__ import annotations

import argparse
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
from build_support.validate_private_essentials_species import (  # noqa: E402
    _publish_project,
)
from essentials_species import (  # noqa: E402
    COMPILED_SPECIES_FILE,
    SPECIES_FORMS_PBS_FILE,
    SPECIES_MESSAGES_FILE,
    SPECIES_PBS_FILE,
)
from essentials_species_category import (  # noqa: E402
    build_species_category_proofs,
    extract_species_category_texts,
)
from extraction_project import PROJECT_CSV_NAME  # noqa: E402
from Pokemon_Fangame_Translator import serialize_project_csv  # noqa: E402
from reconstruction_engine import (  # noqa: E402
    ReconstructionError,
    build_plan,
    build_v21_1_species_category_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from translation_project import TranslationProjectSession  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validation privée bornée d'une Species.Category v21.1."
    )
    parser.add_argument("reference", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("project", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--section", required=True)
    parser.add_argument("--translation", required=True)
    return parser.parse_args()


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
    if not compare_snapshots(reference_before, work_before).passed:
        raise RuntimeError("La copie de travail ne correspond pas à la référence.")

    adapter = PokemonEssentialsAdapter()
    detection = adapter.probe(work)
    if detection.can(GameCapability.RECONSTRUCT) or detection.write_actions_allowed:
        raise RuntimeError("La reconstruction publique v21.1 est devenue active.")
    extraction = adapter.extract_with_provenance(work)
    rows, fields = _publish_project(
        extraction,
        detection,
        work,
        project,
        label="Species.Category",
    )
    species_rows = [row for row in rows if row["fichier"] == SPECIES_PBS_FILE]
    category_rows = [row for row in species_rows if row["commande"] == "Category"]
    technical_rows = [
        row
        for row in species_rows
        if row["commande"] not in {"Name", "Category", "Pokedex", "FormName"}
    ]
    if technical_rows:
        raise RuntimeError(
            "Un champ technique de pokemon.txt a été exposé comme traduction."
        )
    selected = next(
        row for row in category_rows if row["evenement_id"] == args.section
    )
    selected["traduction_fr"] = args.translation
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = (
        "validation_privee_v21_1_species_category"
    )
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

    plan = build_v21_1_species_category_validation_plan(work, csv_path)
    simulate_plan(plan)
    if plan.counts().get("applicable") != 1:
        raise RuntimeError("La simulation privée ne conserve pas sa cible unique.")
    result = reconstruct_copy(plan, candidate, reports)
    expected_translation = selected["traduction_fr"]
    reextracted = extract_species_category_texts(
        (candidate / SPECIES_PBS_FILE).read_bytes(),
        (candidate / SPECIES_FORMS_PBS_FILE).read_bytes(),
        (candidate / COMPILED_SPECIES_FILE).read_bytes(),
        (candidate / SPECIES_MESSAGES_FILE).read_bytes(),
        section=selected["evenement_id"],
    )
    if reextracted != (expected_translation,) * 3:
        raise RuntimeError("La réextraction privée ne retrouve pas les trois textes.")

    proofs = build_species_category_proofs(
        (work / SPECIES_PBS_FILE).read_bytes(),
        (work / SPECIES_FORMS_PBS_FILE).read_bytes(),
        (work / COMPILED_SPECIES_FILE).read_bytes(),
        (work / SPECIES_MESSAGES_FILE).read_bytes(),
    )
    safe_categories = sum(
        json.loads(proof.runtime_structure)["source_usage_count"] == 1
        and json.loads(proof.compiled_structure)["target_reference_count"] == 1
        and json.loads(proof.runtime_structure)["target_key_reference_count"] == 1
        and json.loads(proof.runtime_structure)["target_value_reference_count"] == 1
        for proof in proofs.values()
    )
    selected_proof = json.loads(
        proofs[(selected["evenement_id"], "Category", 1)].pbs_structure
    )

    reference_after = snapshot_tree(reference)
    work_after = snapshot_tree(work)
    candidate_after = snapshot_tree(candidate)
    original_unchanged = compare_snapshots(reference_before, reference_after)
    work_unchanged = compare_snapshots(work_before, work_after)
    expected_files = {
        SPECIES_PBS_FILE,
        COMPILED_SPECIES_FILE,
        SPECIES_MESSAGES_FILE,
    }
    candidate_comparison = compare_snapshots(
        work_after,
        candidate_after,
        allowed_changed=expected_files,
    )
    if not original_unchanged.passed or not work_unchanged.passed:
        raise RuntimeError("La référence ou la copie de travail a changé.")
    if (
        candidate_comparison.missing_files
        or candidate_comparison.changed_files
        or candidate_comparison.emptied_files
    ):
        raise RuntimeError("Le candidat contient une modification hors plan.")
    if set(result.modified_files) != expected_files:
        raise RuntimeError("La liste des fichiers modifiés ne correspond pas au plan.")

    print(
        json.dumps(
            {
                "profile": detection.structural_profile,
                "version": detection.declared_version,
                "confidence": detection.confidence,
                "public_reconstruct": detection.can(GameCapability.RECONSTRUCT),
                "public_reconstruction_refused": public_reconstruction_refused,
                "total_rows": len(rows),
                "species_rows": len(species_rows),
                "species_category_rows": len(category_rows),
                "species_technical_rows": len(technical_rows),
                "species_category_unique_safe": safe_categories,
                "base_species_count": selected_proof["base_section_count"],
                "form_count": selected_proof["form_section_count"],
                "explicit_form_category_count": selected_proof[
                    "explicit_form_category_count"
                ],
                "inherited_form_category_count": selected_proof[
                    "inherited_form_category_count"
                ],
                "selected_section": selected["evenement_id"],
                "selected_occurrence": int(selected["sous_index"]),
                "source_sha256": hashlib.sha256(
                    selected["texte_source"].encode("utf-8")
                ).hexdigest(),
                "translation": expected_translation,
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
