# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Construction expérimentale d'un FPK candidat, jamais installé dans un jeu."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from analysis.integrity import compare_snapshots, snapshot_tree
from flux_archive import FluxArchiveError, FluxArchiveReader, find_7zip
from flux_import_plan import FluxImportPlan, FluxImportPlanItem
from ruby_marshal_reader import RubyHashKey, RubyObject, RubyString, RubyUserDefined, load
from ruby_marshal_writer import dumps
from safe_io import atomic_copy_file, atomic_write_bytes, atomic_write_text


class FluxReinjectionError(RuntimeError):
    """La création du candidat Flux ne peut pas être prouvée sûre."""


@dataclass(frozen=True)
class FluxCandidateResult:
    candidate_path: Path
    plan_fingerprint: str
    source_fpk_sha256: str
    candidate_fpk_sha256: str
    changed_members: tuple[str, ...]
    verified_members: int
    rollback_performed: bool = False


@dataclass(frozen=True)
class FluxWorkingCopyResult:
    working_copy: Path
    backup_path: Path
    candidate_sha256: str
    installed_sha256: str
    restored_sha256: str
    source_files_verified: int
    rollback_verified: bool


@dataclass(frozen=True)
class _Mutation:
    item: FluxImportPlanItem
    source_strings: tuple[RubyString, ...]
    target_strings: tuple[RubyString, ...]


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


def _resolved_game_root(plan: FluxImportPlan) -> Path:
    """Canonise la racine protégée avant toute comparaison de confinement."""
    root_input = Path(plan.game_root).expanduser()
    if _is_redirected(root_input):
        raise FluxReinjectionError("La racine du jeu original est redirigée.")
    try:
        return root_input.resolve()
    except (OSError, RuntimeError) as exc:
        raise FluxReinjectionError("La racine du jeu original ne peut pas être résolue.") from exc


def _is_same_or_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _stable_sha256(path: Path) -> str:
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise FluxReinjectionError(f"Empreinte impossible pour {path.name}.") from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise FluxReinjectionError(f"Le fichier {path.name} a changé pendant son contrôle.")
    return digest.hexdigest()


def _source_hash(strings: tuple[RubyString, ...]) -> str:
    digest = hashlib.sha256()
    for value in strings:
        digest.update(len(value.data).to_bytes(8, "big"))
        digest.update(value.data)
    return digest.hexdigest()


def _generic_value(root, tokens: tuple[object, ...]):
    current = root
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "list":
            if index + 1 >= len(tokens) or not isinstance(current, list):
                raise FluxReinjectionError("Chemin de liste Flux incohérent.")
            child_index = tokens[index + 1]
            if not isinstance(child_index, int) or not 0 <= child_index < len(current):
                raise FluxReinjectionError("Indice de liste Flux invalide.")
            current = current[child_index]
            index += 2
            continue
        if token == "dict":
            if index + 2 >= len(tokens) or not isinstance(current, dict):
                raise FluxReinjectionError("Chemin de dictionnaire Flux incohérent.")
            child_index = tokens[index + 1]
            side = tokens[index + 2]
            items = list(current.items())
            if not isinstance(child_index, int) or not 0 <= child_index < len(items):
                raise FluxReinjectionError("Indice de dictionnaire Flux invalide.")
            if side not in {"key", "value"}:
                raise FluxReinjectionError("Côté de dictionnaire Flux invalide.")
            current = items[child_index][0 if side == "key" else 1]
            index += 3
            continue
        if token == "ivar":
            if index + 1 >= len(tokens) or not isinstance(current, (RubyObject, RubyUserDefined)):
                raise FluxReinjectionError("Chemin d'attribut Ruby incohérent.")
            name = tokens[index + 1]
            if not isinstance(name, str) or name not in current.ivars:
                raise FluxReinjectionError("Attribut Ruby Flux introuvable.")
            current = current.ivars[name]
            index += 2
            continue
        if token == "hash_key":
            if not isinstance(current, RubyHashKey):
                raise FluxReinjectionError("Clé Ruby Flux incohérente.")
            current = current.value
            index += 1
            continue
        raise FluxReinjectionError("Jeton de chemin générique Flux inconnu.")
    return current


def _message_game_mutation(root, item: FluxImportPlanItem) -> _Mutation:
    tokens = item.structural_path
    if len(tokens) < 3 or tokens[-3] != "dict" or tokens[-1] != "key":
        raise FluxReinjectionError("Chemin messages_game Flux non reconnu.")
    parent = _generic_value(root, tokens[:-3])
    if not isinstance(parent, dict):
        raise FluxReinjectionError("Dictionnaire messages_game Flux introuvable.")
    entry_index = tokens[-2]
    entries = list(parent.items())
    if not isinstance(entry_index, int) or not 0 <= entry_index < len(entries):
        raise FluxReinjectionError("Indice messages_game Flux invalide.")
    source, current = entries[entry_index]
    if not isinstance(source, RubyString) or not isinstance(current, RubyString):
        raise FluxReinjectionError("Entrée messages_game Flux non textuelle.")
    return _Mutation(item, (source,), (current,))


def _event_commands(root, item: FluxImportPlanItem) -> tuple[list, tuple[object, ...]]:
    tokens = item.structural_path
    if item.source_kind == "map_events":
        if (
            len(tokens) < 7
            or tokens[0] != "events"
            or tokens[2] != "pages"
            or tokens[4] != "commands"
            or not isinstance(root, RubyObject)
            or root.class_name != "RPG::Map"
        ):
            raise FluxReinjectionError("Chemin d'événement de carte Flux non reconnu.")
        events = root.ivars.get("@events")
        if not isinstance(events, dict):
            raise FluxReinjectionError("Événements de carte Flux invalides.")
        event = events.get(tokens[1])
        pages = event.ivars.get("@pages") if isinstance(event, RubyObject) else None
        page_index = tokens[3]
        if not isinstance(pages, list) or not isinstance(page_index, int) or not 0 <= page_index < len(pages):
            raise FluxReinjectionError("Page d'événement Flux introuvable.")
        page = pages[page_index]
        commands = page.ivars.get("@list") if isinstance(page, RubyObject) else None
        tail = tokens[5:]
    elif item.source_kind == "common_events":
        if len(tokens) < 5 or tokens[0] != "common_events" or tokens[2] != "commands":
            raise FluxReinjectionError("Chemin d'événement commun Flux non reconnu.")
        event_index = tokens[1]
        if not isinstance(root, list) or not isinstance(event_index, int) or not 0 <= event_index < len(root):
            raise FluxReinjectionError("Événement commun Flux introuvable.")
        event = root[event_index]
        commands = event.ivars.get("@list") if isinstance(event, RubyObject) else None
        tail = tokens[3:]
    else:
        raise FluxReinjectionError("Source d'événement Flux incohérente.")
    if not isinstance(commands, list):
        raise FluxReinjectionError("Liste de commandes Flux invalide.")
    return commands, tail


def _event_mutation(root, item: FluxImportPlanItem) -> _Mutation:
    commands, tail = _event_commands(root, item)
    if len(tail) != 3 or not isinstance(tail[0], int):
        raise FluxReinjectionError("Cible de commande Flux invalide.")
    command_index, kind, detail = tail
    if not 0 <= command_index < len(commands):
        raise FluxReinjectionError("Commande Flux introuvable.")
    command = commands[command_index]
    if not isinstance(command, RubyObject):
        raise FluxReinjectionError("Commande Flux non reconnue.")
    if kind == "dialogue":
        if command.ivars.get("@code") != 101 or not isinstance(detail, int) or detail < 1:
            raise FluxReinjectionError("Début de dialogue Flux incohérent.")
        strings: list[RubyString] = []
        for offset in range(detail):
            position = command_index + offset
            if position >= len(commands):
                raise FluxReinjectionError("Dialogue Flux tronqué.")
            current = commands[position]
            expected_code = 101 if offset == 0 else 401
            params = current.ivars.get("@parameters") if isinstance(current, RubyObject) else None
            if (
                not isinstance(current, RubyObject)
                or current.ivars.get("@code") != expected_code
                or not isinstance(params, list)
                or not params
                or not isinstance(params[0], RubyString)
            ):
                raise FluxReinjectionError("Lignes du dialogue Flux incohérentes.")
            strings.append(params[0])
        return _Mutation(item, tuple(strings), tuple(strings))
    if kind == "choice":
        params = command.ivars.get("@parameters")
        if (
            command.ivars.get("@code") != 102
            or not isinstance(params, list)
            or not params
            or not isinstance(params[0], list)
            or not isinstance(detail, int)
            or not 0 <= detail < len(params[0])
            or not isinstance(params[0][detail], RubyString)
        ):
            raise FluxReinjectionError("Choix Flux incohérent.")
        value = params[0][detail]
        return _Mutation(item, (value,), (value,))
    raise FluxReinjectionError("Type de commande Flux non pris en charge.")


def _locate_mutation(root, item: FluxImportPlanItem) -> _Mutation:
    if item.source_kind == "messages_game":
        return _message_game_mutation(root, item)
    if item.source_kind in {"map_events", "common_events"}:
        return _event_mutation(root, item)
    if item.source_kind == "messages":
        value = _generic_value(root, item.structural_path)
        if not isinstance(value, RubyString):
            raise FluxReinjectionError("Occurrence messages.dat Flux introuvable.")
        return _Mutation(item, (value,), (value,))
    raise FluxReinjectionError(f"Source Flux non réinjectable : {item.source_kind}")


def _validate_mutation(mutation: _Mutation) -> None:
    item = mutation.item
    if _source_hash(mutation.source_strings) != item.source_sha256:
        raise FluxReinjectionError(f"Texte source Flux différent pour {item.id_stable[:12]}.")
    if len(mutation.target_strings) != len(item.replacement_parts):
        raise FluxReinjectionError("Nombre de fragments Flux différent du plan.")
    if item.source_kind == "messages_game":
        current = hashlib.sha256(mutation.target_strings[0].data).hexdigest()
        if current != item.current_value_sha256:
            raise FluxReinjectionError("Valeur messages_game différente de l'extraction de contrôle.")


def _tree_files(root: Path) -> dict[str, tuple[Path, str]]:
    result: dict[str, tuple[Path, str]] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in os.scandir(directory):
            path = Path(entry.path)
            if entry.is_symlink() or _is_redirected(path):
                raise FluxReinjectionError("Lien ou jonction interdit dans l'arbre Flux temporaire.")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                relative = path.relative_to(root).as_posix()
                key = relative.casefold()
                if key in result:
                    raise FluxReinjectionError("Collision de fichiers dans l'arbre Flux temporaire.")
                result[key] = (path, _stable_sha256(path))
            else:
                raise FluxReinjectionError("Type de fichier Flux temporaire interdit.")
    return result


def apply_flux_plan_to_tree(plan: FluxImportPlan, extracted_root: Path) -> tuple[str, ...]:
    """Applique le plan seulement dans un dossier temporaire déjà extrait."""
    root = extracted_root.expanduser()
    if not root.is_dir() or _is_redirected(root) or not plan.applicable_items:
        raise FluxReinjectionError("Dossier temporaire ou plan Flux invalide.")
    root = root.resolve()
    if _is_same_or_within(root, _resolved_game_root(plan)):
        raise FluxReinjectionError("La réinjection directe dans le jeu original est interdite.")
    _tree_files(root)
    grouped: dict[str, list[FluxImportPlanItem]] = {}
    for item in plan.applicable_items:
        grouped.setdefault(item.internal_path, []).append(item)

    changed: list[str] = []
    for internal_path in sorted(grouped, key=str.casefold):
        path = root.joinpath(*internal_path.split("/"))
        try:
            path.resolve().relative_to(root)
        except (OSError, ValueError) as exc:
            raise FluxReinjectionError("Cible Flux hors du dossier temporaire.") from exc
        if not path.is_file() or _is_redirected(path) or _is_redirected(path.parent):
            raise FluxReinjectionError("Fichier Flux temporaire absent ou redirigé.")
        try:
            marshalled = load(path)
        except Exception as exc:
            raise FluxReinjectionError(f"Fichier Marshal Flux illisible : {internal_path}") from exc

        mutations = tuple(_locate_mutation(marshalled, item) for item in grouped[internal_path])
        target_ids = [id(value) for mutation in mutations for value in mutation.target_strings]
        if len(target_ids) != len(set(target_ids)):
            raise FluxReinjectionError("Deux occurrences Flux ciblent le même objet Ruby.")
        for mutation in mutations:
            _validate_mutation(mutation)
        for mutation in mutations:
            for value, replacement in zip(mutation.target_strings, mutation.item.replacement_parts):
                value.data = replacement

        payload = dumps(marshalled)
        atomic_write_bytes(path, payload, validator=load)
        reloaded = load(path)
        for item in grouped[internal_path]:
            verified = _locate_mutation(reloaded, item)
            actual = tuple(value.data for value in verified.target_strings)
            if actual != item.replacement_parts:
                raise FluxReinjectionError("Relecture de la réinjection Flux différente du plan.")
        changed.append(internal_path)
    return tuple(changed)


def _run_7zip_create(seven_zip: Path, tree_root: Path, output_path: Path) -> None:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.run(
            [
                str(seven_zip),
                "a",
                "-t7z",
                "-mx=9",
                "-mmt=off",
                str(output_path),
                "*",
            ],
            cwd=str(tree_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FluxReinjectionError("7-Zip n'a pas pu créer le FPK candidat.") from exc
    if process.returncode != 0 or not output_path.is_file():
        details = ((process.stdout or "") + "\n" + (process.stderr or ""))[-4000:]
        raise FluxReinjectionError("7-Zip refuse le FPK candidat :\n" + details)


def build_flux_candidate_archive(
    plan: FluxImportPlan,
    destination: Path,
    *,
    archive_reader: FluxArchiveReader | None = None,
    seven_zip: Path | None = None,
    before_commit: Callable[[Path], None] | None = None,
) -> FluxCandidateResult:
    """Crée un candidat séparé, le réextrait et ne le publie qu'après validation."""
    target_input = destination.expanduser()
    parent = target_input.parent
    if not parent.is_dir() or _is_redirected(parent) or _is_redirected(target_input):
        raise FluxReinjectionError("Dossier de sortie Flux absent ou redirigé.")
    target = target_input.resolve()
    if target.exists():
        raise FluxReinjectionError("Le FPK candidat existe déjà.")
    if _is_same_or_within(target, _resolved_game_root(plan)):
        raise FluxReinjectionError("Le FPK candidat ne peut pas être créé dans le jeu original.")

    if _stable_sha256(plan.fpk_path) != plan.source_fpk_sha256:
        raise FluxReinjectionError("Le FPK source a changé depuis le plan d'import.")
    if _stable_sha256(plan.csv_path) != plan.source_csv_sha256:
        raise FluxReinjectionError("Le CSV a changé depuis le plan d'import.")
    reader = archive_reader or FluxArchiveReader(seven_zip)
    tool = Path(seven_zip) if seven_zip else reader.seven_zip or find_7zip()
    if tool is None or not tool.is_file() or _is_redirected(tool):
        raise FluxReinjectionError("7-Zip fiable introuvable pour le candidat Flux.")

    source_inventory = reader.inspect(plan.fpk_path)
    if not source_inventory.safe:
        raise FluxReinjectionError("Inventaire du FPK source non sûr.")

    temporary_archive: Path | None = None
    committed = False
    try:
        with tempfile.TemporaryDirectory(prefix="pft_flux_reinject_") as work_text:
            work = Path(work_text).resolve()
            extracted = work / "source"
            verification = work / "verification"
            extracted.mkdir()
            verification.mkdir()
            reader.extract_to(plan.fpk_path, extracted, source_inventory)
            before_files = _tree_files(extracted)
            changed_members = apply_flux_plan_to_tree(plan, extracted)
            after_files = _tree_files(extracted)
            if set(before_files) != set(after_files):
                raise FluxReinjectionError("L'inventaire temporaire a changé pendant la réinjection.")
            changed_keys = {path.casefold() for path in changed_members}
            actual_changed = {
                key for key in before_files
                if before_files[key][1] != after_files[key][1]
            }
            if actual_changed != changed_keys:
                raise FluxReinjectionError("Les fichiers modifiés diffèrent exactement du plan Flux.")

            with tempfile.NamedTemporaryFile(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=parent,
                delete=False,
            ) as handle:
                temporary_archive = Path(handle.name)
            temporary_archive.unlink()
            _run_7zip_create(tool, extracted, temporary_archive)
            candidate_inventory = reader.inspect(temporary_archive)
            if not candidate_inventory.safe:
                raise FluxReinjectionError("Inventaire du FPK candidat non sûr.")
            if source_inventory.member_paths != candidate_inventory.member_paths:
                raise FluxReinjectionError("L'inventaire du FPK candidat diffère de l'original.")

            reader.extract_to(temporary_archive, verification, candidate_inventory)
            verified_files = _tree_files(verification)
            if set(before_files) != set(verified_files):
                raise FluxReinjectionError("La réextraction du candidat perd des fichiers.")
            for key, (_path, original_hash) in before_files.items():
                candidate_hash = verified_files[key][1]
                expected_hash = after_files[key][1] if key in changed_keys else original_hash
                if candidate_hash != expected_hash:
                    raise FluxReinjectionError("Un membre du candidat diffère de l'arbre validé.")

            for internal_path, items in {
                path: [item for item in plan.applicable_items if item.internal_path == path]
                for path in changed_members
            }.items():
                reloaded = load(verification.joinpath(*internal_path.split("/")))
                for item in items:
                    mutation = _locate_mutation(reloaded, item)
                    if tuple(value.data for value in mutation.target_strings) != item.replacement_parts:
                        raise FluxReinjectionError("Le FPK réextrait ne contient pas le remplacement prévu.")

            if _stable_sha256(plan.fpk_path) != plan.source_fpk_sha256:
                raise FluxReinjectionError("Le FPK original a changé pendant la construction du candidat.")
            if _stable_sha256(plan.csv_path) != plan.source_csv_sha256:
                raise FluxReinjectionError("Le CSV a changé pendant la construction du candidat.")
            if before_commit is not None:
                before_commit(temporary_archive)
            candidate_hash = _stable_sha256(temporary_archive)
            try:
                os.link(temporary_archive, target)
            except FileExistsError as exc:
                raise FluxReinjectionError(
                    "Un fichier candidat est apparu avant la validation finale."
                ) from exc
            except OSError as exc:
                raise FluxReinjectionError(
                    "Publication atomique sans écrasement du candidat impossible."
                ) from exc
            committed = True
            temporary_archive.unlink()
            temporary_archive = None

        return FluxCandidateResult(
            candidate_path=target,
            plan_fingerprint=plan.fingerprint,
            source_fpk_sha256=plan.source_fpk_sha256,
            candidate_fpk_sha256=candidate_hash,
            changed_members=tuple(sorted(changed_members, key=str.casefold)),
            verified_members=len(source_inventory.file_entries),
        )
    except (FluxReinjectionError, FluxArchiveError):
        if committed:
            target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if committed:
            target.unlink(missing_ok=True)
        raise FluxReinjectionError("Construction du candidat Flux annulée et restaurée.") from exc
    finally:
        if temporary_archive is not None:
            temporary_archive.unlink(missing_ok=True)


def _copy_regular_file(source_file: str, target_file: str):
    source = Path(source_file)
    if _is_redirected(source):
        raise FluxReinjectionError("Lien ou jonction apparu pendant la copie Flux.")
    return shutil.copy2(source_file, target_file)


def create_flux_working_copy(plan: FluxImportPlan, destination: Path) -> Path:
    """Crée une copie complète prouvée identique, sans toucher au jeu source."""
    source = _resolved_game_root(plan)
    target = destination.expanduser().resolve()
    if target.exists() or _is_redirected(target.parent):
        raise FluxReinjectionError("La destination de copie Flux existe ou est redirigée.")
    try:
        target.relative_to(source)
    except ValueError:
        pass
    else:
        raise FluxReinjectionError("La copie de travail ne peut pas être créée dans l'original.")
    source_before = snapshot_tree(source)

    def reject_links(directory: str, names: list[str]) -> set[str]:
        for name in names:
            if _is_redirected(Path(directory) / name):
                raise FluxReinjectionError("Lien ou jonction interdit dans le jeu Flux source.")
        return set()

    try:
        shutil.copytree(
            source,
            target,
            symlinks=True,
            ignore=reject_links,
            copy_function=_copy_regular_file,
        )
        copied = snapshot_tree(target)
        source_after = snapshot_tree(source)
        if not compare_snapshots(source_before, source_after).passed:
            raise FluxReinjectionError("Le jeu original a changé pendant sa copie.")
        if not compare_snapshots(source_before, copied).passed:
            raise FluxReinjectionError("La copie de travail Flux n'est pas identique à l'original.")
    except Exception:
        if target.is_dir():
            try:
                atomic_write_text(
                    target / "COPIE_FLUX_INCOMPLETE.txt",
                    "Cette copie est incomplète et ne doit pas être utilisée.\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
        raise
    return target


def validate_candidate_on_working_copy(
    plan: FluxImportPlan,
    candidate: FluxCandidateResult,
    working_copy: Path,
    backup_path: Path,
    *,
    archive_reader: FluxArchiveReader | None = None,
    after_install: Callable[[Path], None] | None = None,
) -> FluxWorkingCopyResult:
    """Installe brièvement le candidat dans la copie puis restaure exactement celle-ci."""
    work = working_copy.expanduser().resolve()
    backup = backup_path.expanduser().resolve()
    candidate_path = candidate.candidate_path.resolve()
    source = _resolved_game_root(plan)
    if not work.is_dir() or _is_redirected(work) or _is_redirected(backup.parent):
        raise FluxReinjectionError("Copie de travail ou dossier de sauvegarde non sûr.")
    if backup.exists():
        raise FluxReinjectionError("La sauvegarde de test Flux existe déjà.")
    for protected_root in (source, work):
        try:
            backup.relative_to(protected_root)
        except ValueError:
            pass
        else:
            raise FluxReinjectionError("La sauvegarde doit rester hors du jeu et de sa copie.")
    try:
        work.relative_to(source)
    except ValueError:
        pass
    else:
        raise FluxReinjectionError("La copie de travail se trouve dans le jeu original.")
    if candidate.plan_fingerprint != plan.fingerprint:
        raise FluxReinjectionError("Le candidat ne correspond pas au plan Flux courant.")
    if _stable_sha256(candidate_path) != candidate.candidate_fpk_sha256:
        raise FluxReinjectionError("Le candidat Flux a changé avant son test.")

    try:
        relative_fpk = plan.fpk_path.expanduser().resolve().relative_to(source)
    except ValueError as exc:
        raise FluxReinjectionError("Le FPK du plan se trouve hors du jeu original.") from exc
    working_fpk = work / relative_fpk
    if not working_fpk.is_file() or _is_redirected(working_fpk):
        raise FluxReinjectionError("Le FPK de la copie de travail est absent ou redirigé.")
    source_before = snapshot_tree(source)
    working_before = snapshot_tree(work)
    if not compare_snapshots(source_before, working_before).passed:
        raise FluxReinjectionError("La copie de travail n'est plus identique à l'original.")
    original_working_hash = _stable_sha256(working_fpk)
    if original_working_hash != plan.source_fpk_sha256:
        raise FluxReinjectionError("Le FPK de travail ne correspond pas au plan.")

    reader = archive_reader or FluxArchiveReader()
    source_inventory = reader.inspect(plan.fpk_path)
    candidate_inventory = reader.inspect(candidate_path)
    if (
        not source_inventory.safe
        or not candidate_inventory.safe
        or source_inventory.member_paths != candidate_inventory.member_paths
    ):
        raise FluxReinjectionError("Le candidat ne conserve pas l'inventaire Flux source.")

    backup.parent.mkdir(parents=True, exist_ok=True)
    atomic_copy_file(
        working_fpk,
        backup,
        validator=lambda path: reader.inspect(path),
        replace_existing=False,
        expected_sha256=plan.source_fpk_sha256,
    )
    installed = False
    validation_error: Exception | None = None
    installed_hash = ""
    try:
        atomic_copy_file(
            candidate_path,
            working_fpk,
            validator=lambda path: reader.inspect(path),
            expected_sha256=candidate.candidate_fpk_sha256,
        )
        installed = True
        installed_hash = _stable_sha256(working_fpk)
        if installed_hash != candidate.candidate_fpk_sha256:
            raise FluxReinjectionError("Le FPK installé diffère du candidat validé.")
        working_installed = snapshot_tree(work)
        comparison = compare_snapshots(
            source_before,
            working_installed,
            allowed_changed={relative_fpk.as_posix()},
        )
        if not comparison.passed:
            raise FluxReinjectionError("La copie de travail contient une modification hors plan.")
        if _stable_sha256(plan.fpk_path) != plan.source_fpk_sha256:
            raise FluxReinjectionError("Le FPK original a changé pendant le test de copie.")
        if after_install is not None:
            after_install(working_fpk)
    except Exception as exc:
        validation_error = exc
    finally:
        if installed:
            try:
                atomic_copy_file(
                    backup,
                    working_fpk,
                    validator=lambda path: reader.inspect(path),
                    expected_sha256=plan.source_fpk_sha256,
                )
            except Exception as rollback_exc:
                try:
                    atomic_write_text(
                        work / "COPIE_FLUX_INCOMPLETE.txt",
                        "Le rollback automatique a échoué. Cette copie ne doit pas être utilisée.\n",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                raise FluxReinjectionError(
                    "Échec critique du rollback de la copie Flux."
                ) from rollback_exc

    restored_hash = _stable_sha256(working_fpk)
    restored_snapshot = snapshot_tree(work)
    source_after = snapshot_tree(source)
    rollback_valid = (
        restored_hash == original_working_hash
        and compare_snapshots(source_before, restored_snapshot).passed
        and compare_snapshots(source_before, source_after).passed
    )
    if not rollback_valid:
        raise FluxReinjectionError("Le rollback Flux n'a pas restauré exactement la copie.")
    if validation_error is not None:
        if not installed:
            raise FluxReinjectionError(
                "Installation du candidat Flux annulée avant modification ; copie intacte."
            ) from validation_error
        raise FluxReinjectionError(
            "Validation sur copie de travail annulée ; rollback exact réussi."
        ) from validation_error
    return FluxWorkingCopyResult(
        working_copy=work,
        backup_path=backup,
        candidate_sha256=candidate.candidate_fpk_sha256,
        installed_sha256=installed_hash,
        restored_sha256=restored_hash,
        source_files_verified=source_before.file_count,
        rollback_verified=True,
    )
