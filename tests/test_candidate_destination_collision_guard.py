"""Prevent candidate rebuild from overwriting existing user-owned destinations.

All fixtures are synthetic and created inside TemporaryDirectory.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from analysis.integrity import snapshot_tree, compare_snapshots
from reconstruction_engine import ReconstructionError, build_plan, simulate_plan, reconstruct_copy
from test_reconstruction_safety import prepare_essentials_game, write_project, pbs_row


class CandidateDestinationCollisionGuardTests(unittest.TestCase):
    def _case(self, destination_kind: str):
        with tempfile.TemporaryDirectory(prefix="pft_candidate_collision_") as temporary:
            base = Path(temporary)
            game = base / "game"
            prepare_essentials_game(game)
            source = game / "PBS" / "fixture.txt"
            source.write_text("Name = Hello\n", encoding="utf-8")
            project = base / "project.csv"
            write_project(project, pbs_row("PBS/fixture.txt"))
            plan = simulate_plan(build_plan(game, project))
            before = snapshot_tree(game)
            target = base / "game_VERSION_FR"
            if destination_kind == "nonempty_directory":
                target.mkdir()
                marker = target / "important-user-file.txt"
                marker.write_bytes(b"Synthetic existing user-owned contents")
                sentinel = marker.read_bytes()
            elif destination_kind == "empty_directory":
                target.mkdir()
            else:
                target.write_bytes(b"Existing ordinary file must not be replaced")
                sentinel = target.read_bytes()
            with self.assertRaisesRegex(ReconstructionError, "existe"):
                reconstruct_copy(plan, target, base / "reports")
            self.assertTrue(compare_snapshots(before, snapshot_tree(game)).passed)
            if destination_kind == "nonempty_directory":
                self.assertEqual(sentinel, marker.read_bytes())
                self.assertEqual(["important-user-file.txt"], [p.name for p in target.iterdir()])
            elif destination_kind == "empty_directory":
                self.assertTrue(target.is_dir())
                self.assertFalse(list(target.iterdir()))
            else:
                self.assertTrue(target.is_file())
                self.assertEqual(sentinel, target.read_bytes())

    def test_nonempty_candidate_directory_remains_untouched(self):
        self._case("nonempty_directory")

    def test_empty_existing_candidate_directory_is_not_reused(self):
        self._case("empty_directory")

    def test_existing_file_at_candidate_path_remains_untouched(self):
        self._case("existing_file")


if __name__ == "__main__":
    unittest.main()
