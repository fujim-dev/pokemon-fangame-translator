# SPDX-License-Identifier: GPL-3.0-or-later
"""Empreintes et comparaisons d'intégrité sans conserver le contenu des fichiers."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class IntegrityError(RuntimeError):
    """Signale une arborescence impossible à inventorier de manière fiable."""


@dataclass(frozen=True)
class FileFingerprint:
    sha256: str
    size: int


@dataclass(frozen=True)
class TreeSnapshot:
    root: str
    files: dict[str, FileFingerprint]

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(fingerprint.size for fingerprint in self.files.values())


@dataclass(frozen=True)
class SnapshotComparison:
    reference_file_count: int
    candidate_file_count: int
    missing_files: tuple[str, ...]
    unexpected_files: tuple[str, ...]
    changed_files: tuple[str, ...]
    emptied_files: tuple[str, ...]
    allowed_fingerprints: dict[str, dict[str, str | int]]

    @property
    def passed(self) -> bool:
        return not (
            self.missing_files
            or self.unexpected_files
            or self.changed_files
            or self.emptied_files
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "statut": "valide" if self.passed else "invalide",
            "fichiers_reference": self.reference_file_count,
            "fichiers_controles": self.candidate_file_count,
            "fichiers_manquants": list(self.missing_files),
            "fichiers_inattendus": list(self.unexpected_files),
            "fichiers_modifies_hors_plan": list(self.changed_files),
            "fichiers_devenus_vides": list(self.emptied_files),
            "empreintes_fichiers_cibles": self.allowed_fingerprints,
        }


def _relative_sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


def _path_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _is_redirected(entry: os.DirEntry[str], stat_result: os.stat_result) -> bool:
    return entry.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _fingerprint_file(path: Path) -> FileFingerprint:
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise IntegrityError(f"Impossible de calculer l'empreinte de {path.name} : {exc}") from exc

    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise IntegrityError(
            f"Le fichier {path.name} a changé pendant le calcul de son empreinte."
        )
    return FileFingerprint(sha256=digest.hexdigest(), size=after.st_size)


def snapshot_tree(root: Path) -> TreeSnapshot:
    """Inventorie les fichiers ordinaires d'une arborescence sans suivre de lien."""
    expanded_root = root.expanduser()
    try:
        root_stat = expanded_root.lstat()
    except OSError as exc:
        raise IntegrityError(f"Dossier d'intégrité introuvable : {expanded_root}") from exc
    if expanded_root.is_symlink() or bool(
        getattr(root_stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise IntegrityError("Le dossier d'intégrité ne peut pas être un lien ou une jonction.")

    resolved_root = expanded_root.resolve()
    if not resolved_root.is_dir():
        raise IntegrityError(f"Dossier d'intégrité introuvable : {resolved_root}")

    files: dict[str, FileFingerprint] = {}
    pending = [resolved_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        except OSError as exc:
            raise IntegrityError(f"Impossible d'inventorier {directory.name} : {exc}") from exc

        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(resolved_root).as_posix()
            try:
                stat_result = entry.stat(follow_symlinks=False)
                if _is_redirected(entry, stat_result):
                    raise IntegrityError(
                        f"Lien symbolique ou jonction refusé pendant l'intégrité : {relative}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files[relative] = _fingerprint_file(path)
                else:
                    raise IntegrityError(
                        f"Type de fichier non pris en charge pendant l'intégrité : {relative}"
                    )
            except OSError as exc:
                raise IntegrityError(f"Impossible d'inspecter {relative} : {exc}") from exc

    ordered = dict(sorted(files.items(), key=lambda item: _relative_sort_key(item[0])))
    return TreeSnapshot(root=str(resolved_root), files=ordered)


def compare_snapshots(
    reference: TreeSnapshot,
    candidate: TreeSnapshot,
    *,
    allowed_changed: Iterable[str] = (),
) -> SnapshotComparison:
    """Compare deux inventaires et tolère uniquement les fichiers ciblés annoncés."""
    reference_paths = set(reference.files)
    candidate_paths = set(candidate.files)
    common_paths = reference_paths & candidate_paths
    allowed_keys = {_path_key(relative) for relative in allowed_changed}

    missing = tuple(sorted(reference_paths - candidate_paths, key=_relative_sort_key))
    unexpected = tuple(sorted(candidate_paths - reference_paths, key=_relative_sort_key))
    changed: list[str] = []
    emptied: list[str] = []
    allowed_fingerprints: dict[str, dict[str, str | int]] = {}

    for relative in sorted(common_paths, key=_relative_sort_key):
        before = reference.files[relative]
        after = candidate.files[relative]
        is_allowed = _path_key(relative) in allowed_keys
        if before.size > 0 and after.size == 0:
            emptied.append(relative)
        if is_allowed:
            allowed_fingerprints[relative] = {
                "avant": before.sha256,
                "apres": after.sha256,
                "taille_avant": before.size,
                "taille_apres": after.size,
            }
        elif before.sha256 != after.sha256:
            changed.append(relative)

    return SnapshotComparison(
        reference_file_count=reference.file_count,
        candidate_file_count=candidate.file_count,
        missing_files=missing,
        unexpected_files=unexpected,
        changed_files=tuple(changed),
        emptied_files=tuple(emptied),
        allowed_fingerprints=allowed_fingerprints,
    )
