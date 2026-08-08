from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import PokemonFluxAdapter
from adapters.pokemon_flux import SUPPORTED_RELEASES
from analysis.integrity import compare_snapshots, snapshot_tree
from flux_archive import FluxArchiveEntry, FluxArchiveInventory
from flux_import_validator import FluxImportValidationError, validate_flux_import
from project_identity import write_project_identity
from ruby_marshal_reader import RubyString
from ruby_marshal_writer import dumps
from safe_io import atomic_text_writer


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def inventory() -> FluxArchiveInventory:
    members = {
        "Data/messages_game.dat",
        "Data/messages.dat",
        "Data/CommonEvents.rxdata",
        "Data/MapInfos.rxdata",
        "Data/System.rxdata",
        "Data/Script_index",
        "Data/Script_001.rb",
        "Data/Map001.rxdata",
    }
    return FluxArchiveInventory(
        archive_type="7z",
        physical_size=1024,
        entries=tuple(
            FluxArchiveEntry(path, 10, 10, "A", False, "Copy")
            for path in sorted(members)
        ),
    )


class FakeArchiveReader:
    def inspect(self, archive_path: Path) -> FluxArchiveInventory:
        del archive_path
        return inventory()

    def member_sha256(self, archive_path: Path, member_path: str, inventory=None) -> str:
        del archive_path, inventory
        if member_path != "Data/messages_game.dat":
            raise AssertionError(member_path)
        return SUPPORTED_RELEASES[0].messages_game_sha256

    def extract_to(self, archive_path: Path, target_root: Path, inventory=None) -> None:
        del archive_path, inventory
        data = target_root / "Data"
        data.mkdir()
        canonical = r"\pn<color=blue>Hello trainer.</color>"
        (data / "messages_game.dat").write_bytes(
            dumps([[{ruby_text(canonical): ruby_text(canonical)}]])
        )
        (data / "messages.dat").write_bytes(dumps([ruby_text(canonical)]))
        (data / "CommonEvents.rxdata").write_bytes(dumps([None]))


def known_hasher(path: Path) -> str:
    release = SUPPORTED_RELEASES[0]
    if path.name == "Flux.exe":
        return release.executable_sha256
    if path.name == "Data_0.fpk":
        return release.fpk_sha256
    raise AssertionError(path)


def make_flux_root(base: Path) -> Path:
    root = base / "Synthetic Flux"
    (root / "Data").mkdir(parents=True)
    (root / "Graphics").mkdir()
    (root / "Flux.exe").write_bytes(b"synthetic executable")
    (root / "Data" / "Data_0.fpk").write_bytes(b"synthetic fpk kept read-only")
    (root / "Graphics" / "Assets_0.fpk").write_bytes(b"synthetic assets")
    (root / "Flux.ini").write_text(
        "[Game]\nLibrary=RGSS104E.dll\nScripts=Data\\Scripts.rxdata\nTitle=Pokemon Flux\n",
        encoding="utf-8",
    )
    (root / "mkxp.json").write_text('{"execName":"Flux"}', encoding="utf-8")
    return root


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with atomic_text_writer(path, encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def prepared_project(base: Path):
    root = make_flux_root(base)
    adapter = PokemonFluxAdapter(FakeArchiveReader(), file_hasher=known_hasher)
    rows, errors = adapter.extract(root)
    if errors:
        raise AssertionError(errors)
    project = base / "project"
    project.mkdir()
    csv_path = project / "textes_structures.csv"
    write_project_identity(
        project,
        root,
        adapter_id="pokemon_flux",
        adapter_version="2.1.0",
    )
    return root, adapter, rows, csv_path


class FluxImportValidatorTests(unittest.TestCase):
    def test_validates_accepted_translation_without_modifying_game_or_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_valid_") as temp_dir:
            base = Path(temp_dir)
            root, adapter, rows, csv_path = prepared_project(base)
            rows[0]["traduction_fr"] = r"\pn<color=blue>Bonjour dresseur.</color>"
            rows[0]["statut"] = "Accepté"
            write_csv(csv_path, rows)
            game_before = snapshot_tree(root)
            csv_before = csv_path.read_bytes()

            report = validate_flux_import(root, csv_path, adapter=adapter)

            game_after = snapshot_tree(root)
            self.assertTrue(report.structurally_valid)
            self.assertTrue(report.ready_for_future_import)
            self.assertTrue(report.original_fpk_unchanged)
            self.assertEqual(2, report.matched_occurrences)
            self.assertEqual(1, report.eligible_translations)
            self.assertEqual(1, report.untranslated)
            self.assertEqual((), report.issues)
            self.assertTrue(compare_snapshots(game_before, game_after).passed)
            self.assertEqual(csv_before, csv_path.read_bytes())

    def test_rejects_any_changed_structural_field(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_structure_") as temp_dir:
            base = Path(temp_dir)
            root, adapter, rows, csv_path = prepared_project(base)
            rows[0]["traduction_fr"] = r"\pn<color=blue>Bonjour dresseur.</color>"
            rows[0]["statut"] = "Accepté"
            rows[0]["chemin_structurel"] = "[]"
            write_csv(csv_path, rows)

            report = validate_flux_import(root, csv_path, adapter=adapter)

            self.assertFalse(report.structurally_valid)
            self.assertFalse(report.ready_for_future_import)
            self.assertTrue(any(
                issue.code == "immutable_field_changed"
                and issue.field == "chemin_structurel"
                for issue in report.issues
            ))

    def test_rejects_reordered_commands_and_tags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_commands_") as temp_dir:
            base = Path(temp_dir)
            root, adapter, rows, csv_path = prepared_project(base)
            rows[0]["traduction_fr"] = r"<color=blue>\pnBonjour dresseur.</color>"
            rows[0]["statut"] = "Accepté"
            write_csv(csv_path, rows)

            report = validate_flux_import(root, csv_path, adapter=adapter)

            self.assertFalse(report.structurally_valid)
            self.assertTrue(any(
                issue.code == "protected_commands_changed"
                for issue in report.issues
            ))

    def test_rejects_duplicate_missing_and_unknown_occurrences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_ids_") as temp_dir:
            base = Path(temp_dir)
            root, adapter, rows, csv_path = prepared_project(base)
            unexpected = dict(rows[0])
            unexpected["id_stable"] = "f" * 64
            write_csv(csv_path, [rows[0], rows[0], unexpected])

            report = validate_flux_import(root, csv_path, adapter=adapter)

            codes = {issue.code for issue in report.issues}
            self.assertFalse(report.structurally_valid)
            self.assertIn("duplicate_id", codes)
            self.assertIn("missing_occurrence", codes)
            self.assertIn("unexpected_occurrence", codes)

    def test_unknown_flux_release_cannot_validate_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_unknown_") as temp_dir:
            base = Path(temp_dir)
            root, _adapter, rows, csv_path = prepared_project(base)
            write_csv(csv_path, rows)
            unknown_adapter = PokemonFluxAdapter(
                FakeArchiveReader(),
                file_hasher=lambda _path: "0" * 64,
            )

            with self.assertRaisesRegex(FluxImportValidationError, "non homologuée"):
                validate_flux_import(root, csv_path, adapter=unknown_adapter)

    def test_redirected_csv_is_rejected_before_any_control_extraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_link_") as temp_dir:
            base = Path(temp_dir)
            root, adapter, rows, csv_path = prepared_project(base)
            write_csv(csv_path, rows)

            with patch(
                "flux_import_validator._is_redirected",
                side_effect=lambda path: Path(path) == csv_path,
            ):
                with self.assertRaisesRegex(FluxImportValidationError, "redirigé"):
                    validate_flux_import(root, csv_path, adapter=adapter)

    def test_control_extraction_with_duplicate_ids_blocks_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_control_ids_") as temp_dir:
            base = Path(temp_dir)
            root, adapter, rows, csv_path = prepared_project(base)
            write_csv(csv_path, rows)

            with patch.object(adapter, "extract", return_value=([rows[0], rows[0]], [])):
                with self.assertRaisesRegex(FluxImportValidationError, "dupliqués"):
                    validate_flux_import(root, csv_path, adapter=adapter)

    def test_extraction_warning_keeps_future_import_blocked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_import_warning_") as temp_dir:
            base = Path(temp_dir)
            root, adapter, rows, csv_path = prepared_project(base)
            rows[0]["traduction_fr"] = r"\pn<color=blue>Bonjour dresseur.</color>"
            rows[0]["statut"] = "Accepté"
            write_csv(csv_path, rows)

            with patch.object(
                adapter,
                "extract",
                return_value=(rows, ["structure synthétique incertaine"]),
            ):
                report = validate_flux_import(root, csv_path, adapter=adapter)

            self.assertTrue(report.structurally_valid)
            self.assertFalse(report.ready_for_future_import)
            self.assertEqual(
                ("structure synthétique incertaine",),
                report.extraction_warnings,
            )


if __name__ == "__main__":
    unittest.main()
