# SPDX-License-Identifier: GPL-3.0-or-later
"""Écritures atomiques communes, sans nom temporaire prévisible."""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TextIO


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


def atomic_copy_file(
    source: Path,
    destination: Path,
    *,
    validator: Callable[[Path], object] | None = None,
    replace_existing: bool = True,
) -> None:
    """Copie un fichier par flux puis le publie atomiquement après validation."""
    source_path = source.expanduser()
    if not source_path.is_file() or _is_link_or_junction(source_path):
        raise OSError("Le fichier source est absent ou redirigé.")
    target = _prepare_destination(destination)
    temporary_path: Path | None = None
    try:
        with source_path.open("rb") as source_handle, tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as target_handle:
            temporary_path = Path(target_handle.name)
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
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
