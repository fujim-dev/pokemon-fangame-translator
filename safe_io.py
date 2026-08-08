# SPDX-License-Identifier: GPL-3.0-or-later
"""Écritures atomiques communes, sans nom temporaire prévisible."""
from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, TextIO


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


def _source_signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
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
