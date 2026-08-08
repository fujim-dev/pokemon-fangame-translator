# SPDX-License-Identifier: GPL-3.0-or-later
"""Identité privée liant un projet de traduction à son fangame source."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from safe_io import atomic_write_text


PROJECT_METADATA_NAME = "projet.json"


class ProjectIdentityError(ValueError):
    """Le projet ne peut pas être rattaché avec certitude à son fangame."""


@dataclass(frozen=True)
class ProjectIdentity:
    metadata_path: Path
    game_root: Path
    adapter_id: str
    adapter_version: str
    sha256: str


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


def write_project_identity(
    project_dir: Path,
    game_root: Path,
    *,
    adapter_id: str = "",
    adapter_version: str = "",
    software_version: str = "1.0.2",
) -> Path:
    project = project_dir.expanduser()
    project.mkdir(parents=True, exist_ok=True)
    if _is_redirected(project):
        raise ProjectIdentityError(
            "Le dossier du projet ne peut pas être un lien ou une jonction."
        )
    root = game_root.expanduser().resolve()
    payload = {
        "format": "pft_project_identity_v1",
        "nom": root.name,
        "dossier_jeu": str(root),
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "version_logiciel": software_version,
        "mis_a_jour": datetime.now().isoformat(timespec="seconds"),
    }
    destination = project / PROJECT_METADATA_NAME
    atomic_write_text(
        destination,
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination


def read_project_identity(
    csv_path: Path,
    game_root: Path,
    *,
    expected_adapter_id: str,
) -> ProjectIdentity:
    csv_file = csv_path.expanduser()
    project_dir = csv_file.parent
    metadata_path = project_dir / PROJECT_METADATA_NAME
    if _is_redirected(project_dir) or _is_redirected(csv_file) or _is_redirected(metadata_path):
        raise ProjectIdentityError(
            "Le CSV, son projet ou son identité utilise un lien ou une jonction."
        )
    if not metadata_path.is_file():
        raise ProjectIdentityError(
            "Identité de projet absente. Relancez l'analyse et l'extraction pour rattacher ce CSV."
        )
    try:
        before = metadata_path.stat()
        raw = metadata_path.read_bytes()
        after = metadata_path.stat()
        payload = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise ProjectIdentityError("L'identité du projet est illisible.") from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ProjectIdentityError("L'identité du projet a changé pendant sa lecture.")
    if not isinstance(payload, dict) or payload.get("format") != "pft_project_identity_v1":
        raise ProjectIdentityError(
            "Identité de projet ancienne ou inconnue. Relancez l'analyse du fangame."
        )

    stored_root_text = str(payload.get("dossier_jeu") or "")
    stored_root = Path(stored_root_text).expanduser()
    if not stored_root.is_absolute():
        raise ProjectIdentityError("Le chemin du fangame dans l'identité du projet est invalide.")
    stored_root = stored_root.resolve()
    expected_root = game_root.expanduser().resolve()
    if os.path.normcase(str(stored_root)) != os.path.normcase(str(expected_root)):
        raise ProjectIdentityError(
            "Ce CSV appartient à un autre fangame. Sélectionnez le projet associé au jeu courant."
        )

    adapter_id = str(payload.get("adapter_id") or "")
    if adapter_id != expected_adapter_id:
        raise ProjectIdentityError(
            "L'adaptateur du projet est absent ou différent. Relancez l'analyse et l'extraction."
        )
    return ProjectIdentity(
        metadata_path=metadata_path.resolve(),
        game_root=stored_root,
        adapter_id=adapter_id,
        adapter_version=str(payload.get("adapter_version") or ""),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
