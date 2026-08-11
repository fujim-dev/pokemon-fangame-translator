# SPDX-License-Identifier: GPL-3.0-or-later
"""Cycle de vie vérifiable d'un projet de traduction Pokémon Essentials."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from extraction_project import (
    BASELINE_CSV_NAME,
    EXTRACTION_REPORT_NAME,
    PROJECT_CSV_NAME,
)
from project_identity import (
    PROJECT_METADATA_NAME,
    ProjectIdentityError,
    read_project_identity,
)
from safe_io import (
    StableFileState,
    atomic_write_bundle,
    read_stable_file,
)


TRANSLATION_STATE_NAME = "ETAT_PROJET_TRADUCTION.json"
RESUME_STATE_NAME = "etat_traduction.json"
LOCK_NAME = ".pft_traduction.lock"
ESSENTIALS_ADAPTER_ID = "pokemon_essentials"
TRANSLATION_STATE_FORMAT = "pft_translation_project_v1"
RESUME_STATE_FORMAT = "pft_translation_resume_v2"

IMMUTABLE_ROW_FIELDS = (
    "id_stable",
    "type",
    "fichier",
    "carte_id",
    "carte_nom",
    "evenement_id",
    "evenement_nom",
    "page",
    "commande",
    "sous_index",
    "texte_source",
    "codes_proteges",
    "adaptateur",
    "source_sha256",
    "source_manifest_sha256",
    "rpg_command_code",
    "rpg_command_indent",
    "rpg_parameter_index",
    "rpg_continuation_end",
    "rpg_dialogue_segments",
    "rpg_common_event_array_index",
    "rpg_common_event_trigger",
    "rpg_common_event_switch_id",
    "rpg_common_event_sha256",
    "rpg_choice_branch_command",
    "rpg_choice_branch_parameter_index",
    "profil_essentials",
    "version_essentials_declaree",
    "methode_version_essentials",
    "pbs_encoding",
    "pbs_bom",
    "pbs_newline",
    "pbs_field_index",
    "pbs_value_sha256",
    "pbs_line_number",
    "pbs_field_count",
    "pbs_point_structure",
)
REQUIRED_ROW_FIELDS = {
    "id_stable",
    "type",
    "fichier",
    "texte_source",
    "traduction_fr",
    "statut",
    "adaptateur",
    "source_sha256",
    "source_manifest_sha256",
}


class TranslationProjectError(RuntimeError):
    """Une opération de Studio ne peut plus prouver la cohérence du projet."""


class TranslationProjectInUseError(TranslationProjectError):
    """Une autre session détient déjà le verrou du projet."""


def _is_redirected(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x0400)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TranslationProjectError(
            "Impossible de vérifier si un artefact du projet est redirigé."
        ) from exc


def _signature(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


class ProjectSessionLock:
    """Verrou interprocessus libéré automatiquement même après un crash."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir.expanduser().resolve()
        self.path = self.project_dir / LOCK_NAME
        self.handle = None
        self.session_id = uuid.uuid4().hex
        self._locked = False

    def acquire(self) -> None:
        if self._locked:
            return
        if _is_redirected(self.project_dir):
            raise TranslationProjectError(
                "Le dossier du projet est un lien ou une jonction ; le Studio reste bloqué."
            )
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if _is_redirected(self.path):
            raise TranslationProjectError("Le verrou du projet est redirigé.")
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise TranslationProjectInUseError(
                "Ce projet est déjà ouvert dans une autre session du Studio. "
                "Fermez l'autre fenêtre avant de continuer."
            ) from exc

        self.handle = handle
        self._locked = True
        payload = json.dumps(
            {
                "format": "pft_translation_lock_v1",
                "session_id": self.session_id,
                "pid": os.getpid(),
                "opened_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(0)
        except OSError as exc:
            self.close()
            raise TranslationProjectError(
                "Impossible d'initialiser le verrou du projet."
            ) from exc

    def assert_held(self) -> None:
        if not self._locked or self.handle is None:
            raise TranslationProjectError("La session du projet n'est plus verrouillée.")
        try:
            opened = os.fstat(self.handle.fileno())
            current = self.path.stat()
        except OSError as exc:
            raise TranslationProjectError(
                "Le verrou du projet a disparu ou a été remplacé."
            ) from exc
        if _signature(opened) != _signature(current) or _is_redirected(self.path):
            raise TranslationProjectError(
                "Le fichier de verrouillage du projet a été remplacé pendant la session."
            )

    def close(self) -> None:
        handle = self.handle
        self.handle = None
        if handle is None:
            self._locked = False
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._locked = False
            handle.close()


@dataclass(frozen=True)
class CsvStructure:
    fields: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    immutable_sha256: str


@dataclass(frozen=True)
class VerifiedProjectSnapshot:
    csv_state: StableFileState
    identity_state: StableFileState
    manifest_state: StableFileState
    baseline_state: StableFileState
    report_state: StableFileState
    translation_state: StableFileState | None
    resume_state: StableFileState | None
    source_manifest_sha256: str
    extraction_id: str
    immutable_rows_sha256: str
    revision: int
    resume_warning: str = ""

    def token(self) -> tuple[object, ...]:
        states = (
            self.csv_state,
            self.identity_state,
            self.manifest_state,
            self.baseline_state,
            self.report_state,
            self.translation_state,
            self.resume_state,
        )
        return tuple(
            None if state is None else (state.sha256, state.signature)
            for state in states
        ) + (
            self.source_manifest_sha256,
            self.extraction_id,
            self.immutable_rows_sha256,
            self.revision,
        )

    def logical_token(self) -> tuple[object, ...]:
        """État logique, sans identités remplacées par un rollback atomique."""
        states = (
            self.csv_state,
            self.identity_state,
            self.manifest_state,
            self.baseline_state,
            self.report_state,
            self.translation_state,
            self.resume_state,
        )
        return tuple(None if state is None else state.sha256 for state in states) + (
            self.source_manifest_sha256,
            self.extraction_id,
            self.immutable_rows_sha256,
            self.revision,
        )

    def provenance_token(self) -> str:
        return hashlib.sha256(repr(self.token()).encode("utf-8")).hexdigest()


def _json_object(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except Exception as exc:
        raise TranslationProjectError(f"{label} est illisible ou invalide.") from exc
    if not isinstance(value, dict):
        raise TranslationProjectError(f"{label} ne contient pas un objet JSON reconnu.")
    return value


def _safe_artifact_name(value: object, fallback: str, label: str) -> str:
    name = str(value or fallback)
    if not name or Path(name).name != name or name in {".", ".."}:
        raise TranslationProjectError(f"Le nom de {label} est invalide dans le manifeste.")
    return name


def inspect_csv_structure(payload: bytes) -> CsvStructure:
    try:
        text = payload.decode("utf-8-sig")
        with io.StringIO(text, newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";", strict=True)
            fields = tuple(reader.fieldnames or ())
            missing = sorted(REQUIRED_ROW_FIELDS.difference(fields))
            if missing:
                raise TranslationProjectError(
                    "CSV de projet incomplet, colonnes de provenance manquantes : "
                    + ", ".join(missing)
                )
            rows: list[dict[str, str]] = []
            identifiers: set[str] = set()
            immutable: list[dict[str, str]] = []
            for line_number, raw in enumerate(reader, start=2):
                if None in raw:
                    raise TranslationProjectError(
                        f"CSV de projet mal formé à la ligne {line_number}."
                    )
                row = {field: str(raw.get(field) or "") for field in fields}
                identifier = row.get("id_stable", "")
                if not identifier or identifier in identifiers:
                    raise TranslationProjectError(
                        "Les identifiants d'occurrence du CSV sont absents ou dupliqués."
                    )
                identifiers.add(identifier)
                rows.append(row)
                immutable.append(
                    {field: row.get(field, "") for field in IMMUTABLE_ROW_FIELDS}
                )
    except TranslationProjectError:
        raise
    except (UnicodeError, csv.Error) as exc:
        raise TranslationProjectError("Le CSV du projet est illisible.") from exc

    serialized = json.dumps(
        immutable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CsvStructure(
        fields=fields,
        rows=tuple(rows),
        immutable_sha256=hashlib.sha256(serialized).hexdigest(),
    )


def build_translation_state_bytes(
    *,
    revision: int,
    csv_name: str,
    csv_sha256: str,
    identity_sha256: str,
    manifest_sha256: str,
    baseline_sha256: str,
    report_sha256: str,
    source_manifest_sha256: str,
    extraction_id: str,
    immutable_rows_sha256: str,
) -> bytes:
    payload = {
        "format": TRANSLATION_STATE_FORMAT,
        "revision": int(revision),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "csv_name": csv_name,
        "csv_sha256": csv_sha256,
        "identity_sha256": identity_sha256,
        "manifest_sha256": manifest_sha256,
        "baseline_sha256": baseline_sha256,
        "report_sha256": report_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "extraction_id": extraction_id,
        "immutable_rows_sha256": immutable_rows_sha256,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def build_resume_state_bytes(
    data: Mapping[str, object],
    *,
    csv_name: str,
    csv_sha256: str,
    source_manifest_sha256: str,
    extraction_id: str,
) -> bytes:
    payload = dict(data)
    payload.update(
        {
            "format": RESUME_STATE_FORMAT,
            "csv_name": csv_name,
            "csv_sha256": csv_sha256,
            "source_manifest_sha256": source_manifest_sha256,
            "extraction_id": extraction_id,
        }
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


class TranslationProjectSession:
    """Session exclusive ; les projets anciens restent consultables en lecture seule."""

    def __init__(
        self,
        csv_path: Path,
        *,
        game_root: Path | None,
        expected_adapter_id: str,
    ):
        requested = csv_path.expanduser()
        if _is_redirected(requested) or _is_redirected(requested.parent):
            raise TranslationProjectError(
                "Le CSV ou son dossier est redirigé ; le Studio refuse de l'ouvrir."
            )
        self.csv_path = requested.resolve()
        if not self.csv_path.is_file():
            raise TranslationProjectError("Le CSV de traduction est introuvable.")
        self.project_dir = self.csv_path.parent
        self.game_root = game_root.expanduser().resolve() if game_root else None
        self.expected_adapter_id = expected_adapter_id
        self.lock = ProjectSessionLock(self.project_dir)
        self.lock.acquire()
        self._mutex = threading.RLock()
        self.writable = False
        self.read_only_reason = ""
        self.snapshot: VerifiedProjectSnapshot | None = None
        self.identity_path = self.project_dir / PROJECT_METADATA_NAME
        self.manifest_path: Path | None = None
        self.baseline_path: Path | None = None
        self.report_path: Path | None = None
        try:
            _csv_payload, self.legacy_csv_state = read_stable_file(self.csv_path)
        except OSError as exc:
            self.lock.close()
            raise TranslationProjectError(
                "Le CSV de traduction ne peut pas être lu de manière stable."
            ) from exc

        if expected_adapter_id != ESSENTIALS_ADAPTER_ID:
            self.read_only_reason = (
                "Ce service de provenance est réservé aux projets Pokémon Essentials."
            )
            return
        if self.game_root is None:
            self.read_only_reason = (
                "Le fangame source n'est pas connu. Rouvrez ce projet depuis l'analyse du jeu."
            )
            return
        try:
            self.snapshot = self._capture_verified()
        except (TranslationProjectError, ProjectIdentityError) as exc:
            self.read_only_reason = (
                f"Provenance non démontrée : {exc} "
                "Le projet est consultable, mais une nouvelle extraction est requise avant "
                "toute sauvegarde, reprise ou reconstruction."
            )
            return
        self.writable = True

    @property
    def translation_state_path(self) -> Path:
        return self.project_dir / TRANSLATION_STATE_NAME

    @property
    def resume_state_path(self) -> Path:
        return self.project_dir / RESUME_STATE_NAME

    def _read_required(self, path: Path, label: str) -> tuple[bytes, StableFileState]:
        if _is_redirected(path):
            raise TranslationProjectError(f"{label} est redirigé.")
        try:
            return read_stable_file(path)
        except OSError as exc:
            raise TranslationProjectError(f"{label} est absent ou illisible.") from exc

    def _read_optional(self, path: Path, label: str) -> tuple[bytes | None, StableFileState | None]:
        if not path.exists():
            if _is_redirected(path):
                raise TranslationProjectError(f"{label} est redirigé.")
            return None, None
        payload, state = self._read_required(path, label)
        return payload, state

    def _assert_still_same(self, path: Path, expected: StableFileState | None, label: str) -> None:
        if expected is None:
            if path.exists() or _is_redirected(path):
                raise TranslationProjectError(f"{label} est apparu pendant sa validation.")
            return
        _payload, current = self._read_required(path, label)
        if current != expected:
            raise TranslationProjectError(f"{label} a été remplacé pendant sa validation.")

    def _capture_verified(self) -> VerifiedProjectSnapshot:
        assert self.game_root is not None
        self.lock.assert_held()
        try:
            identity = read_project_identity(
                self.csv_path,
                self.game_root,
                expected_adapter_id=ESSENTIALS_ADAPTER_ID,
                require_extraction_provenance=True,
            )
        except ProjectIdentityError as exc:
            raise TranslationProjectError(str(exc)) from exc

        identity_path = self.identity_path
        identity_payload, identity_state = self._read_required(
            identity_path, "L'identité du projet"
        )
        if identity_state.sha256 != identity.sha256:
            raise TranslationProjectError(
                "L'identité du projet a changé pendant son contrôle."
            )
        identity_data = _json_object(identity_payload, "L'identité du projet")
        manifest_name = _safe_artifact_name(
            identity_data.get("extraction_manifest_name"),
            "",
            "manifeste d'extraction",
        )
        manifest_path = self.project_dir / manifest_name
        self.manifest_path = manifest_path
        manifest_payload, manifest_state = self._read_required(
            manifest_path, "Le manifeste d'extraction"
        )
        manifest = _json_object(manifest_payload, "Le manifeste d'extraction")

        project_csv_name = _safe_artifact_name(
            manifest.get("project_csv_name"), PROJECT_CSV_NAME, "CSV principal"
        )
        if self.csv_path.name.casefold() != project_csv_name.casefold():
            raise TranslationProjectError(
                "Ce CSV n'est pas le CSV principal rattaché au manifeste d'extraction."
            )
        baseline_name = _safe_artifact_name(
            manifest.get("baseline_csv_name"), BASELINE_CSV_NAME, "CSV d'extraction témoin"
        )
        report_name = _safe_artifact_name(
            manifest.get("report_name"), EXTRACTION_REPORT_NAME, "rapport d'extraction"
        )
        baseline_path = self.project_dir / baseline_name
        report_path = self.project_dir / report_name
        self.baseline_path = baseline_path
        self.report_path = report_path
        baseline_payload, baseline_state = self._read_required(
            baseline_path, "Le CSV d'extraction témoin"
        )
        report_payload, report_state = self._read_required(
            report_path, "Le rapport d'extraction"
        )
        csv_payload, csv_state = self._read_required(self.csv_path, "Le CSV principal")

        if baseline_state.sha256 != identity.extracted_csv_sha256:
            raise TranslationProjectError(
                "Le CSV d'extraction témoin ne correspond plus au manifeste."
            )
        report_sha256 = str(manifest.get("report_sha256") or "").casefold()
        if len(report_sha256) != 64 or report_state.sha256 != report_sha256:
            raise TranslationProjectError(
                "Le rapport d'extraction ne correspond plus au manifeste."
            )
        del report_payload

        baseline_structure = inspect_csv_structure(baseline_payload)
        current_structure = inspect_csv_structure(csv_payload)
        if current_structure.immutable_sha256 != baseline_structure.immutable_sha256:
            raise TranslationProjectError(
                "Les occurrences ou champs sources du CSV ont changé depuis l'extraction."
            )
        try:
            expected_rows = int(manifest.get("row_count", -1))
        except (TypeError, ValueError) as exc:
            raise TranslationProjectError(
                "Le nombre d'occurrences du manifeste est invalide."
            ) from exc
        if expected_rows != len(baseline_structure.rows) or expected_rows != len(current_structure.rows):
            raise TranslationProjectError(
                "Le nombre d'occurrences du CSV ne correspond plus au manifeste."
            )
        raw_manifest_sources = manifest.get("sources")
        if not isinstance(raw_manifest_sources, list):
            raise TranslationProjectError(
                "L'inventaire des sources du manifeste est absent ou invalide."
            )
        manifest_sources = {
            str(source.get("relative_path") or ""): str(source.get("sha256") or "").casefold()
            for source in raw_manifest_sources
            if isinstance(source, dict)
        }
        for row in current_structure.rows:
            if row.get("adaptateur") != ESSENTIALS_ADAPTER_ID:
                raise TranslationProjectError(
                    "Une occurrence du CSV annonce un autre adaptateur."
                )
            if row.get("source_manifest_sha256", "").casefold() != identity.source_manifest_sha256:
                raise TranslationProjectError(
                    "Une occurrence du CSV ne correspond pas à l'inventaire source."
                )
            relative = row.get("fichier", "")
            if manifest_sources.get(relative) != row.get("source_sha256", "").casefold():
                raise TranslationProjectError(
                    f"L'empreinte de la source {relative or 'inconnue'} est incohérente."
                )

        state_payload, state_file = self._read_optional(
            self.translation_state_path, "L'état de traduction"
        )
        revision = 0
        if state_payload is None:
            if csv_state.sha256 != identity.extracted_csv_sha256:
                raise TranslationProjectError(
                    "Le CSV a déjà été modifié mais ne possède aucun état de traduction vérifiable."
                )
        else:
            state = _json_object(state_payload, "L'état de traduction")
            expected_state = {
                "format": TRANSLATION_STATE_FORMAT,
                "csv_name": self.csv_path.name,
                "csv_sha256": csv_state.sha256,
                "identity_sha256": identity_state.sha256,
                "manifest_sha256": manifest_state.sha256,
                "baseline_sha256": baseline_state.sha256,
                "report_sha256": report_state.sha256,
                "source_manifest_sha256": identity.source_manifest_sha256,
                "extraction_id": identity.extraction_id,
                "immutable_rows_sha256": baseline_structure.immutable_sha256,
            }
            for key, expected in expected_state.items():
                if str(state.get(key) or "") != expected:
                    raise TranslationProjectError(
                        f"L'état de traduction est incohérent ({key})."
                    )
            try:
                revision = int(state.get("revision", 0))
            except (TypeError, ValueError) as exc:
                raise TranslationProjectError(
                    "La révision de l'état de traduction est invalide."
                ) from exc
            if revision < 1:
                raise TranslationProjectError(
                    "La révision de l'état de traduction est invalide."
                )

        resume_payload, resume_file = self._read_optional(
            self.resume_state_path, "L'état de reprise"
        )
        resume_warning = ""
        if resume_payload is not None:
            resume = _json_object(resume_payload, "L'état de reprise")
            if resume.get("format") == RESUME_STATE_FORMAT:
                for key, expected in (
                    ("csv_name", self.csv_path.name),
                    ("csv_sha256", csv_state.sha256),
                    ("source_manifest_sha256", identity.source_manifest_sha256),
                    ("extraction_id", identity.extraction_id),
                ):
                    if str(resume.get(key) or "") != expected:
                        raise TranslationProjectError(
                            f"L'état de reprise est incohérent ({key})."
                        )
                try:
                    totals = {
                        key: int(resume.get(key, 0))
                        for key in ("total", "completed", "remaining")
                    }
                except (TypeError, ValueError) as exc:
                    raise TranslationProjectError(
                        "Les compteurs de l'état de reprise sont invalides."
                    ) from exc
                if (
                    any(value < 0 for value in totals.values())
                    or totals["completed"] > totals["total"]
                    or totals["remaining"] > totals["total"]
                ):
                    raise TranslationProjectError(
                        "Les compteurs de l'état de reprise sont incohérents."
                    )
            elif bool(resume.get("active")):
                resume_warning = (
                    "L'ancien état de reprise n'est pas lié à la provenance actuelle ; "
                    "sa reprise est bloquée."
                )

        try:
            identity_again = read_project_identity(
                self.csv_path,
                self.game_root,
                expected_adapter_id=ESSENTIALS_ADAPTER_ID,
                require_extraction_provenance=True,
            )
        except ProjectIdentityError as exc:
            raise TranslationProjectError(str(exc)) from exc
        if identity_again != identity:
            raise TranslationProjectError(
                "L'identité ou le manifeste a changé pendant la validation du projet."
            )
        for path, expected, label in (
            (self.csv_path, csv_state, "Le CSV principal"),
            (identity_path, identity_state, "L'identité du projet"),
            (manifest_path, manifest_state, "Le manifeste d'extraction"),
            (baseline_path, baseline_state, "Le CSV d'extraction témoin"),
            (report_path, report_state, "Le rapport d'extraction"),
            (self.translation_state_path, state_file, "L'état de traduction"),
            (self.resume_state_path, resume_file, "L'état de reprise"),
        ):
            self._assert_still_same(path, expected, label)

        return VerifiedProjectSnapshot(
            csv_state=csv_state,
            identity_state=identity_state,
            manifest_state=manifest_state,
            baseline_state=baseline_state,
            report_state=report_state,
            translation_state=state_file,
            resume_state=resume_file,
            source_manifest_sha256=identity.source_manifest_sha256,
            extraction_id=identity.extraction_id,
            immutable_rows_sha256=baseline_structure.immutable_sha256,
            revision=revision,
            resume_warning=resume_warning,
        )

    def _require_writable(self) -> VerifiedProjectSnapshot:
        if not self.writable or self.snapshot is None:
            raise TranslationProjectError(
                self.read_only_reason
                or "La provenance du projet n'est pas vérifiable ; l'écriture reste bloquée."
            )
        return self.snapshot

    def check_current(self) -> None:
        with self._mutex:
            self.lock.assert_held()
            if not self.writable or self.snapshot is None:
                _payload, state = read_stable_file(self.csv_path)
                if state != self.legacy_csv_state:
                    raise TranslationProjectError(
                        "Le CSV consulté a été modifié ou remplacé pendant la session."
                    )
                return
            current = self._capture_verified()
            if current.token() != self.snapshot.token():
                raise TranslationProjectError(
                    "Le CSV ou un artefact de provenance a été modifié ou remplacé "
                    "pendant la session. Rechargez le projet après vérification."
                )

    def read_csv_payload(self) -> bytes:
        """Retourne les octets correspondant exactement à la photographie validée."""
        with self._mutex:
            self.check_current()
            payload, state = read_stable_file(self.csv_path)
            expected = (
                self.snapshot.csv_state
                if self.writable and self.snapshot is not None
                else self.legacy_csv_state
            )
            if state != expected:
                raise TranslationProjectError(
                    "Le CSV a été remplacé entre sa validation et son ouverture."
                )
            return payload

    def _state_bytes(
        self,
        snapshot: VerifiedProjectSnapshot,
        csv_sha256: str,
        immutable_sha256: str,
    ) -> bytes:
        return build_translation_state_bytes(
            revision=snapshot.revision + 1,
            csv_name=self.csv_path.name,
            csv_sha256=csv_sha256,
            identity_sha256=snapshot.identity_state.sha256,
            manifest_sha256=snapshot.manifest_state.sha256,
            baseline_sha256=snapshot.baseline_state.sha256,
            report_sha256=snapshot.report_state.sha256,
            source_manifest_sha256=snapshot.source_manifest_sha256,
            extraction_id=snapshot.extraction_id,
            immutable_rows_sha256=immutable_sha256,
        )

    def _resume_bytes(
        self,
        data: Mapping[str, object],
        snapshot: VerifiedProjectSnapshot,
        csv_sha256: str,
    ) -> bytes:
        return build_resume_state_bytes(
            data,
            csv_name=self.csv_path.name,
            csv_sha256=csv_sha256,
            source_manifest_sha256=snapshot.source_manifest_sha256,
            extraction_id=snapshot.extraction_id,
        )

    def _managed_related_path(self, path: Path, *, operation: str) -> Path:
        """Limite les artefacts liés aux dossiers privés du projet courant."""
        try:
            resolved = path.expanduser().resolve()
            relative = resolved.relative_to(self.project_dir)
        except (OSError, ValueError) as exc:
            raise TranslationProjectError(
                f"{operation} refusée : un artefact sort du dossier du projet."
            ) from exc
        if len(relative.parts) != 2 or relative.parts[0] not in {
            "Rapports",
            "Sauvegardes",
        }:
            raise TranslationProjectError(
                f"{operation} refusée : l'artefact n'appartient pas à Rapports ou Sauvegardes."
            )
        parent = self.project_dir / relative.parts[0]
        if _is_redirected(parent) or _is_redirected(resolved):
            raise TranslationProjectError(
                f"{operation} refusée : un artefact est redirigé."
            )
        return resolved

    def save(
        self,
        csv_payload: bytes,
        *,
        resume_state: Mapping[str, object] | None = None,
        synchronized_artifacts: Mapping[Path, bytes] | None = None,
        guarded_artifacts: Mapping[Path, StableFileState | None] | None = None,
    ) -> None:
        with self._mutex:
            snapshot = self._require_writable()
            self.lock.assert_held()
            current = self._capture_verified()
            if current.token() != snapshot.token():
                raise TranslationProjectError(
                    "Enregistrement refusé : le projet a changé depuis son ouverture."
                )
            candidate = inspect_csv_structure(csv_payload)
            if candidate.immutable_sha256 != snapshot.immutable_rows_sha256:
                raise TranslationProjectError(
                    "Enregistrement refusé : une occurrence ou une donnée source du CSV a changé."
                )
            csv_sha256 = hashlib.sha256(csv_payload).hexdigest()
            state_payload = self._state_bytes(
                snapshot,
                csv_sha256,
                candidate.immutable_sha256,
            )
            artifacts: dict[Path, bytes] = {
                self.csv_path: csv_payload,
                self.translation_state_path: state_payload,
            }
            expected_hashes: dict[Path, str | None] = {
                self.csv_path: snapshot.csv_state.sha256,
                self.translation_state_path: (
                    snapshot.translation_state.sha256
                    if snapshot.translation_state is not None
                    else None
                ),
            }
            expected_signatures = {
                self.csv_path: snapshot.csv_state.signature,
                self.translation_state_path: (
                    snapshot.translation_state.signature
                    if snapshot.translation_state is not None
                    else None
                ),
            }
            for requested_path, payload in (synchronized_artifacts or {}).items():
                destination = self._managed_related_path(
                    Path(requested_path), operation="Publication transactionnelle"
                )
                if destination in artifacts:
                    raise TranslationProjectError(
                        "Publication refusée : deux artefacts désignent la même destination."
                    )
                artifacts[destination] = bytes(payload)
                expected_hashes[destination] = None
                expected_signatures[destination] = None
            if resume_state is not None:
                resume_payload = self._resume_bytes(resume_state, snapshot, csv_sha256)
                artifacts[self.resume_state_path] = resume_payload
                expected_hashes[self.resume_state_path] = (
                    snapshot.resume_state.sha256 if snapshot.resume_state is not None else None
                )
                expected_signatures[self.resume_state_path] = (
                    snapshot.resume_state.signature if snapshot.resume_state is not None else None
                )
            elif snapshot.resume_state is not None:
                old_resume_payload, _old_resume_state = read_stable_file(self.resume_state_path)
                old_resume = _json_object(old_resume_payload, "L'état de reprise")
                if old_resume.get("format") == RESUME_STATE_FORMAT:
                    artifacts[self.resume_state_path] = self._resume_bytes(
                        old_resume, snapshot, csv_sha256
                    )
                    expected_hashes[self.resume_state_path] = snapshot.resume_state.sha256
                    expected_signatures[self.resume_state_path] = snapshot.resume_state.signature

            if (
                self.manifest_path is None
                or self.baseline_path is None
                or self.report_path is None
            ):
                raise TranslationProjectError(
                    "Les chemins des artefacts de provenance ne sont plus disponibles."
                )
            guarded = {
                self.identity_path: snapshot.identity_state,
                self.manifest_path: snapshot.manifest_state,
                self.baseline_path: snapshot.baseline_state,
                self.report_path: snapshot.report_state,
            }
            for requested_path, expected in (guarded_artifacts or {}).items():
                guarded_path = self._managed_related_path(
                    Path(requested_path), operation="Surveillance transactionnelle"
                )
                previous = guarded.get(guarded_path)
                if previous is not None and previous != expected:
                    raise TranslationProjectError(
                        "Publication refusée : deux contrôles contradictoires visent un artefact."
                    )
                guarded[guarded_path] = expected
            try:
                atomic_write_bundle(
                    artifacts,
                    expected_existing_sha256=expected_hashes,
                    expected_existing_signatures=expected_signatures,
                    guarded_existing=guarded,
                )
            except (OSError, ValueError) as exc:
                try:
                    recovered = self._capture_verified()
                except TranslationProjectError:
                    recovered = None
                if (
                    recovered is not None
                    and recovered.logical_token() == snapshot.logical_token()
                ):
                    # Le rollback remplace atomiquement les fichiers restaurés :
                    # leur identité change, mais leurs octets et tous les liens de
                    # provenance sont de nouveau exactement ceux de la session.
                    self.snapshot = recovered
                if "rollback incomplet" in str(exc).casefold():
                    raise TranslationProjectError(
                        "La sauvegarde a échoué et son rollback est incomplet. "
                        "N'utilisez plus ce projet avant d'avoir restauré les fichiers de "
                        "récupération conservés à côté des artefacts. "
                        f"Détails de récupération : {exc}"
                    ) from exc
                raise TranslationProjectError(
                    "La sauvegarde transactionnelle a été annulée ; l'état précédent a été conservé."
                ) from exc
            self.snapshot = self._capture_verified()
            self.legacy_csv_state = self.snapshot.csv_state

    def read_resume_state(self) -> dict[str, object]:
        with self._mutex:
            snapshot = self._require_writable()
            self.check_current()
            if snapshot.resume_warning:
                raise TranslationProjectError(snapshot.resume_warning)
            if snapshot.resume_state is None:
                return {}
            payload, _state = read_stable_file(self.resume_state_path)
            data = _json_object(payload, "L'état de reprise")
            if data.get("format") != RESUME_STATE_FORMAT:
                return {}
            return data

    def close(self) -> None:
        self.lock.close()

    def __enter__(self) -> "TranslationProjectSession":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def open_verified_project(
    csv_path: Path,
    *,
    game_root: Path,
    expected_adapter_id: str = ESSENTIALS_ADAPTER_ID,
) -> TranslationProjectSession:
    """Ouvre une garde stricte, notamment avant de construire une reconstruction."""
    session = TranslationProjectSession(
        csv_path,
        game_root=game_root,
        expected_adapter_id=expected_adapter_id,
    )
    if not session.writable:
        reason = session.read_only_reason
        session.close()
        raise TranslationProjectError(reason)
    session.check_current()
    return session
