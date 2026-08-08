# SPDX-License-Identifier: GPL-3.0-or-later
"""Écritures atomiques communes, sans nom temporaire prévisible."""
from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Mapping, TextIO


FileSignature = tuple[int, int, int, int]


@dataclass(frozen=True)
class StableFileState:
    """Empreinte de contenu et identité d'un fichier lu sans changement."""

    sha256: str
    signature: FileSignature


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x0400)
    except OSError:
        return False


def _prepare_destination(path: Path) -> Path:
    destination = path.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _is_link_or_junction(destination.parent):
        raise OSError("Le dossier de destination est un lien ou une jonction.")
    if _is_link_or_junction(destination):
        raise OSError("Le fichier de destination est un lien ou une jonction.")
    return destination


@contextmanager
def atomic_text_writer(
    path: Path,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> Iterator[TextIO]:
    """Fournit un fichier texte voisin unique puis le remplace atomiquement."""
    destination = _prepare_destination(path)
    temporary_path: Path | None = None
    handle: TextIO | None = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline=newline,
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        )
        temporary_path = Path(handle.name)
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if handle is not None and not handle.closed:
            handle.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    with atomic_text_writer(path, encoding=encoding, newline=newline) as handle:
        handle.write(content)


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    validator: Callable[[Path], object] | None = None,
) -> None:
    """Écrit des octets, valide éventuellement le temporaire, puis remplace."""
    destination = _prepare_destination(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if validator is not None:
            validator(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _copy_stream_and_hash(source_handle: BinaryIO, target_handle: BinaryIO) -> str:
    """Copie le flux tout en calculant l'empreinte des octets effectivement lus."""
    digest = hashlib.sha256()
    while True:
        chunk = source_handle.read(1024 * 1024)
        if not chunk:
            return digest.hexdigest()
        target_handle.write(chunk)
        digest.update(chunk)


def _source_signature(stat_result: os.stat_result) -> FileSignature:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _read_stable_bytes_with_signature(path: Path) -> tuple[bytes, FileSignature]:
    source = path.expanduser()
    if (
        not source.is_file()
        or _is_link_or_junction(source)
        or _is_link_or_junction(source.parent)
    ):
        raise OSError("Le fichier est absent, inaccessible ou redirigé.")
    before = source.stat()
    with source.open("rb") as handle:
        opened_before = os.fstat(handle.fileno())
        if _source_signature(before) != _source_signature(opened_before):
            raise OSError("Le fichier a changé avant sa lecture.")
        payload = handle.read()
        opened_after = os.fstat(handle.fileno())
    after = source.stat()
    signatures = {
        _source_signature(before),
        _source_signature(opened_before),
        _source_signature(opened_after),
        _source_signature(after),
    }
    if len(signatures) != 1 or _is_link_or_junction(source):
        raise OSError("Le fichier a changé pendant sa lecture.")
    return payload, _source_signature(after)


def read_stable_bytes(path: Path) -> bytes:
    """Lit un fichier sans accepter un remplacement ou une redirection en cours."""
    payload, _signature = _read_stable_bytes_with_signature(path)
    return payload


def read_stable_file(path: Path) -> tuple[bytes, StableFileState]:
    """Lit un fichier et fige aussi son identité pour les contrôles ultérieurs."""
    payload, signature = _read_stable_bytes_with_signature(path)
    return payload, StableFileState(
        sha256=hashlib.sha256(payload).hexdigest(),
        signature=signature,
    )


def atomic_copy_file(
    source: Path,
    destination: Path,
    *,
    validator: Callable[[Path], object] | None = None,
    replace_existing: bool = True,
    expected_sha256: str | None = None,
) -> None:
    """Copie un fichier par flux puis le publie atomiquement après validation."""
    source_path = source.expanduser()
    if not source_path.is_file() or _is_link_or_junction(source_path):
        raise OSError("Le fichier source est absent ou redirigé.")
    expected = expected_sha256.casefold() if expected_sha256 is not None else None
    if expected is not None and (
        len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise ValueError("L'empreinte SHA-256 attendue est invalide.")
    target = _prepare_destination(destination)
    temporary_path: Path | None = None
    try:
        source_before = source_path.stat()
        with source_path.open("rb") as source_handle, tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as target_handle:
            temporary_path = Path(target_handle.name)
            opened_before = os.fstat(source_handle.fileno())
            if _source_signature(source_before) != _source_signature(opened_before):
                raise OSError("Le fichier source a changé avant sa copie.")
            copied_sha256 = _copy_stream_and_hash(source_handle, target_handle)
            opened_after = os.fstat(source_handle.fileno())
            target_handle.flush()
            os.fsync(target_handle.fileno())
        source_after = source_path.stat()
        if not (
            _source_signature(source_before)
            == _source_signature(opened_before)
            == _source_signature(opened_after)
            == _source_signature(source_after)
        ):
            raise OSError("Le fichier source a changé pendant sa copie.")
        if expected is not None and copied_sha256 != expected:
            raise OSError("La copie ne correspond pas à l'empreinte SHA-256 attendue.")
        if validator is not None:
            validator(temporary_path)
        if replace_existing:
            os.replace(temporary_path, target)
        else:
            os.link(temporary_path, target)
            temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _replace_file(source: Path, destination: Path) -> None:
    """Point d'injection testé pour la publication et son rollback."""
    os.replace(source, destination)


@dataclass
class _BundleArtifact:
    destination: Path
    payload: bytes
    existed: bool
    previous_sha256: str | None
    previous_signature: tuple[int, int, int, int] | None
    staged_path: Path
    rollback_path: Path | None


def _write_neighbor_temporary(destination: Path, payload: bytes, role: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.pft-bundle-{role}-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def _current_artifact_state(
    destination: Path,
) -> tuple[bool, str | None, tuple[int, int, int, int] | None, bytes | None]:
    if not destination.exists():
        if _is_link_or_junction(destination):
            raise OSError("Un artefact de destination est redirigé.")
        return False, None, None, None
    payload, signature = _read_stable_bytes_with_signature(destination)
    return True, hashlib.sha256(payload).hexdigest(), signature, payload


def atomic_write_bundle(
    artifacts: Mapping[Path, bytes],
    *,
    expected_existing_sha256: Mapping[Path, str | None] | None = None,
    expected_existing_signatures: Mapping[Path, FileSignature | None] | None = None,
    guarded_existing: Mapping[Path, StableFileState | None] | None = None,
) -> None:
    """Publie plusieurs artefacts atomiques ou restaure exactement l'état précédent.

    Chaque fichier est remplacé atomiquement. La transaction vérifie à nouveau
    sa destination juste avant chaque remplacement ; une modification concurrente
    annule le lot et les fichiers déjà publiés sont restaurés.
    """
    if not artifacts:
        return

    expected_by_path = {
        os.path.normcase(str(Path(path).expanduser().resolve())): expected
        for path, expected in (expected_existing_sha256 or {}).items()
    }
    expected_signatures_by_path = {
        os.path.normcase(str(Path(path).expanduser().resolve())): expected
        for path, expected in (expected_existing_signatures or {}).items()
    }
    guarded_by_path = {
        os.path.normcase(str(Path(path).expanduser().resolve())): (
            Path(path).expanduser(),
            expected,
        )
        for path, expected in (guarded_existing or {}).items()
    }

    def assert_guards_unchanged() -> None:
        for path, expected in guarded_by_path.values():
            exists, current_sha256, current_signature, _payload = _current_artifact_state(path)
            if expected is None:
                if exists:
                    raise OSError("Un artefact surveillé est apparu pendant la publication.")
                continue
            if (
                not exists
                or current_sha256 != expected.sha256
                or current_signature != expected.signature
            ):
                raise OSError("Un artefact surveillé a changé pendant la publication.")

    prepared: list[_BundleArtifact] = []
    seen: set[str] = set()
    committed: list[_BundleArtifact] = []
    preserved_rollback_paths: set[Path] = set()
    try:
        assert_guards_unchanged()
        for requested, payload in sorted(
            artifacts.items(),
            key=lambda item: os.path.normcase(str(Path(item[0]).expanduser().resolve())),
        ):
            destination = _prepare_destination(Path(requested))
            identity = os.path.normcase(str(destination.resolve()))
            if identity in seen:
                raise ValueError("Le lot contient deux chemins désignant le même artefact.")
            seen.add(identity)

            (
                existed,
                previous_sha256,
                previous_signature,
                previous_payload,
            ) = _current_artifact_state(destination)
            if identity in expected_by_path:
                expected = expected_by_path[identity]
                if expected is None and existed:
                    raise OSError("Un artefact concurrent est apparu avant la publication.")
                if expected is not None and previous_sha256 != expected.casefold():
                    raise OSError("Un artefact a changé depuis sa validation initiale.")
            if identity in expected_signatures_by_path:
                expected_signature = expected_signatures_by_path[identity]
                if expected_signature is None and existed:
                    raise OSError("Un artefact concurrent a remplacé une destination absente.")
                if expected_signature is not None and previous_signature != expected_signature:
                    raise OSError("Un artefact a été remplacé depuis sa validation initiale.")

            rollback_path = (
                _write_neighbor_temporary(
                    destination,
                    previous_payload or b"",
                    "old",
                )
                if existed
                else None
            )
            staged_path = _write_neighbor_temporary(destination, bytes(payload), "new")
            prepared.append(
                _BundleArtifact(
                    destination=destination,
                    payload=bytes(payload),
                    existed=existed,
                    previous_sha256=previous_sha256,
                    previous_signature=previous_signature,
                    staged_path=staged_path,
                    rollback_path=rollback_path,
                )
            )

        assert_guards_unchanged()
        for artifact in prepared:
            (
                existed,
                current_sha256,
                current_signature,
                _current_payload,
            ) = _current_artifact_state(artifact.destination)
            if (
                existed != artifact.existed
                or current_sha256 != artifact.previous_sha256
                or current_signature != artifact.previous_signature
            ):
                raise OSError("Un artefact a changé pendant la préparation du lot.")
            _replace_file(artifact.staged_path, artifact.destination)
            committed.append(artifact)
        assert_guards_unchanged()

    except Exception as publication_error:
        rollback_errors: list[str] = []
        for artifact in reversed(committed):
            try:
                if artifact.rollback_path is not None:
                    _replace_file(artifact.rollback_path, artifact.destination)
                    artifact.rollback_path = None
                else:
                    artifact.destination.unlink(missing_ok=True)
            except OSError as exc:
                recovery = (
                    artifact.rollback_path.name
                    if artifact.rollback_path is not None
                    else "indisponible"
                )
                rollback_errors.append(
                    f"{artifact.destination.name} ({type(exc).__name__}, récupération : {recovery})"
                )
                if artifact.rollback_path is not None:
                    preserved_rollback_paths.add(artifact.rollback_path)
        if rollback_errors:
            raise OSError(
                "Échec de publication et rollback incomplet du lot : "
                + ", ".join(rollback_errors)
            ) from publication_error
        raise
    finally:
        for artifact in prepared:
            artifact.staged_path.unlink(missing_ok=True)
            if (
                artifact.rollback_path is not None
                and artifact.rollback_path not in preserved_rollback_paths
            ):
                artifact.rollback_path.unlink(missing_ok=True)
