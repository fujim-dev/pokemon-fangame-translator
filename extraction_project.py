# SPDX-License-Identifier: GPL-3.0-or-later
"""Provenance privée des artefacts produits par une extraction Essentials."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from structured_extractor import StructuredExtractionResult


EXTRACTION_MANIFEST_NAME = "MANIFESTE_EXTRACTION.json"


def extraction_id(source_manifest_sha256: str, csv_sha256: str) -> str:
    payload = f"pokemon_essentials|{source_manifest_sha256}|{csv_sha256}"
    return hashlib.sha256(payload.encode("ascii")).hexdigest()[:24]


def build_extraction_manifest_bytes(
    result: StructuredExtractionResult,
    *,
    game_root: Path,
    adapter_version: str,
    csv_sha256: str,
    report_sha256: str,
    row_count: int,
) -> bytes:
    run_id = extraction_id(result.source_manifest_sha256, csv_sha256)
    payload = {
        "format": "pft_essentials_extraction_v1",
        "extraction_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "adapter_id": "pokemon_essentials",
        "adapter_version": adapter_version,
        "game_root": str(game_root.expanduser().resolve()),
        "source_manifest_sha256": result.source_manifest_sha256,
        "source_count": len(result.sources),
        "sources": [source.public_record() for source in result.sources],
        "row_count": row_count,
        "csv_sha256": csv_sha256,
        "report_sha256": report_sha256,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
