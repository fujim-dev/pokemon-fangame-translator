# SPDX-License-Identifier: GPL-3.0-or-later
"""Exécute les sondes d'adaptateurs hors du processus principal, avec délai dur."""
from __future__ import annotations

import multiprocessing
import os
import signal
import threading
import time
from multiprocessing.reduction import ForkingPickler
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .base import DetectionResult, GameAdapter


DEFAULT_PROBE_TIMEOUT_SECONDS = 30.0
DEFAULT_PROBE_STARTUP_TIMEOUT_SECONDS = 10.0
DEFAULT_PROBE_POLL_SECONDS = 0.025
DEFAULT_PROBE_SHUTDOWN_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class ProbeFailure:
    adapter_id: str
    display_name: str
    error_type: str
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class ProbeBatchResult:
    results: tuple[DetectionResult, ...] = ()
    failures: tuple[ProbeFailure, ...] = ()
    cancelled: bool = False


def _probe_worker(adapters: tuple[GameAdapter, ...], root: Path, connection) -> None:
    """Point d'entrée minimal du processus enfant; aucun résultat n'est partagé en mémoire."""
    try:
        if os.name != "nt":
            os.setsid()
        connection.send(("ready", time.monotonic()))
        command = connection.recv()
        if command != "run":
            return
        for index, adapter in enumerate(adapters):
            adapter_id = str(getattr(adapter, "adapter_id", "adaptateur_inconnu"))
            display_name = str(getattr(adapter, "display_name", adapter_id))
            started_at = time.monotonic()
            connection.send(
                ("started", index, adapter_id, display_name, started_at)
            )
            try:
                result = adapter.probe(root)
            except BaseException as exc:
                connection.send(
                    (
                        "failure",
                        index,
                        adapter_id,
                        display_name,
                        type(exc).__name__,
                        time.monotonic(),
                    )
                )
            else:
                connection.send(
                    (
                        "result",
                        index,
                        adapter_id,
                        display_name,
                        result,
                        time.monotonic(),
                    )
                )
        connection.send(("complete", len(adapters), time.monotonic()))
        # Le parent ferme ensuite le Job Object ou le groupe de processus. Rester
        # vivant jusque-là garantit que les descendants sont encore rattachés à
        # l'arbre exact que le parent vient de superviser.
        connection.recv()
    except (BrokenPipeError, EOFError, OSError):
        # Le parent a annulé ou expiré le lot. Aucun résultat tardif n'est récupéré.
        pass
    finally:
        try:
            connection.close()
        except OSError:
            pass


class _WindowsJob:
    """Job Windows fermé avec KILL_ON_JOB_CLOSE pour tuer aussi les descendants."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, process_handle: int):
        import ctypes
        from ctypes import wintypes

        ulong_ptr = ctypes.c_size_t
        size_t = ctypes.c_size_t

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", size_t),
                ("MaximumWorkingSetSize", size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ulong_ptr),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", size_t),
                ("JobMemoryLimit", size_t),
                ("PeakProcessMemoryUsed", size_t),
                ("PeakJobMemoryUsed", size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
        )
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        self._kernel32 = kernel32
        self._handle = handle
        try:
            limits = ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = (
                self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            if not kernel32.SetInformationJobObject(
                handle,
                self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
            if not kernel32.AssignProcessToJobObject(
                handle,
                wintypes.HANDLE(process_handle),
            ):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        self._handle = None
        if handle:
            self._kernel32.CloseHandle(handle)


class _ProcessTreeGuard:
    def __init__(self, process: multiprocessing.Process, shutdown_grace_seconds: float):
        self.process = process
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self._windows_job: _WindowsJob | None = None
        self._process_group_ready = False
        self._lock = threading.Lock()
        self._stop_requested = False

    def start(self) -> bool:
        """Démarre le worker, sauf si une annulation a gagné la course."""
        with self._lock:
            if self._stop_requested:
                return False
            self.process.start()
            return True

    def attach(self) -> bool:
        with self._lock:
            if self._stop_requested or not self.process.is_alive():
                return False
            if os.name == "nt":
                self._windows_job = _WindowsJob(int(self.process.sentinel))
            else:
                # Le message "ready" est envoyé après setsid() dans le worker.
                self._process_group_ready = True
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_requested = True
            if self.process.pid is None:
                return
            process_alive = self.process.is_alive()
            if os.name == "nt" and self._windows_job is not None:
                # La fermeture du job tue le worker et tout 7-Zip qu'il aurait lancé.
                self._windows_job.close()
                self._windows_job = None
            elif os.name != "nt" and self._process_group_ready:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    if process_alive:
                        self.process.terminate()
            elif process_alive:
                self.process.terminate()
            else:
                self.process.join(0)
                return

            self.process.join(self.shutdown_grace_seconds)
            if self.process.is_alive():
                if os.name != "nt" and self._process_group_ready:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        self.process.kill()
                else:
                    self.process.kill()
                self.process.join(self.shutdown_grace_seconds)

    def finish(self) -> None:
        # Même un démarrage partiel ou une erreur de sérialisation ne doit pas
        # laisser vivre un worker dont le parent ne récupérera jamais le résultat.
        self.stop()


class _ActiveProbeBatch:
    def __init__(
        self,
        adapters: tuple[GameAdapter, ...],
        root: Path,
        *,
        timeout_seconds: float,
        startup_timeout_seconds: float,
        poll_seconds: float,
        shutdown_grace_seconds: float,
        cancel_event: threading.Event | None = None,
    ):
        self.adapters = adapters
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.poll_seconds = poll_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.cancelled = cancel_event if cancel_event is not None else threading.Event()
        self._guard: _ProcessTreeGuard | None = None
        self._guard_lock = threading.Lock()

    @staticmethod
    def _worker_failure(error_type: str) -> ProbeFailure:
        return ProbeFailure(
            adapter_id="probe_worker",
            display_name="Service isolé de détection",
            error_type=error_type,
        )

    def cancel(self) -> None:
        self.cancelled.set()
        with self._guard_lock:
            guard = self._guard
        if guard is not None:
            guard.stop()

    def _cancelled_result(self) -> ProbeBatchResult:
        return ProbeBatchResult(
            failures=(
                ProbeFailure(
                    adapter_id="probe_worker",
                    display_name="Service isolé de détection",
                    error_type="ProbeCancelled",
                    cancelled=True,
                ),
            ),
            cancelled=True,
        )

    def run(self) -> ProbeBatchResult:
        if self.cancelled.is_set():
            return self._cancelled_result()
        try:
            # Windows peut créer le processus avant de découvrir qu'un objet
            # métier n'est pas sérialisable. Vérifier d'abord évite alors un
            # enfant incomplet qui n'aurait pas encore rejoint notre Job Object.
            ForkingPickler.dumps((self.adapters, self.root))
        except Exception:
            return ProbeBatchResult(
                failures=(self._worker_failure("ProbeSerializationError"),)
            )
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        process = context.Process(
            target=_probe_worker,
            args=(self.adapters, self.root, child_connection),
            name="PFTAdapterProbe",
            daemon=False,
        )
        guard = _ProcessTreeGuard(process, self.shutdown_grace_seconds)
        with self._guard_lock:
            self._guard = guard
        try:
            try:
                if self.cancelled.is_set():
                    return self._cancelled_result()
                if not guard.start():
                    return self._cancelled_result()
            except Exception:
                if process.pid is not None:
                    guard.stop()
                return ProbeBatchResult(
                    failures=(self._worker_failure("ProbeIsolationError"),)
                )
            finally:
                child_connection.close()

            startup_deadline = time.monotonic() + self.startup_timeout_seconds
            ready = False
            while not ready:
                if self.cancelled.is_set():
                    guard.stop()
                    return self._cancelled_result()
                if parent_connection.poll(self.poll_seconds):
                    try:
                        message = parent_connection.recv()
                    except (EOFError, OSError):
                        break
                    ready = bool(message and message[0] == "ready")
                    if not ready:
                        break
                if time.monotonic() >= startup_deadline or not process.is_alive():
                    break
            if not ready:
                guard.stop()
                return ProbeBatchResult(
                    failures=(self._worker_failure("ProbeStartupTimeout"),)
                )

            try:
                attached = guard.attach()
            except OSError:
                # Le worker attend encore "run" : aucun adaptateur n'a été exécuté.
                guard.stop()
                return ProbeBatchResult(
                    failures=(self._worker_failure("ProbeIsolationError"),)
                )
            if not attached:
                guard.stop()
                return self._cancelled_result()
            if self.cancelled.is_set():
                guard.stop()
                return self._cancelled_result()
            try:
                parent_connection.send("run")
            except (BrokenPipeError, EOFError, OSError):
                guard.stop()
                return ProbeBatchResult(
                    failures=(self._worker_failure("ProbeWorkerExit"),)
                )

            results: list[DetectionResult] = []
            failures: list[ProbeFailure] = []
            current: tuple[int, str, str, float] | None = None
            completed = 0
            while True:
                if self.cancelled.is_set():
                    guard.stop()
                    return self._cancelled_result()
                if current is not None:
                    deadline = current[3] + self.timeout_seconds
                    wait_seconds = max(0.0, min(self.poll_seconds, deadline - time.monotonic()))
                else:
                    deadline = None
                    wait_seconds = self.poll_seconds

                message = None
                if parent_connection.poll(wait_seconds):
                    try:
                        message = parent_connection.recv()
                    except (EOFError, OSError):
                        message = None

                if message is None:
                    if current is not None and time.monotonic() >= deadline:
                        _index, adapter_id, display_name, _started_at = current
                        guard.stop()
                        failures.append(
                            ProbeFailure(
                                adapter_id=adapter_id,
                                display_name=display_name,
                                error_type="ProbeTimeout",
                                timed_out=True,
                            )
                        )
                        return ProbeBatchResult(tuple(results), tuple(failures))
                    if not process.is_alive():
                        failures.append(self._worker_failure("ProbeWorkerExit"))
                        return ProbeBatchResult(tuple(results), tuple(failures))
                    continue

                kind = message[0]
                if kind == "started" and current is None:
                    _kind, index, adapter_id, display_name, started_at = message
                    if index != completed:
                        failures.append(self._worker_failure("ProbeProtocolError"))
                        guard.stop()
                        return ProbeBatchResult(tuple(results), tuple(failures))
                    current = (index, adapter_id, display_name, float(started_at))
                    continue

                if kind in {"result", "failure"} and current is not None:
                    index, adapter_id, display_name, started_at = current
                    message_index = int(message[1])
                    finished_at = float(message[-1])
                    if message_index != index:
                        failures.append(self._worker_failure("ProbeProtocolError"))
                        guard.stop()
                        return ProbeBatchResult(tuple(results), tuple(failures))
                    if finished_at - started_at > self.timeout_seconds:
                        failures.append(
                            ProbeFailure(
                                adapter_id=adapter_id,
                                display_name=display_name,
                                error_type="ProbeTimeout",
                                timed_out=True,
                            )
                        )
                        guard.stop()
                        return ProbeBatchResult(tuple(results), tuple(failures))
                    if kind == "failure":
                        failures.append(
                            ProbeFailure(
                                adapter_id=adapter_id,
                                display_name=display_name,
                                error_type=str(message[4]),
                            )
                        )
                    else:
                        result = message[4]
                        if (
                            not isinstance(result, DetectionResult)
                            or result.adapter_id != adapter_id
                        ):
                            failures.append(
                                ProbeFailure(
                                    adapter_id=adapter_id,
                                    display_name=display_name,
                                    error_type="InvalidProbeResult",
                                )
                            )
                        else:
                            results.append(result)
                    completed += 1
                    current = None
                    continue

                if kind == "complete" and current is None:
                    if completed != len(self.adapters) or int(message[1]) != completed:
                        failures.append(self._worker_failure("ProbeProtocolError"))
                    guard.stop()
                    return ProbeBatchResult(tuple(results), tuple(failures))

                failures.append(self._worker_failure("ProbeProtocolError"))
                guard.stop()
                return ProbeBatchResult(tuple(results), tuple(failures))
        finally:
            try:
                parent_connection.close()
            except OSError:
                pass
            if process.pid is not None:
                guard.finish()
            with self._guard_lock:
                self._guard = None


class IsolatedProbeRunner:
    """Supervise les lots actifs afin que Tkinter puisse tous les annuler à la fermeture."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
        startup_timeout_seconds: float = DEFAULT_PROBE_STARTUP_TIMEOUT_SECONDS,
        poll_seconds: float = DEFAULT_PROBE_POLL_SECONDS,
        shutdown_grace_seconds: float = DEFAULT_PROBE_SHUTDOWN_GRACE_SECONDS,
    ):
        for label, value in (
            ("timeout_seconds", timeout_seconds),
            ("startup_timeout_seconds", startup_timeout_seconds),
            ("poll_seconds", poll_seconds),
            ("shutdown_grace_seconds", shutdown_grace_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{label} doit être strictement positif.")
        self.timeout_seconds = float(timeout_seconds)
        self.startup_timeout_seconds = float(startup_timeout_seconds)
        self.poll_seconds = float(poll_seconds)
        self.shutdown_grace_seconds = float(shutdown_grace_seconds)
        self._active: set[_ActiveProbeBatch] = set()
        self._lock = threading.Lock()

    def run(
        self,
        adapters: Iterable[GameAdapter],
        root: Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ProbeBatchResult:
        batch = _ActiveProbeBatch(
            tuple(adapters),
            root,
            timeout_seconds=self.timeout_seconds,
            startup_timeout_seconds=self.startup_timeout_seconds,
            poll_seconds=self.poll_seconds,
            shutdown_grace_seconds=self.shutdown_grace_seconds,
            cancel_event=cancel_event,
        )
        with self._lock:
            self._active.add(batch)
        try:
            return batch.run()
        finally:
            with self._lock:
                self._active.discard(batch)

    def cancel_all(self) -> None:
        with self._lock:
            active = tuple(self._active)
        for batch in active:
            batch.cancel()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)
