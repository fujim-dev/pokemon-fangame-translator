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
from build_support.validate_private_essentials_ability import (  # noqa: E402
    _publish_project,
)
from essentials_ability import (  # noqa: E402
    ABILITY_MESSAGES_FILE,
    ABILITY_NAME_MESSAGES_INDEX,
    ABILITY_PBS_FILE,
    COMPILED_ABILITY_FILE,
    build_ability_name_proofs,
    extract_ability_name_texts,
)
from extraction_project import PROJECT_CSV_NAME  # noqa: E402
from Pokemon_Fangame_Translator import serialize_project_csv  # noqa: E402
from reconstruction_engine import (  # noqa: E402
    ReconstructionError,
    build_plan,
    build_v21_1_ability_name_validation_plan,
    reconstruct_copy,
    simulate_plan,
)
from ruby_marshal_reader import load  # noqa: E402
from ruby_marshal_writer import dumps  # noqa: E402
from structured_extractor import extract_structured_verified  # noqa: E402
from translation_project import TranslationProjectSession  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validation privée bornée d'un unique Ability.Name v21.1."
    )
    parser.add_argument("reference", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("project", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--section", required=True)
    parser.add_argument("--marker", required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    reference = args.reference.expanduser().resolve()
    work = args.work.expanduser().resolve()
    project = args.project.expanduser().resolve()
    candidate = args.candidate.expanduser().resolve()
    reports = args.reports.expanduser().resolve()
    if not args.marker or any(character in args.marker for character in ("\r", "\n")):
        raise RuntimeError("Le marqueur Ability.Name doit tenir sur une ligne PBS.")
    for path in (project, candidate, reports):
        if path.exists():
            raise RuntimeError(f"La destination privée existe déjà : {path}")

    reference_before = snapshot_tree(reference)
    work_before = snapshot_tree(work)
    initial_copy = compare_snapshots(reference_before, work_before)
    if not initial_copy.passed:
        raise RuntimeError("La copie de travail ne correspond pas à la référence.")

    compiled_before = load(work / COMPILED_ABILITY_FILE)
    messages_before = load(work / ABILITY_MESSAGES_FILE)
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
        label="Ability.Name",
    )
    ability_rows = [
        row
        for row in rows
        if row["fichier"] == ABILITY_PBS_FILE and row["commande"] == "Name"
    ]
    matching = [row for row in ability_rows if row["evenement_id"] == args.section]
    if len(matching) != 1:
        raise RuntimeError("Ability.Name cible n'est pas une occurrence unique.")
    selected = matching[0]
    selected["traduction_fr"] = args.marker
    selected["statut"] = "Accepté"
    selected["origine_traduction"] = "validation_privee_v21_1_ability_name"
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

    plan = build_v21_1_ability_name_validation_plan(work, csv_path)
    simulate_plan(plan)
    if plan.counts().get("applicable") != 1:
        raise RuntimeError("La simulation privée ne conserve pas sa cible unique.")
    result = reconstruct_copy(plan, candidate, reports)
    expected_translation = selected["traduction_fr"]
    reextracted = extract_ability_name_texts(
        (candidate / ABILITY_PBS_FILE).read_bytes(),
        (candidate / COMPILED_ABILITY_FILE).read_bytes(),
        (candidate / ABILITY_MESSAGES_FILE).read_bytes(),
        section=selected["evenement_id"],
    )
    if reextracted != (expected_translation,) * 3:
        raise RuntimeError("La réextraction privée ne retrouve pas les trois noms.")
    candidate_extraction = extract_structured_verified(
        candidate,
        essentials_profile="essentials_v21_1_readonly",
    )
    candidate_rows = [
        row
        for row in candidate_extraction.rows
        if row["fichier"] == ABILITY_PBS_FILE
        and row["commande"] == "Name"
        and row["evenement_id"] == args.section
    ]
    if (
        len(candidate_rows) != 1
        or candidate_rows[0]["id_stable"] != selected["id_stable"]
        or candidate_rows[0]["texte_source"] != expected_translation
    ):
        raise RuntimeError("La réextraction structurée perd l'identité Ability.Name.")

    compiled_after = load(candidate / COMPILED_ABILITY_FILE)
    before_target = compiled_before[args.section]
    after_target = compiled_after[args.section]
    if tuple(before_target.ivars) != tuple(after_target.ivars):
        raise RuntimeError("La structure GameData::Ability cible a changé.")
    for ivar in before_target.ivars:
        if ivar == "@real_name":
            continue
        if dumps(before_target.ivars[ivar]) != dumps(after_target.ivars[ivar]):
            raise RuntimeError(f"Le champ Ability hors cible a changé : {ivar}")
    messages_after = load(candidate / ABILITY_MESSAGES_FILE)
    if dumps(messages_before[11]) != dumps(messages_after[11]):
        raise RuntimeError("La banque ABILITY_DESCRIPTIONS a changé.")

    proofs = build_ability_name_proofs(
        (work / ABILITY_PBS_FILE).read_bytes(),
        (work / COMPILED_ABILITY_FILE).read_bytes(),
        (work / ABILITY_MESSAGES_FILE).read_bytes(),
    )
    safe_unique = sum(
        json.loads(proof.compiled_structure)["target_reference_count"] == 1
        and json.loads(proof.runtime_structure)["source_usage_count"] == 1
        and json.loads(proof.runtime_structure)["target_key_reference_count"] == 1
        and json.loads(proof.runtime_structure)["target_value_reference_count"] == 1
        for proof in proofs.values()
    )
    name_hashes = [hashlib.sha256(proof.source.encode("utf-8")).hexdigest() for proof in proofs.values()]

    reference_after = snapshot_tree(reference)
    work_after = snapshot_tree(work)
    candidate_after = snapshot_tree(candidate)
    original_unchanged = compare_snapshots(reference_before, reference_after)
    work_unchanged = compare_snapshots(work_before, work_after)
    candidate_comparison = compare_snapshots(
        work_after,
        candidate_after,
        allowed_changed={
            ABILITY_PBS_FILE,
            COMPILED_ABILITY_FILE,
            ABILITY_MESSAGES_FILE,
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
                "ability_name_rows": len(ability_rows),
                "ability_name_structural_occurrences": len(proofs),
                "ability_name_unique_values": len(set(name_hashes)),
                "ability_name_unique_safe": safe_unique,
                "ability_name_bank_index": ABILITY_NAME_MESSAGES_INDEX,
                "selected_section": selected["evenement_id"],
                "selected_occurrence": int(selected["sous_index"]),
                "marker": args.marker,
                "plan_scope": plan.validation_scope,
                "plan_hash_files": sorted(plan.source_hashes),
                "modified_files": result.modified_files,
                "applied": result.applied,
                "integrity": result.integrity_valid,
                "description_unchanged": True,
                "original_unchanged": original_unchanged.passed,
                "work_unchanged": work_unchanged.passed,
                "candidate_generated": list(candidate_comparison.unexpected_files),
                "reextracted_equal": reextracted == (expected_translation,) * 3,
                "stable_id_preserved": True,
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
