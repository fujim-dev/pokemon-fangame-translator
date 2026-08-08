from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from extraction_project import (
    BASELINE_CSV_NAME,
    EXTRACTION_MANIFEST_NAME,
    EXTRACTION_REPORT_NAME,
    extraction_id,
)
from project_identity import PROJECT_METADATA_NAME, build_project_identity_bytes
from structured_extractor import build_extraction_inventory
from translation_project import RESUME_STATE_NAME, TRANSLATION_STATE_NAME


def finalize_verified_essentials_project(game_root: Path, csv_path: Path) -> None:
    """Ajoute une provenance complète à une fixture synthétique de reconstruction."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";", strict=True)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    for field in ("adaptateur", "source_sha256", "source_manifest_sha256"):
        if field not in fields:
            fields.append(field)

    inventory = build_extraction_inventory(game_root)
    sources = {source.relative_path: source for source in inventory.sources}
    for row in rows:
        relative = str(row.get("fichier") or "").replace("\\", "/")
        source = sources.get(relative)
        row["adaptateur"] = row.get("adaptateur") or "pokemon_essentials"
        row["source_manifest_sha256"] = (
            row.get("source_manifest_sha256") or inventory.source_manifest_sha256
        )
        row["source_sha256"] = row.get("source_sha256") or (
            source.sha256 if source is not None else "0" * 64
        )

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, delimiter=";")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    csv_payload = output.getvalue().encode("utf-8-sig")
    csv_path.write_bytes(csv_payload)

    project_dir = csv_path.parent
    baseline_path = project_dir / BASELINE_CSV_NAME
    report_path = project_dir / EXTRACTION_REPORT_NAME
    manifest_path = project_dir / EXTRACTION_MANIFEST_NAME
    baseline_path.write_bytes(csv_payload)
    report_payload = b"synthetic verified extraction report"
    report_path.write_bytes(report_payload)
    csv_sha256 = hashlib.sha256(csv_payload).hexdigest()
    run_id = extraction_id(inventory.source_manifest_sha256, csv_sha256)
    manifest = {
        "format": "pft_essentials_extraction_v1",
        "extraction_id": run_id,
        "adapter_id": "pokemon_essentials",
        "adapter_version": "21.1",
        "game_root": str(game_root.resolve()),
        "source_manifest_sha256": inventory.source_manifest_sha256,
        "source_count": len(inventory.sources),
        "sources": [source.public_record() for source in inventory.sources],
        "row_count": len(rows),
        "project_csv_name": csv_path.name,
        "baseline_csv_name": baseline_path.name,
        "report_name": report_path.name,
        "csv_sha256": csv_sha256,
        "report_sha256": hashlib.sha256(report_payload).hexdigest(),
    }
    manifest_payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest_path.write_bytes(manifest_payload)
    (project_dir / PROJECT_METADATA_NAME).write_bytes(
        build_project_identity_bytes(
            game_root,
            adapter_id="pokemon_essentials",
            adapter_version="21.1",
            source_manifest_sha256=inventory.source_manifest_sha256,
            extraction_manifest_name=manifest_path.name,
            extraction_manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
            extraction_id=run_id,
            extracted_csv_sha256=csv_sha256,
        )
    )
    (project_dir / TRANSLATION_STATE_NAME).unlink(missing_ok=True)
    (project_dir / RESUME_STATE_NAME).unlink(missing_ok=True)
