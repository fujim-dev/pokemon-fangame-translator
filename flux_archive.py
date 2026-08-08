# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture sécurisée des archives FPK de Pokémon Flux via 7-Zip.

Ce module ne reconstruit aucune archive. Il inventorie ou extrait uniquement
dans un dossier temporaire fourni par l'appelant, après validation de tous les
chemins internes.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
MAX_ARCHIVE_ENTRIES = 100_000
MAX_UNPACKED_SIZE = 8 * 1024 * 1024 * 1024
MAX_MEMBER_SIZE = 2 * 1024 * 1024 * 1024
MAX_STREAMED_MEMBER_SIZE = 64 * 1024 * 1024
MAX_LISTING_SIZE = 16 * 1024 * 1024


class FluxArchiveError(RuntimeError):
    """Archive Flux impossible à analyser sans risque."""


@dataclass(frozen=True)
class FluxArchiveEntry:
    path: str
    size: int
    packed_size: int
    attributes: str
    encrypted: bool
    method: str

    @property
    def normalized_path(self) -> str:
        return self.path.replace("\\", "/")

    @property
    def is_directory(self) -> bool:
        return self.normalized_path.endswith("/") or "D" in self.attributes.upper()


@dataclass(frozen=True)
class FluxArchiveInventory:
    archive_type: str
    physical_size: int
    entries: tuple[FluxArchiveEntry, ...]
    issues: tuple[str, ...] = ()

    @property
    def file_entries(self) -> tuple[FluxArchiveEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.is_directory)

    @property
    def member_paths(self) -> frozenset[str]:
        return frozenset(entry.normalized_path for entry in self.file_entries)

    @property
    def unpacked_size(self) -> int:
        return sum(entry.size for entry in self.file_entries)

    @property
    def safe(self) -> bool:
        return self.archive_type.casefold() == "7z" and not self.issues


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        return bool(path.lstat().st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return False
    except OSError:
        return False


def _member_path_issue(value: str) -> str | None:
    normalized = str(value or "").replace("\\", "/")
    windows = PureWindowsPath(value)
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
    ):
        return "chemin absolu ou vide"

    invalid_windows = set('<>:"|?*')
    reserved_windows = {
        "con", "prn", "aux", "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "segment interdit"
    for part in parts:
        if (
            any(ord(character) < 32 or character in invalid_windows for character in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in reserved_windows
        ):
            return "nom Windows interdit"
    return None


def _parse_integer(value: str, field: str, *, empty_as_zero: bool = False) -> int:
    if empty_as_zero and not value.strip():
        return 0
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise FluxArchiveError(f"Valeur 7-Zip invalide pour {field}.") from exc
    if parsed < 0:
        raise FluxArchiveError(f"Valeur 7-Zip négative pour {field}.")
    return parsed


def parse_7zip_slt(output: str) -> FluxArchiveInventory:
    """Parse la sortie ``7z l -slt`` sans faire confiance aux chemins listés."""
    if len(output.encode("utf-8", errors="replace")) > MAX_LISTING_SIZE:
        raise FluxArchiveError("La liste de l'archive est anormalement volumineuse.")
    lines = output.splitlines()
    try:
        separator = lines.index("----------")
    except ValueError as exc:
        raise FluxArchiveError("Format de liste 7-Zip non reconnu.") from exc

    header: dict[str, str] = {}
    for line in lines[:separator]:
        if " = " in line:
            key, value = line.split(" = ", 1)
            header[key] = value

    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines[separator + 1:]:
        if line.startswith("Path = "):
            if current is not None:
                records.append(current)
            current = {"Path": line[7:]}
        elif current is not None and " = " in line:
            key, value = line.split(" = ", 1)
            current[key] = value
    if current is not None:
        records.append(current)

    entries: list[FluxArchiveEntry] = []
    issues: list[str] = []
    path_keys: set[str] = set()
    for record in records:
        path = record.get("Path", "")
        path_issue = _member_path_issue(path)
        if path_issue:
            issues.append(f"Chemin interne refusé ({path_issue}) : {path[:160]}")
        normalized_key = path.replace("\\", "/").casefold()
        if normalized_key in path_keys:
            issues.append(f"Collision de chemins internes : {path[:160]}")
        path_keys.add(normalized_key)
        entries.append(
            FluxArchiveEntry(
                path=path,
                size=_parse_integer(record.get("Size", "0"), "Size"),
                packed_size=_parse_integer(
                    record.get("Packed Size", "0"),
                    "Packed Size",
                    empty_as_zero=True,
                ),
                attributes=record.get("Attributes", ""),
                encrypted=record.get("Encrypted", "-").strip() != "-",
                method=record.get("Method", ""),
            )
        )

    if not entries:
        issues.append("L'archive ne contient aucun fichier inventoriable.")
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        issues.append("L'archive contient trop d'entrées pour une analyse sûre.")
    if any(entry.size > MAX_MEMBER_SIZE for entry in entries):
        issues.append("Un membre de l'archive dépasse la taille de sécurité.")
    if sum(entry.size for entry in entries) > MAX_UNPACKED_SIZE:
        issues.append("La taille décompressée dépasse la limite de sécurité.")
    if any(entry.encrypted for entry in entries):
        issues.append("Une archive Flux chiffrée n'est pas prise en charge.")

    archive_type = header.get("Type", "")
    if archive_type.casefold() != "7z":
        issues.append("Le FPK n'est pas une archive 7z reconnue.")
    physical_size = _parse_integer(header.get("Physical Size", "0"), "Physical Size")
    return FluxArchiveInventory(
        archive_type=archive_type,
        physical_size=physical_size,
        entries=tuple(entries),
        issues=tuple(dict.fromkeys(issues)),
    )


def find_7zip() -> Path | None:
    """Cherche uniquement une installation locale ; aucun téléchargement implicite."""
    app_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = [
        app_root / "tools" / "7zr.exe",
        app_root / "tools" / "7z.exe",
        Path(__file__).resolve().parent / "build_support" / "tools" / "7zr.exe",
        Path(__file__).resolve().parent / "build_support" / "tools" / "7z.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
    ]
    for candidate in candidates:
        if candidate.is_file() and not _is_link_or_junction(candidate):
            return candidate
    for command in ("7z", "7zz", "7za", "7zr"):
        found = shutil.which(command)
        if found:
            candidate = Path(found)
            if candidate.is_file() and not _is_link_or_junction(candidate):
                return candidate
    return None


class FluxArchiveReader:
    def __init__(self, seven_zip: Path | None = None, *, timeout_seconds: int = 120):
        self.seven_zip = Path(seven_zip) if seven_zip else find_7zip()
        self.timeout_seconds = timeout_seconds

    def _run(self, args: list[str], *, binary: bool = False) -> subprocess.CompletedProcess:
        if not self.seven_zip or not self.seven_zip.is_file():
            raise FluxArchiveError(
                "7-Zip est introuvable. L'analyse Flux reste bloquée sans extraction implicite."
            )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run(
                [str(self.seven_zip), *args],
                capture_output=True,
                text=not binary,
                encoding=None if binary else "utf-8",
                errors=None if binary else "replace",
                creationflags=creationflags,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FluxArchiveError(f"7-Zip n'a pas pu analyser l'archive : {exc}") from exc

    @staticmethod
    def _assert_archive_file(archive_path: Path) -> Path:
        expanded = archive_path.expanduser()
        if (
            not expanded.is_file()
            or _is_link_or_junction(expanded)
            or _is_link_or_junction(expanded.parent)
        ):
            raise FluxArchiveError("Le FPK est absent, illisible ou redirigé.")
        return expanded.resolve()

    def inspect(self, archive_path: Path) -> FluxArchiveInventory:
        archive = self._assert_archive_file(archive_path)
        result = self._run(["l", "-slt", "--", str(archive)])
        if result.returncode != 0:
            details = ((result.stdout or "") + "\n" + (result.stderr or ""))[-4000:]
            raise FluxArchiveError("7-Zip refuse de lister le FPK :\n" + details)
        return parse_7zip_slt(result.stdout or "")

    def read_member(
        self,
        archive_path: Path,
        member_path: str,
        inventory: FluxArchiveInventory | None = None,
    ) -> bytes:
        archive = self._assert_archive_file(archive_path)
        inventory = inventory or self.inspect(archive)
        normalized = member_path.replace("\\", "/")
        if not inventory.safe:
            raise FluxArchiveError("Lecture d'un membre refusée : inventaire FPK non sûr.")
        matches = [entry for entry in inventory.file_entries if entry.normalized_path == normalized]
        if len(matches) != 1:
            raise FluxArchiveError("Le membre FPK attendu est absent ou ambigu.")
        if matches[0].size > MAX_STREAMED_MEMBER_SIZE:
            raise FluxArchiveError("Le membre FPK est trop volumineux pour une lecture en mémoire.")
        result = self._run(["e", "-so", "--", str(archive), matches[0].path], binary=True)
        if result.returncode != 0:
            details = (result.stderr or b"")[-4000:].decode("utf-8", errors="replace")
            raise FluxArchiveError("7-Zip refuse de lire le membre FPK :\n" + details)
        payload = result.stdout or b""
        if len(payload) != matches[0].size:
            raise FluxArchiveError("La taille du membre FPK lu ne correspond pas à l'inventaire.")
        return payload

    def member_sha256(
        self,
        archive_path: Path,
        member_path: str,
        inventory: FluxArchiveInventory | None = None,
    ) -> str:
        return hashlib.sha256(
            self.read_member(archive_path, member_path, inventory)
        ).hexdigest()

    def extract_to(
        self,
        archive_path: Path,
        target_root: Path,
        inventory: FluxArchiveInventory | None = None,
    ) -> None:
        """Extrait dans un dossier vide et compare exactement l'inventaire obtenu."""
        archive = self._assert_archive_file(archive_path)
        inventory = inventory or self.inspect(archive)
        if not inventory.safe:
            raise FluxArchiveError("Extraction refusée : inventaire FPK non sûr.")

        expanded_target = target_root.expanduser()
        if not expanded_target.is_dir() or _is_link_or_junction(expanded_target):
            raise FluxArchiveError("Le dossier temporaire Flux est absent ou redirigé.")
        target = expanded_target.resolve()
        if any(target.iterdir()):
            raise FluxArchiveError("Le dossier temporaire Flux doit être vide.")

        result = self._run(["x", "-y", "-aoa", f"-o{target}", "--", str(archive)])
        if result.returncode != 0:
            details = ((result.stdout or "") + "\n" + (result.stderr or ""))[-4000:]
            raise FluxArchiveError("7-Zip refuse d'extraire le FPK temporaire :\n" + details)

        extracted: dict[str, tuple[Path, int]] = {}
        pending = [target]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise FluxArchiveError(f"Dossier temporaire Flux illisible : {exc}") from exc
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(target).as_posix()
                try:
                    stat_result = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise FluxArchiveError(f"Entrée Flux temporaire illisible : {relative}") from exc
                if entry.is_symlink() or bool(
                    getattr(stat_result, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise FluxArchiveError(f"Lien ou jonction refusé dans le FPK : {relative}")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    key = relative.casefold()
                    if key in extracted:
                        raise FluxArchiveError(f"Collision après extraction du FPK : {relative}")
                    extracted[key] = (path, stat_result.st_size)
                else:
                    raise FluxArchiveError(f"Type de fichier FPK non pris en charge : {relative}")

        expected = {
            entry.normalized_path.casefold(): entry
            for entry in inventory.file_entries
        }
        if set(extracted) != set(expected):
            raise FluxArchiveError("L'inventaire extrait diffère de l'inventaire annoncé par le FPK.")
        for key, (_path, size) in extracted.items():
            if size != expected[key].size:
                raise FluxArchiveError(
                    f"Taille différente après extraction du FPK : {expected[key].normalized_path}"
                )
