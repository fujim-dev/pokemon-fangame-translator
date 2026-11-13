"""Synthetic unrelated asset update: candidate must copy the latest bytes safely.

Translated sources are plan-locked; a non-target asset may be updated before
copying, but the candidate must faithfully copy the updated asset and never
mutate the original. This test does NOT authorize stale translation sources.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from analysis.integrity import compare_snapshots, snapshot_tree
from reconstruction_engine import reconstruct_copy
import test_candidate_source_drift_guard as drift_fixture


class CandidateUnrelatedAssetSnapshotTests(unittest.TestCase):
    def _case(self, kind: str):
        with tempfile.TemporaryDirectory(prefix="pft_unrelated_asset_snapshot_") as temporary:
            base = Path(temporary)
            game, plan = drift_fixture.CandidateSourceDriftGuardTests()._make_plan(base, kind)
            asset = game / "Data" / "System.rxdata"
            self.assertTrue(asset.is_file())
            new_contents = asset.read_bytes() + b"synthetic unexpected change"
            asset.write_bytes(new_contents)
            before = snapshot_tree(game)
            candidate = base / "candidate"
            result = reconstruct_copy(plan, candidate, base / "reports")
            self.assertTrue(result.original_unchanged)
            self.assertTrue(result.integrity_valid)
            self.assertEqual(new_contents, asset.read_bytes())
            self.assertEqual(new_contents, (candidate / "Data" / "System.rxdata").read_bytes())
            self.assertTrue(compare_snapshots(before, snapshot_tree(game)).passed)
            comparison = compare_snapshots(
                before, snapshot_tree(candidate),
                allowed_changed=set(result.modified_files),
            )
            self.assertEqual((), comparison.missing_files)
            self.assertEqual((), comparison.changed_files)
            self.assertEqual((), comparison.emptied_files)

    def test_map_candidate_copies_updated_unrelated_asset_exactly(self):
        self._case("map")

    def test_message_bank_candidate_copies_updated_unrelated_asset_exactly(self):
        self._case("bank")


if __name__ == "__main__":
    unittest.main()
