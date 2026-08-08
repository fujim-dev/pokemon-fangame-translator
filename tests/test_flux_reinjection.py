from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import flux_reinjection
from adapters import GameCapability, PokemonFluxAdapter
from adapters.pokemon_flux import SUPPORTED_RELEASES
from analysis.integrity import compare_snapshots, snapshot_tree
from flux_archive import FluxArchiveReader, find_7zip
from flux_import_plan import FluxImportPlan, FluxImportPlanItem, build_flux_import_plan
from flux_reinjection import (
    FluxReinjectionError,
    apply_flux_plan_to_tree,
    build_flux_candidate_archive,
    create_flux_working_copy,
    validate_candidate_on_working_copy,
)
from project_identity import write_project_identity
from ruby_marshal_reader import RubyObject, RubyString, load
from ruby_marshal_writer import dumps
from test_flux_import_validator import write_csv


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def source_hash(*values: RubyString) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(len(value.data).to_bytes(8, "big"))
        digest.update(value.data)
    return digest.hexdigest()


class RecognizedSyntheticReader(FluxArchiveReader):
    def member_sha256(self, archive_path: Path, member_path: str, inventory=None) -> str:
        if member_path.replace("\\", "/").casefold() == "data/messages_game.dat":
            return SUPPORTED_RELEASES[0].messages_game_sha256
        return super().member_sha256(archive_path, member_path, inventory)


def known_hasher(path: Path) -> str:
    release = SUPPORTED_RELEASES[0]
    if path.name == "Flux.exe":
        return release.executable_sha256
    if path.name == "Data_0.fpk":
        return release.fpk_sha256
    raise AssertionError(path)


def create_7z_archive(source: Path, destination: Path, seven_zip: Path) -> None:
    process = subprocess.run(
        [str(seven_zip), "a", "-t7z", "-mx=1", "-mmt=off", str(destination), "*"],
        cwd=str(source),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if process.returncode != 0:
        raise AssertionError((process.stdout or "") + (process.stderr or ""))


def prepare_real_archive_project(base: Path, seven_zip: Path):
    source_tree = base / "archive_source"
    data = source_tree / "Data"
    data.mkdir(parents=True)
    canonical = r"\pn<color=blue>Hello trainer.</color>"
    (data / "messages_game.dat").write_bytes(
        dumps([[{ruby_text(canonical): ruby_text(canonical)}]])
    )
    (data / "messages.dat").write_bytes(dumps([ruby_text(canonical)]))
    (data / "CommonEvents.rxdata").write_bytes(dumps([None]))
    (data / "MapInfos.rxdata").write_bytes(dumps({}))
    (data / "System.rxdata").write_bytes(dumps(RubyObject("RPG::System", {})))
    (data / "Map001.rxdata").write_bytes(
        dumps(RubyObject("RPG::Map", {"@events": {}}))
    )
    (data / "Script_index").write_text("synthetic index", encoding="utf-8")
    (data / "Script_001.rb").write_text(
        "raise 'must never execute'\n",
        encoding="utf-8",
    )

    root = base / "Synthetic Flux"
    (root / "Data").mkdir(parents=True)
    (root / "Graphics").mkdir()
    fpk = root / "Data" / "Data_0.fpk"
    create_7z_archive(source_tree, fpk, seven_zip)
    (root / "Flux.exe").write_bytes(b"synthetic executable")
    (root / "Graphics" / "Assets_0.fpk").write_bytes(b"synthetic assets")
    (root / "Flux.ini").write_text(
        "[Game]\nLibrary=RGSS104E.dll\nScripts=Data\\Scripts.rxdata\nTitle=Pokemon Flux\n",
        encoding="utf-8",
    )
    (root / "mkxp.json").write_text('{"execName":"Flux"}', encoding="utf-8")

    reader = RecognizedSyntheticReader(seven_zip)
    adapter = PokemonFluxAdapter(reader, file_hasher=known_hasher)
    detection = adapter.probe(root)
    if detection.recognized_version != "2.1.0":
        raise AssertionError(
            f"fixture Flux non reconnue : {detection.confidence}, "
            f"{[(item.evidence_id, item.weight) for item in detection.evidence]}, "
            f"{detection.warnings}, "
            f"{sorted(reader.inspect(fpk).member_paths)}"
        )
    rows, warnings = adapter.extract(root)
    if warnings:
        raise AssertionError(warnings)
    for row in rows:
        row["traduction_fr"] = r"\pn<color=blue>Bonjour dresseur.</color>"
        row["statut"] = "Accepté"
    project = base / "project"
    project.mkdir()
    csv_path = project / "textes_structures.csv"
    write_csv(csv_path, rows)
    write_project_identity(
        project,
        root,
        adapter_id="pokemon_flux",
        adapter_version="2.1.0",
    )
    plan = build_flux_import_plan(root, csv_path, adapter=adapter)
    return root, reader, adapter, csv_path, plan, canonical


class FluxSyntheticReinjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seven_zip = find_7zip()
        if cls.seven_zip is None:
            raise unittest.SkipTest("7-Zip local requis pour les tests FPK synthétiques.")

    def test_builds_and_reextracts_separate_synthetic_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_candidate_") as temp_dir:
            base = Path(temp_dir)
            root, reader, adapter, csv_path, plan, canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            original_before = snapshot_tree(root)
            csv_before = csv_path.read_bytes()
            destination = base / "candidate" / "Data_0.fpk"
            destination.parent.mkdir()

            result = build_flux_candidate_archive(
                plan,
                destination,
                archive_reader=reader,
                seven_zip=self.seven_zip,
            )

            self.assertTrue(destination.is_file())
            self.assertEqual(
                ("Data/messages.dat", "Data/messages_game.dat"),
                result.changed_members,
            )
            self.assertEqual(8, result.verified_members)
            self.assertNotEqual(result.source_fpk_sha256, result.candidate_fpk_sha256)
            self.assertTrue(compare_snapshots(original_before, snapshot_tree(root)).passed)
            self.assertEqual(csv_before, csv_path.read_bytes())
            self.assertNotIn(GameCapability.RECONSTRUCT, adapter.probe(root).capabilities)

            verification = base / "candidate_extracted"
            verification.mkdir()
            inventory = reader.inspect(destination)
            reader.extract_to(destination, verification, inventory)
            messages_game = load(verification / "Data" / "messages_game.dat")
            entry = messages_game[0][0]
            source, translated = next(iter(entry.items()))
            self.assertEqual(canonical, source.text())
            self.assertEqual(
                r"\pn<color=blue>Bonjour dresseur.</color>",
                translated.text(),
            )
            messages = load(verification / "Data" / "messages.dat")
            self.assertEqual(
                r"\pn<color=blue>Bonjour dresseur.</color>",
                messages[0].text(),
            )

    def test_validation_failure_rolls_back_candidate_creation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_candidate_rollback_") as temp_dir:
            base = Path(temp_dir)
            root, reader, _adapter, csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            original_before = snapshot_tree(root)
            csv_before = csv_path.read_bytes()
            output = base / "output"
            output.mkdir()
            destination = output / "Data_0.fpk"

            with self.assertRaisesRegex(FluxReinjectionError, "annulée|restaurée"):
                build_flux_candidate_archive(
                    plan,
                    destination,
                    archive_reader=reader,
                    seven_zip=self.seven_zip,
                    before_commit=lambda _path: (_ for _ in ()).throw(OSError("échec simulé")),
                )

            self.assertFalse(destination.exists())
            self.assertEqual([], list(output.iterdir()))
            self.assertTrue(compare_snapshots(original_before, snapshot_tree(root)).passed)
            self.assertEqual(csv_before, csv_path.read_bytes())

    def test_concurrent_destination_is_preserved_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_candidate_race_") as temp_dir:
            base = Path(temp_dir)
            _root, reader, _adapter, _csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            output = base / "output"
            output.mkdir()
            destination = output / "Data_0.fpk"

            def create_competing_file(_temporary: Path) -> None:
                destination.write_bytes(b"do not overwrite")

            with self.assertRaisesRegex(FluxReinjectionError, "apparu"):
                build_flux_candidate_archive(
                    plan,
                    destination,
                    archive_reader=reader,
                    seven_zip=self.seven_zip,
                    before_commit=create_competing_file,
                )

            self.assertEqual(b"do not overwrite", destination.read_bytes())

    def test_unplanned_member_change_blocks_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_candidate_scope_") as temp_dir:
            base = Path(temp_dir)
            root, reader, _adapter, _csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            destination = base / "candidate" / "Data_0.fpk"
            destination.parent.mkdir()
            real_apply = flux_reinjection.apply_flux_plan_to_tree

            def apply_then_corrupt(current_plan, extracted_root):
                changed = real_apply(current_plan, extracted_root)
                (Path(extracted_root) / "Data" / "Script_index").write_text(
                    "unexpected change",
                    encoding="utf-8",
                )
                return changed

            with patch(
                "flux_reinjection.apply_flux_plan_to_tree",
                side_effect=apply_then_corrupt,
            ):
                with self.assertRaisesRegex(FluxReinjectionError, "exactement du plan"):
                    build_flux_candidate_archive(
                        plan,
                        destination,
                        archive_reader=reader,
                        seven_zip=self.seven_zip,
                    )

            self.assertFalse(destination.exists())

    def test_candidate_inside_original_game_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_candidate_location_") as temp_dir:
            base = Path(temp_dir)
            root, reader, _adapter, _csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )

            alias_anchor = base / "path_alias"
            alias_anchor.mkdir()
            plans = {
                "chemin direct": plan,
                "chemin équivalent non canonique": replace(
                    plan,
                    game_root=alias_anchor / ".." / root.name,
                ),
            }
            for label, current_plan in plans.items():
                with self.subTest(label=label):
                    self.assertEqual(root.resolve(), current_plan.game_root.resolve())
                    with self.assertRaisesRegex(FluxReinjectionError, "jeu original"):
                        build_flux_candidate_archive(
                            current_plan,
                            root / "candidate.fpk",
                            archive_reader=reader,
                            seven_zip=self.seven_zip,
                        )

    def test_map_dialogue_and_choice_are_reinjected_by_exact_occurrence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_map_reinject_") as temp_dir:
            base = Path(temp_dir)
            data = base / "Data"
            data.mkdir()
            first = ruby_text("Hello")
            second = ruby_text("Trainer")
            choice = ruby_text("Yes")
            commands = [
                RubyObject("RPG::EventCommand", {"@code": 101, "@parameters": [first]}),
                RubyObject("RPG::EventCommand", {"@code": 401, "@parameters": [second]}),
                RubyObject("RPG::EventCommand", {"@code": 102, "@parameters": [[choice]]}),
            ]
            page = RubyObject("RPG::Event::Page", {"@list": commands})
            event = RubyObject("RPG::Event", {"@pages": [page]})
            map_path = data / "Map001.rxdata"
            map_path.write_bytes(
                dumps(RubyObject("RPG::Map", {"@events": {1: event}}))
            )
            dialogue_item = FluxImportPlanItem(
                id_stable="a" * 64,
                source_kind="map_events",
                internal_path="Data/Map001.rxdata",
                structural_path=("events", 1, "pages", 0, "commands", 0, "dialogue", 2),
                source_sha256=source_hash(first, second),
                current_value_sha256="",
                replacement_parts=(b"Bonjour", b"Dresseur"),
                decision="applicable",
                reason="test synthétique",
            )
            choice_item = FluxImportPlanItem(
                id_stable="b" * 64,
                source_kind="map_events",
                internal_path="Data/Map001.rxdata",
                structural_path=("events", 1, "pages", 0, "commands", 2, "choice", 0),
                source_sha256=source_hash(choice),
                current_value_sha256="",
                replacement_parts=(b"Oui",),
                decision="applicable",
                reason="test synthétique",
            )
            plan = FluxImportPlan(
                game_root=base / "unused_game",
                fpk_path=base / "unused.fpk",
                csv_path=base / "unused.csv",
                adapter_version="2.1.0",
                source_fpk_sha256="0" * 64,
                source_csv_sha256="0" * 64,
                items=(dialogue_item, choice_item),
                fingerprint="c" * 64,
            )

            changed = apply_flux_plan_to_tree(plan, base)

            self.assertEqual(("Data/Map001.rxdata",), changed)
            restored = load(map_path)
            restored_commands = (
                restored.ivars["@events"][1].ivars["@pages"][0].ivars["@list"]
            )
            self.assertEqual("Bonjour", restored_commands[0].ivars["@parameters"][0].text())
            self.assertEqual("Dresseur", restored_commands[1].ivars["@parameters"][0].text())
            self.assertEqual("Oui", restored_commands[2].ivars["@parameters"][0][0].text())

    def test_direct_application_inside_original_root_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_direct_original_") as temp_dir:
            base = Path(temp_dir)
            data = base / "game" / "Data"
            data.mkdir(parents=True)
            source = ruby_text("Hello")
            (data / "messages.dat").write_bytes(dumps([source]))
            nested_root = base / "game" / "temporary_extraction"
            nested_data = nested_root / "Data"
            nested_data.mkdir(parents=True)
            (nested_data / "messages.dat").write_bytes(dumps([source]))
            item = FluxImportPlanItem(
                id_stable="d" * 64,
                source_kind="messages",
                internal_path="Data/messages.dat",
                structural_path=("list", 0),
                source_sha256=source_hash(source),
                current_value_sha256="",
                replacement_parts=(b"Bonjour",),
                decision="applicable",
                reason="test synthétique",
            )
            before = (data / "messages.dat").read_bytes()
            nested_before = (nested_data / "messages.dat").read_bytes()

            alias_anchor = base / "path_alias"
            alias_anchor.mkdir()
            cases = {
                "racine directe": (base / "game", base / "game"),
                "racine équivalente non canonique": (
                    alias_anchor / ".." / "game",
                    base / "game",
                ),
                "sous-dossier direct": (base / "game", nested_root),
                "sous-dossier avec racine non canonique": (
                    alias_anchor / ".." / "game",
                    nested_root,
                ),
            }
            for label, (game_root, destination) in cases.items():
                with self.subTest(label=label):
                    self.assertEqual((base / "game").resolve(), game_root.resolve())
                    plan = FluxImportPlan(
                        game_root=game_root,
                        fpk_path=base / "unused.fpk",
                        csv_path=base / "unused.csv",
                        adapter_version="2.1.0",
                        source_fpk_sha256="0" * 64,
                        source_csv_sha256="0" * 64,
                        items=(item,),
                        fingerprint="e" * 64,
                    )
                    with self.assertRaisesRegex(FluxReinjectionError, "jeu original"):
                        apply_flux_plan_to_tree(plan, destination)

            self.assertEqual(before, (data / "messages.dat").read_bytes())
            self.assertEqual(nested_before, (nested_data / "messages.dat").read_bytes())

    def test_common_event_dialogue_is_reinjected_by_exact_occurrence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_common_reinject_") as temp_dir:
            base = Path(temp_dir)
            data = base / "Data"
            data.mkdir()
            source = ruby_text("Hello everyone")
            command = RubyObject(
                "RPG::EventCommand",
                {"@code": 101, "@parameters": [source]},
            )
            event = RubyObject("RPG::CommonEvent", {"@list": [command]})
            path = data / "CommonEvents.rxdata"
            path.write_bytes(dumps([None, event]))
            item = FluxImportPlanItem(
                id_stable="f" * 64,
                source_kind="common_events",
                internal_path="Data/CommonEvents.rxdata",
                structural_path=("common_events", 1, "commands", 0, "dialogue", 1),
                source_sha256=source_hash(source),
                current_value_sha256="",
                replacement_parts=(b"Bonjour tout le monde",),
                decision="applicable",
                reason="test synthétique",
            )
            plan = FluxImportPlan(
                game_root=base / "unused_game",
                fpk_path=base / "unused.fpk",
                csv_path=base / "unused.csv",
                adapter_version="2.1.0",
                source_fpk_sha256="0" * 64,
                source_csv_sha256="0" * 64,
                items=(item,),
                fingerprint="1" * 64,
            )

            changed = apply_flux_plan_to_tree(plan, base)

            self.assertEqual(("Data/CommonEvents.rxdata",), changed)
            restored = load(path)
            translated = restored[1].ivars["@list"][0].ivars["@parameters"][0]
            self.assertEqual("Bonjour tout le monde", translated.text())

    def test_candidate_installation_on_working_copy_is_fully_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_working_copy_") as temp_dir:
            base = Path(temp_dir)
            root, reader, _adapter, _csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            candidate_path = base / "candidate" / "Data_0.fpk"
            candidate_path.parent.mkdir()
            candidate = build_flux_candidate_archive(
                plan,
                candidate_path,
                archive_reader=reader,
                seven_zip=self.seven_zip,
            )
            source_before = snapshot_tree(root)
            working = create_flux_working_copy(plan, base / "working_copy")

            result = validate_candidate_on_working_copy(
                plan,
                candidate,
                working,
                base / "backups" / "Data_0.before-test.fpk",
                archive_reader=reader,
            )

            self.assertTrue(result.rollback_verified)
            self.assertEqual(result.candidate_sha256, result.installed_sha256)
            self.assertEqual(plan.source_fpk_sha256, result.restored_sha256)
            self.assertTrue(result.backup_path.is_file())
            self.assertEqual(
                plan.source_fpk_sha256,
                hashlib.sha256(result.backup_path.read_bytes()).hexdigest(),
            )
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(working)).passed)

    def test_working_copy_validation_error_still_rolls_back_exactly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_working_rollback_") as temp_dir:
            base = Path(temp_dir)
            root, reader, _adapter, _csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            candidate_path = base / "candidate" / "Data_0.fpk"
            candidate_path.parent.mkdir()
            candidate = build_flux_candidate_archive(
                plan,
                candidate_path,
                archive_reader=reader,
                seven_zip=self.seven_zip,
            )
            working = create_flux_working_copy(plan, base / "working_copy")
            before = snapshot_tree(working)

            with self.assertRaisesRegex(FluxReinjectionError, "rollback exact réussi"):
                validate_candidate_on_working_copy(
                    plan,
                    candidate,
                    working,
                    base / "backups" / "Data_0.before-failure.fpk",
                    archive_reader=reader,
                    after_install=lambda _path: (_ for _ in ()).throw(
                        RuntimeError("échec synthétique après installation")
                    ),
                )

            self.assertTrue(compare_snapshots(before, snapshot_tree(working)).passed)
            self.assertTrue(compare_snapshots(snapshot_tree(root), snapshot_tree(working)).passed)

    def test_candidate_install_failure_leaves_working_copy_untouched(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_install_failure_") as temp_dir:
            base = Path(temp_dir)
            root, reader, _adapter, _csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            candidate_path = base / "candidate" / "Data_0.fpk"
            candidate_path.parent.mkdir()
            candidate = build_flux_candidate_archive(
                plan,
                candidate_path,
                archive_reader=reader,
                seven_zip=self.seven_zip,
            )
            working = create_flux_working_copy(plan, base / "working_copy")
            working_before = snapshot_tree(working)
            real_atomic_copy = flux_reinjection.atomic_copy_file
            copy_calls = 0

            def fail_install(*args, **kwargs):
                nonlocal copy_calls
                copy_calls += 1
                if copy_calls == 2:
                    raise OSError("échec synthétique avant installation")
                return real_atomic_copy(*args, **kwargs)

            backup = base / "backups" / "Data_0.before-install-failure.fpk"
            with patch("flux_reinjection.atomic_copy_file", side_effect=fail_install):
                with self.assertRaisesRegex(FluxReinjectionError, "copie intacte"):
                    validate_candidate_on_working_copy(
                        plan,
                        candidate,
                        working,
                        backup,
                        archive_reader=reader,
                    )

            self.assertTrue(compare_snapshots(working_before, snapshot_tree(working)).passed)
            self.assertTrue(compare_snapshots(snapshot_tree(root), snapshot_tree(working)).passed)
            self.assertEqual(plan.source_fpk_sha256, hashlib.sha256(backup.read_bytes()).hexdigest())

    def test_rollback_failure_marks_copy_and_preserves_exact_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_flux_rollback_failure_") as temp_dir:
            base = Path(temp_dir)
            root, reader, _adapter, _csv_path, plan, _canonical = prepare_real_archive_project(
                base,
                self.seven_zip,
            )
            candidate_path = base / "candidate" / "Data_0.fpk"
            candidate_path.parent.mkdir()
            candidate = build_flux_candidate_archive(
                plan,
                candidate_path,
                archive_reader=reader,
                seven_zip=self.seven_zip,
            )
            working = create_flux_working_copy(plan, base / "working_copy")
            source_before = snapshot_tree(root)
            real_atomic_copy = flux_reinjection.atomic_copy_file
            copy_calls = 0

            def fail_rollback(*args, **kwargs):
                nonlocal copy_calls
                copy_calls += 1
                if copy_calls == 3:
                    raise OSError("échec synthétique du rollback")
                return real_atomic_copy(*args, **kwargs)

            backup = base / "backups" / "Data_0.before-rollback-failure.fpk"
            with patch("flux_reinjection.atomic_copy_file", side_effect=fail_rollback):
                with self.assertRaisesRegex(FluxReinjectionError, "Échec critique du rollback"):
                    validate_candidate_on_working_copy(
                        plan,
                        candidate,
                        working,
                        backup,
                        archive_reader=reader,
                    )

            marker = working / "COPIE_FLUX_INCOMPLETE.txt"
            working_fpk = working / plan.fpk_path.relative_to(plan.game_root)
            self.assertTrue(marker.is_file())
            self.assertIn("ne doit pas être utilisée", marker.read_text(encoding="utf-8"))
            self.assertEqual(plan.source_fpk_sha256, hashlib.sha256(backup.read_bytes()).hexdigest())
            self.assertEqual(
                candidate.candidate_fpk_sha256,
                hashlib.sha256(working_fpk.read_bytes()).hexdigest(),
            )
            self.assertTrue(compare_snapshots(source_before, snapshot_tree(root)).passed)


if __name__ == "__main__":
    unittest.main()
