from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import (
    AdapterOperationBlocked,
    AdapterRegistry,
    DetectionResult,
    GameCapability,
    PokemonEssentialsAdapter,
    UnknownAdapter,
    create_default_registry,
)


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


class FixedRegistry:
    def __init__(self, result: DetectionResult):
        self.result = result

    def detect(self, root: Path) -> DetectionResult:
        del root
        return self.result


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
            self.assertEqual(result.recognized_version, "21.1")
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
                result = create_default_registry().detect(root)

            self.assertEqual(result.adapter_id, "unknown")
            self.assertFalse(result.write_actions_allowed)
            self.assertEqual(
                result.capabilities,
                frozenset({GameCapability.ANALYZE, GameCapability.DEEP_ANALYZE}),
            )
            self.assertTrue(any("redirig" in warning.casefold() for warning in result.warnings))
            essentials_probe.assert_not_called()

    def test_redirected_essentials_marker_blocks_detection_and_direct_extraction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "JeuTest"
            self._write(root / "Game.exe")
            self._write(root / "Game.ini")
            self._write(root / "Data" / "System.rxdata")
            self._write(root / "Data" / "Map001.rxdata")
            self._write(root / "Data" / "messages_game.dat")
            self._write(root / "PBS" / "pokemon.txt", b"Pokemon Essentials v21.1")
            redirected = root / "PBS"

            def marks_pbs_as_redirected(path: Path) -> bool:
                return path == redirected

            with patch(
                "adapters.pokemon_essentials._is_link_or_junction",
                side_effect=marks_pbs_as_redirected,
            ):
                result = create_default_registry().detect(root)

            self.assertEqual(result.adapter_id, "unknown")
            self.assertFalse(result.write_actions_allowed)
            self.assertTrue(any("redirig" in warning.casefold() for warning in result.warnings))
            self.assertNotEqual("21.1", result.recognized_version)

            with (
                patch(
                    "adapters.pokemon_essentials._is_link_or_junction",
                    side_effect=marks_pbs_as_redirected,
                ),
                patch("adapters.pokemon_essentials.extract_structured") as extractor,
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

            with patch(
                "adapters.pokemon_essentials.extract_structured",
                return_value=expected,
            ) as extractor:
                result = PokemonEssentialsAdapter().extract(root)

            self.assertEqual(result, expected)
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
            patch("adapters.pokemon_essentials.extract_structured") as extractor,
        ):
            with self.assertRaisesRegex(AdapterOperationBlocked, "ambigu"):
                PokemonEssentialsAdapter().extract(Path("synthetic"))

        extractor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
