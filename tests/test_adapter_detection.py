from __future__ import annotations

import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import adapters.pokemon_essentials as pokemon_essentials_module
import adapters.registry as registry_module
from adapters import (
    AdapterOperationBlocked,
    AdapterRegistry,
    DetectionResult,
    GameCapability,
    PokemonEssentialsAdapter,
    PokemonFluxAdapter,
    UnknownAdapter,
    create_default_registry,
)
from adapters.probe_isolation import IsolatedProbeRunner, ProbeBatchResult, ProbeFailure
from structured_extractor import StructuredExtractionResult


class StaticAdapter:
    def __init__(self, adapter_id: str, confidence: int):
        self.adapter_id = adapter_id
        self.display_name = adapter_id
        self.confidence = confidence

    def probe(self, root: Path) -> DetectionResult:
        del root
        return DetectionResult(
            adapter_id=self.adapter_id,
            display_name=self.display_name,
            confidence=self.confidence,
            capabilities=frozenset(GameCapability),
            write_actions_allowed=True,
        )


class FailingAdapter:
    adapter_id = "failing"
    display_name = "Adaptateur synthétique défaillant"

    def probe(self, root: Path) -> DetectionResult:
        del root
        raise OSError("chemin privé volontairement absent du message public")


class DelayedAdapter(StaticAdapter):
    def __init__(
        self,
        adapter_id: str,
        confidence: int,
        delay_seconds: float,
        *,
        late_marker: str = "",
        late_error: bool = False,
    ):
        super().__init__(adapter_id, confidence)
        self.delay_seconds = delay_seconds
        self.late_marker = late_marker
        self.late_error = late_error

    def probe(self, root: Path) -> DetectionResult:
        time.sleep(self.delay_seconds)
        if self.late_marker:
            Path(self.late_marker).write_text("late", encoding="utf-8")
        if self.late_error:
            raise RuntimeError("late synthetic confidential error")
        return super().probe(root)


class NeverReturningAdapter:
    adapter_id = "never_returning"
    display_name = "Sonde synthetique bloquee"

    def probe(self, root: Path) -> DetectionResult:
        del root
        while True:
            time.sleep(0.05)


def write_delayed_marker(marker: str, delay_seconds: float) -> None:
    time.sleep(delay_seconds)
    Path(marker).write_text("descendant encore actif", encoding="utf-8")


class DescendantSpawningAdapter(NeverReturningAdapter):
    adapter_id = "descendant_spawning"
    display_name = "Sonde synthetique avec descendant"

    def __init__(self, marker: str, delay_seconds: float):
        self.marker = marker
        self.delay_seconds = delay_seconds

    def probe(self, root: Path) -> DetectionResult:
        del root
        child = multiprocessing.get_context("spawn").Process(
            target=write_delayed_marker,
            args=(self.marker, self.delay_seconds),
            name="PFTProbeDescendant",
        )
        child.start()
        while True:
            time.sleep(0.05)


class UnserializableAdapter(StaticAdapter):
    def __init__(self):
        super().__init__("unserializable", 99)
        self.unserializable_callback = lambda: None


class InlineProbeRunner:
    """Execution locale reservee aux tests unitaires d'un patch de chemin."""

    def run(self, adapters, root: Path) -> ProbeBatchResult:
        results = []
        failures = []
        for adapter in adapters:
            try:
                results.append(adapter.probe(root))
            except Exception as exc:
                failures.append(
                    ProbeFailure(
                        adapter_id=adapter.adapter_id,
                        display_name=adapter.display_name,
                        error_type=type(exc).__name__,
                    )
                )
        return ProbeBatchResult(tuple(results), tuple(failures))

    def cancel_all(self) -> None:
        return None

    @property
    def active_count(self) -> int:
        return 0


def inline_default_registry(*, replacement=None) -> AdapterRegistry:
    adapters = [PokemonEssentialsAdapter(), PokemonFluxAdapter()]
    if replacement is not None:
        adapters = [
            replacement if adapter.adapter_id == replacement.adapter_id else adapter
            for adapter in adapters
        ]
    return AdapterRegistry(tuple(adapters), probe_runner=InlineProbeRunner())


class FixedRegistry:
    def __init__(self, result: DetectionResult):
        self.result = result

    def detect(self, root: Path) -> DetectionResult:
        del root
        return self.result


class SyntheticSymlinkPath:
    def is_symlink(self) -> bool:
        return True


class SyntheticJunctionPath:
    def is_symlink(self) -> bool:
        return False

    def is_junction(self) -> bool:
        return True


class SyntheticReparsePointPath:
    """Simule Python 3.11 : pas de Path.is_junction(), attribut Windows présent."""

    def is_symlink(self) -> bool:
        return False

    def lstat(self) -> SimpleNamespace:
        return SimpleNamespace(st_file_attributes=0x0400)


class AdapterDetectionTests(unittest.TestCase):
    @staticmethod
    def _write(path: Path, content: bytes = b"synthetic test data") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_classic_essentials_structure_is_recognized_without_modification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "JeuTest"
            self._write(root / "Game.exe")
            self._write(root / "Game.ini")
            self._write(root / "Data" / "System.rxdata")
            self._write(root / "Data" / "Map1000.rxdata")
            self._write(root / "Data" / "messages_game.dat")
            self._write(root / "PBS" / "pokemon.txt", b"Pokemon Essentials v21.1")
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

            result = create_default_registry().detect(root)

            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(result.adapter_id, "pokemon_essentials")
            self.assertTrue(result.write_actions_allowed)
            self.assertTrue(result.can(GameCapability.EXTRACT))
            self.assertTrue(result.can(GameCapability.TRANSLATE))
            self.assertTrue(result.can(GameCapability.RECONSTRUCT))
            self.assertEqual(result.recognized_version, "inconnue")
            self.assertEqual("", result.declared_version)
            self.assertEqual("essentials_legacy_rxmp", result.structural_profile)
            self.assertIn("maps", {item.evidence_id for item in result.evidence})
            self.assertEqual(before, after)

    def test_folder_name_alone_never_selects_an_adapter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Pokemon Flux Essentials"
            root.mkdir()

            result = create_default_registry().detect(root)

            self.assertEqual(result.adapter_id, "unknown")
            self.assertFalse(result.write_actions_allowed)
            self.assertEqual(
                result.capabilities,
                frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            )

    def test_incomplete_rpg_maker_structure_keeps_write_actions_locked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ProjetPartiel"
            self._write(root / "Game.exe")
            self._write(root / "Game.ini")
            self._write(root / "Data" / "System.rxdata")
            self._write(root / "Data" / "Map001.rxdata")

            result = create_default_registry().detect(root)

            self.assertEqual(result.adapter_id, "unknown")
            self.assertFalse(result.write_actions_allowed)
            self.assertTrue(any("insuffis" in warning.casefold() for warning in result.warnings))

    def test_empty_marker_directories_do_not_unlock_write_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "FauxPositif"
            self._write(root / "Game.exe")
            self._write(root / "Game.ini")
            self._write(root / "Data" / "System.rxdata")
            (root / "PBS").mkdir()
            (root / "Graphics" / "Pokemon").mkdir(parents=True)

            result = create_default_registry().detect(root)

            self.assertEqual(result.adapter_id, "unknown")
            self.assertFalse(result.write_actions_allowed)

    def test_redirected_game_root_is_blocked_before_adapter_probes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "JeuRedirige"
            root.mkdir()

            with (
                patch("adapters.registry._is_link_or_junction", return_value=True),
                patch.object(PokemonEssentialsAdapter, "probe") as essentials_probe,
            ):
                result = inline_default_registry().detect(root)

            self.assertEqual(result.adapter_id, "unknown")
            self.assertFalse(result.write_actions_allowed)
            self.assertEqual(
                result.capabilities,
                frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            )
            self.assertTrue(any("redirig" in warning.casefold() for warning in result.warnings))
            essentials_probe.assert_not_called()

    def test_symlink_junction_and_generic_reparse_points_are_all_detected(self):
        redirected_path_types = (
            ("symlink", SyntheticSymlinkPath()),
            ("junction", SyntheticJunctionPath()),
            ("generic_reparse_point", SyntheticReparsePointPath()),
        )
        helpers = (
            ("registry", registry_module._is_link_or_junction),
            ("essentials", pokemon_essentials_module._is_link_or_junction),
        )

        for helper_name, helper in helpers:
            for path_type, redirected_path in redirected_path_types:
                with self.subTest(helper=helper_name, path_type=path_type):
                    self.assertTrue(helper(redirected_path))

    def test_redirected_essentials_marker_blocks_detection_and_direct_extraction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            actual_root = Path(temp_dir) / "JeuTest"
            self._write(actual_root / "Game.exe")
            self._write(actual_root / "Game.ini")
            self._write(actual_root / "Data" / "System.rxdata")
            self._write(actual_root / "Data" / "Map001.rxdata")
            self._write(actual_root / "Data" / "messages_game.dat")
            self._write(actual_root / "PBS" / "pokemon.txt", b"Pokemon Essentials v21.1")

            alias_parent = Path(temp_dir) / "CheminAlias"
            alias_parent.mkdir()
            root = alias_parent / ".." / actual_root.name
            redirected_parent = actual_root.resolve()

            self.assertNotEqual(root, actual_root)
            self.assertEqual(root.resolve(), redirected_parent)

            def marks_pbs_as_redirected(path: Path) -> bool:
                return (
                    path.name.casefold() == "pbs"
                    and path.parent.resolve() == redirected_parent
                )

            with patch(
                "adapters.pokemon_essentials._is_link_or_junction",
                side_effect=marks_pbs_as_redirected,
            ):
                result = inline_default_registry().detect(root)

            self.assertEqual(result.adapter_id, "unknown")
            self.assertFalse(result.write_actions_allowed)
            self.assertTrue(any("redirig" in warning.casefold() for warning in result.warnings))
            self.assertNotEqual("21.1", result.recognized_version)

            with (
                patch(
                    "adapters.pokemon_essentials._is_link_or_junction",
                    side_effect=marks_pbs_as_redirected,
                ),
                patch(
                    "adapters.registry.create_default_registry",
                    side_effect=inline_default_registry,
                ),
                patch(
                    "adapters.pokemon_essentials.extract_structured_verified"
                ) as extractor,
            ):
                with self.assertRaisesRegex(AdapterOperationBlocked, "bloqu"):
                    PokemonEssentialsAdapter().extract(root)

            extractor.assert_not_called()

    def test_close_scores_are_reported_as_ambiguous(self):
        registry = AdapterRegistry(
            (StaticAdapter("adapter_a", 90), StaticAdapter("adapter_b", 85)),
            ambiguity_margin=8,
        )

        result = registry.detect(Path("."))

        self.assertEqual(result.adapter_id, "unknown")
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.write_actions_allowed)
        self.assertTrue(any("ambigu" in warning.casefold() for warning in result.warnings))

    def test_probe_failure_keeps_the_registry_read_only_without_raw_error_details(self):
        registry = AdapterRegistry((StaticAdapter("adapter_valide", 95), FailingAdapter()))

        result = registry.detect(Path("."))

        self.assertEqual(result.adapter_id, "unknown")
        self.assertFalse(result.write_actions_allowed)
        self.assertEqual(
            result.capabilities,
            frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
        )
        warning = " ".join(result.warnings)
        self.assertIn("Détection incomplète", warning)
        self.assertIn("OSError", warning)
        self.assertNotIn("chemin privé", warning)

    def test_probe_finishing_just_before_deadline_is_accepted(self):
        runner = IsolatedProbeRunner(
            timeout_seconds=0.35,
            startup_timeout_seconds=10.0,
        )
        registry = AdapterRegistry(
            (DelayedAdapter("adapter_rapide", 95, 0.20),),
            probe_runner=runner,
        )

        result = registry.detect(Path("."))

        self.assertEqual("adapter_rapide", result.adapter_id)
        self.assertTrue(result.write_actions_allowed)
        self.assertEqual(0, runner.active_count)

    def test_probe_finishing_after_deadline_is_killed_and_never_published(self):
        with tempfile.TemporaryDirectory(prefix="pft_test_probe_late_") as temporary:
            marker = Path(temporary) / "late-result-used.txt"
            runner = IsolatedProbeRunner(
                timeout_seconds=0.15,
                startup_timeout_seconds=10.0,
            )
            registry = AdapterRegistry(
                (
                    DelayedAdapter(
                        "adapter_tardif",
                        99,
                        0.45,
                        late_marker=str(marker),
                    ),
                ),
                probe_runner=runner,
            )

            result = registry.detect(Path("."))
            announced = (
                result.adapter_id,
                result.capabilities,
                result.write_actions_allowed,
                result.warnings,
            )
            time.sleep(0.5)

            self.assertFalse(marker.exists())

        self.assertEqual("unknown", result.adapter_id)
        self.assertFalse(result.write_actions_allowed)
        self.assertTrue(any("expir" in warning.casefold() for warning in result.warnings))
        self.assertEqual(
            announced,
            (
                result.adapter_id,
                result.capabilities,
                result.write_actions_allowed,
                result.warnings,
            ),
        )
        self.assertEqual(0, runner.active_count)

    def test_never_returning_probe_is_stopped_without_residual_process(self):
        runner = IsolatedProbeRunner(
            timeout_seconds=0.15,
            startup_timeout_seconds=10.0,
        )
        registry = AdapterRegistry(
            (NeverReturningAdapter(),),
            probe_runner=runner,
        )

        started = time.monotonic()
        result = registry.detect(Path("."))
        elapsed = time.monotonic() - started

        self.assertEqual("unknown", result.adapter_id)
        self.assertLess(elapsed, 5.0)
        self.assertEqual(0, runner.active_count)
        self.assertFalse(
            any(
                child.name == "PFTAdapterProbe" and child.is_alive()
                for child in multiprocessing.active_children()
            )
        )

    def test_precancelled_detection_never_starts_a_probe(self):
        with tempfile.TemporaryDirectory(prefix="pft_test_probe_precancel_") as temporary:
            marker = Path(temporary) / "probe-started.txt"
            cancel_event = threading.Event()
            cancel_event.set()
            runner = IsolatedProbeRunner(
                timeout_seconds=10.0,
                startup_timeout_seconds=10.0,
            )
            registry = AdapterRegistry(
                (
                    DelayedAdapter(
                        "adapter_annule",
                        99,
                        0.0,
                        late_marker=str(marker),
                    ),
                ),
                probe_runner=runner,
            )

            result = registry.detect(Path("."), cancel_event=cancel_event)

            self.assertFalse(marker.exists())
        self.assertEqual("unknown", result.adapter_id)
        self.assertTrue(any("annul" in warning.casefold() for warning in result.warnings))
        self.assertEqual(0, runner.active_count)

    def test_timeout_kills_processes_spawned_by_the_probe(self):
        with tempfile.TemporaryDirectory(prefix="pft_test_probe_tree_") as temporary:
            marker = Path(temporary) / "descendant-survived.txt"
            runner = IsolatedProbeRunner(
                timeout_seconds=0.15,
                startup_timeout_seconds=10.0,
            )
            registry = AdapterRegistry(
                (DescendantSpawningAdapter(str(marker), 0.60),),
                probe_runner=runner,
            )

            result = registry.detect(Path("."))
            time.sleep(0.75)

            self.assertFalse(marker.exists())
        self.assertEqual("unknown", result.adapter_id)
        self.assertEqual(0, runner.active_count)

    def test_unserializable_probe_is_refused_before_starting_a_process(self):
        runner = IsolatedProbeRunner(
            timeout_seconds=10.0,
            startup_timeout_seconds=10.0,
        )
        registry = AdapterRegistry(
            (UnserializableAdapter(),),
            probe_runner=runner,
        )

        result = registry.detect(Path("."))

        self.assertEqual("unknown", result.adapter_id)
        self.assertFalse(result.write_actions_allowed)
        self.assertEqual(0, runner.active_count)
        self.assertFalse(
            any(
                child.name == "PFTAdapterProbe" and child.is_alive()
                for child in multiprocessing.active_children()
            )
        )

    def test_exception_raised_after_expiration_is_contained(self):
        with tempfile.TemporaryDirectory(prefix="pft_test_probe_late_error_") as temporary:
            marker = Path(temporary) / "late-error.txt"
            runner = IsolatedProbeRunner(
                timeout_seconds=0.15,
                startup_timeout_seconds=10.0,
            )
            registry = AdapterRegistry(
                (
                    DelayedAdapter(
                        "adapter_erreur_tardive",
                        99,
                        0.45,
                        late_marker=str(marker),
                        late_error=True,
                    ),
                ),
                probe_runner=runner,
            )

            result = registry.detect(Path("."))
            time.sleep(0.5)

            self.assertFalse(marker.exists())

        self.assertEqual("unknown", result.adapter_id)
        self.assertNotIn("confidential", " ".join(result.warnings).casefold())
        self.assertEqual(0, runner.active_count)

    def test_one_expired_probe_invalidates_another_successful_adapter(self):
        runner = IsolatedProbeRunner(
            timeout_seconds=0.15,
            startup_timeout_seconds=10.0,
        )
        registry = AdapterRegistry(
            (StaticAdapter("adapter_valide", 99), NeverReturningAdapter()),
            probe_runner=runner,
        )

        result = registry.detect(Path("."))

        self.assertEqual("unknown", result.adapter_id)
        self.assertFalse(result.write_actions_allowed)
        self.assertNotEqual("adapter_valide", result.adapter_id)
        self.assertEqual(0, runner.active_count)

    def test_application_cancellation_stops_active_probe_and_coordinator(self):
        runner = IsolatedProbeRunner(
            timeout_seconds=10.0,
            startup_timeout_seconds=10.0,
        )
        registry = AdapterRegistry(
            (NeverReturningAdapter(),),
            probe_runner=runner,
        )
        result_holder = []
        coordinator = threading.Thread(
            target=lambda: result_holder.append(registry.detect(Path("."))),
            name="PFTSyntheticDetectionCoordinator",
        )
        coordinator.start()
        deadline = time.monotonic() + 10.0
        while runner.active_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        registry.cancel_active()
        coordinator.join(timeout=5.0)

        self.assertFalse(coordinator.is_alive())
        self.assertEqual(1, len(result_holder))
        self.assertEqual("unknown", result_holder[0].adapter_id)
        self.assertTrue(
            any("annul" in warning.casefold() for warning in result_holder[0].warnings)
        )
        self.assertEqual(0, runner.active_count)

    def test_unknown_adapter_refuses_extraction_even_if_called_directly(self):
        with self.assertRaises(AdapterOperationBlocked):
            UnknownAdapter().extract(Path("."))

    def test_essentials_extraction_delegates_to_existing_structured_extractor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "JeuTest"
            self._write(root / "Game.exe")
            self._write(root / "Game.ini")
            self._write(root / "Data" / "System.rxdata")
            self._write(root / "Data" / "messages_game.dat")
            self._write(root / "PBS" / "pokemon.txt")
            expected = ([{"identifiant": "synthetic"}], ["alerte synthétique"])
            verified = StructuredExtractionResult(
                rows=expected[0],
                errors=expected[1],
                sources=(),
                source_manifest_sha256="0" * 64,
            )

            with patch(
                "adapters.pokemon_essentials.extract_structured_verified",
                return_value=verified,
            ) as extractor:
                result = PokemonEssentialsAdapter().extract(root)

            self.assertEqual(result[1], expected[1])
            self.assertEqual("synthetic", result[0][0]["identifiant"])
            self.assertEqual(
                "essentials_legacy_rxmp",
                result[0][0]["profil_essentials"],
            )
            extractor.assert_called_once_with(root, progress=None, logger=None)

    def test_direct_essentials_extraction_respects_an_ambiguous_registry_decision(self):
        ambiguous = DetectionResult(
            adapter_id="unknown",
            display_name="Structure inconnue",
            confidence=90,
            capabilities=frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            warnings=("Détection ambiguë synthétique.",),
            adapter_recognized=False,
            write_actions_allowed=False,
            ambiguous=True,
        )
        with (
            patch(
                "adapters.registry.create_default_registry",
                return_value=FixedRegistry(ambiguous),
            ),
            patch(
                "adapters.pokemon_essentials.extract_structured_verified"
            ) as extractor,
        ):
            with self.assertRaisesRegex(AdapterOperationBlocked, "ambigu"):
                PokemonEssentialsAdapter().extract(Path("synthetic"))

        extractor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
