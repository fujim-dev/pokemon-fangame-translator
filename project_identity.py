# SPDX-License-Identifier: GPL-3.0-or-later
"""Identité privée liant un projet de traduction à son fangame source."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from safe_io import atomic_write_bytes, read_stable_bytes


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
    source_manifest_sha256: str = ""
    extraction_manifest_sha256: str = ""
    extraction_id: str = ""
    extracted_csv_sha256: str = ""


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


def build_project_identity_bytes(
    game_root: Path,
    *,
    adapter_id: str = "",
    adapter_version: str = "",
    software_version: str = "1.0.2",
    source_manifest_sha256: str = "",
    extraction_manifest_name: str = "",
    extraction_manifest_sha256: str = "",
    extraction_id: str = "",
    extracted_csv_sha256: str = "",
) -> bytes:
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
    if source_manifest_sha256:
        payload.update(
            {
                "source_manifest_sha256": source_manifest_sha256,
                "extraction_manifest_name": extraction_manifest_name,
                "extraction_manifest_sha256": extraction_manifest_sha256,
                "extraction_id": extraction_id,
                "extracted_csv_sha256": extracted_csv_sha256,
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def write_project_identity(
    project_dir: Path,
    game_root: Path,
    *,
    adapter_id: str = "",
    adapter_version: str = "",
    software_version: str = "1.0.2",
    source_manifest_sha256: str = "",
    extraction_manifest_name: str = "",
    extraction_manifest_sha256: str = "",
    extraction_id: str = "",
    extracted_csv_sha256: str = "",
) -> Path:
    project = project_dir.expanduser()
    project.mkdir(parents=True, exist_ok=True)
    if _is_redirected(project):
        raise ProjectIdentityError(
            "Le dossier du projet ne peut pas être un lien ou une jonction."
        )
    destination = project / PROJECT_METADATA_NAME
    if destination.exists() and not source_manifest_sha256:
        try:
            previous = json.loads(read_stable_bytes(destination).decode("utf-8-sig"))
        except Exception as exc:
            raise ProjectIdentityError(
                "L'identité existante est illisible et ne sera pas remplacée silencieusement."
            ) from exc
        if not isinstance(previous, dict):
            raise ProjectIdentityError(
                "L'identité existante est inconnue et ne sera pas remplacée silencieusement."
            )
        previous_root = Path(str(previous.get("dossier_jeu") or "")).expanduser()
        same_root = (
            previous.get("format") == "pft_project_identity_v1"
            and previous_root.is_absolute()
            and os.path.normcase(str(previous_root.resolve()))
            == os.path.normcase(str(game_root.expanduser().resolve()))
        )
        previous_adapter = str(previous.get("adapter_id") or "")
        compatible_adapter = not adapter_id or not previous_adapter or adapter_id == previous_adapter
        if not same_root:
            raise ProjectIdentityError(
                "L'identité existante appartient à un autre fangame et ne sera pas remplacée."
            )
        if not compatible_adapter:
            raise ProjectIdentityError(
                "L'identité existante appartient à un autre adaptateur et ne sera pas remplacée."
            )
        previous_source_manifest = str(previous.get("source_manifest_sha256") or "")
        if previous_source_manifest:
            # Un simple diagnostic ne constitue pas une nouvelle extraction. Il
            # ne doit donc ni réhorodater ni réécrire une identité déjà ancrée à
            # un manifeste, sinon une session ouverte verrait un faux conflit.
            return destination
        adapter_id = adapter_id or previous_adapter
        adapter_version = adapter_version or str(previous.get("adapter_version") or "")
    atomic_write_bytes(
        destination,
        build_project_identity_bytes(
            game_root,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            software_version=software_version,
            source_manifest_sha256=source_manifest_sha256,
            extraction_manifest_name=extraction_manifest_name,
            extraction_manifest_sha256=extraction_manifest_sha256,
            extraction_id=extraction_id,
            extracted_csv_sha256=extracted_csv_sha256,
        ),
    )
    return destination


def _validated_sha256(value: object, label: str) -> str:
    digest = str(value or "").casefold()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ProjectIdentityError(f"L'empreinte {label} de l'identité est invalide.")
    return digest


def read_project_identity(
    csv_path: Path,
    game_root: Path,
    *,
    expected_adapter_id: str,
    require_extraction_provenance: bool = False,
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
        raw = read_stable_bytes(metadata_path)
        payload = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise ProjectIdentityError("L'identité du projet est illisible.") from exc
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
    source_manifest_sha256 = str(payload.get("source_manifest_sha256") or "")
    extraction_manifest_sha256 = ""
    extraction_id = str(payload.get("extraction_id") or "")
    extracted_csv_sha256 = ""
    if source_manifest_sha256:
        source_manifest_sha256 = _validated_sha256(
            source_manifest_sha256,
            "de l'inventaire source",
        )
        extraction_manifest_sha256 = _validated_sha256(
            payload.get("extraction_manifest_sha256"),
            "du manifeste d'extraction",
        )
        extracted_csv_sha256 = _validated_sha256(
            payload.get("extracted_csv_sha256"),
            "du CSV extrait",
        )
        manifest_name = str(payload.get("extraction_manifest_name") or "")
        if not manifest_name or Path(manifest_name).name != manifest_name:
            raise ProjectIdentityError("Le chemin du manifeste d'extraction est invalide.")
        manifest_path = project_dir / manifest_name
        if _is_redirected(manifest_path):
            raise ProjectIdentityError("Le manifeste d'extraction est redirigé.")
        try:
            manifest_raw = read_stable_bytes(manifest_path)
            manifest = json.loads(manifest_raw.decode("utf-8-sig"))
        except Exception as exc:
            raise ProjectIdentityError("Le manifeste d'extraction est illisible.") from exc
        if hashlib.sha256(manifest_raw).hexdigest() != extraction_manifest_sha256:
            raise ProjectIdentityError("Le manifeste d'extraction ne correspond plus au projet.")
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != "pft_essentials_extraction_v1"
            or str(manifest.get("adapter_id") or "") != adapter_id
            or str(manifest.get("source_manifest_sha256") or "").casefold()
            != source_manifest_sha256
            or str(manifest.get("extraction_id") or "") != extraction_id
            or str(manifest.get("csv_sha256") or "").casefold() != extracted_csv_sha256
        ):
            raise ProjectIdentityError(
                "Le manifeste d'extraction est incohérent avec l'identité du projet."
            )
        manifest_root = Path(str(manifest.get("game_root") or "")).expanduser()
        if (
            not manifest_root.is_absolute()
            or os.path.normcase(str(manifest_root.resolve()))
            != os.path.normcase(str(expected_root))
        ):
            raise ProjectIdentityError(
                "Le manifeste d'extraction appartient à un autre fangame."
            )
    elif require_extraction_provenance:
        raise ProjectIdentityError(
            "Ce projet ancien ne possède pas de manifeste de provenance fiable. "
            "Relancez l'extraction pour conserver les traductions dans un projet vérifié."
        )

    return ProjectIdentity(
        metadata_path=metadata_path.resolve(),
        game_root=stored_root,
        adapter_id=adapter_id,
        adapter_version=str(payload.get("adapter_version") or ""),
        sha256=hashlib.sha256(raw).hexdigest(),
        source_manifest_sha256=source_manifest_sha256,
        extraction_manifest_sha256=extraction_manifest_sha256,
        extraction_id=extraction_id,
        extracted_csv_sha256=extracted_csv_sha256,
    )
