from __future__ import annotations

import unittest
from pathlib import Path


class SourceArchivePolicyTests(unittest.TestCase):
    def test_archive_is_built_from_clean_git_commit_with_forbidden_content_checks(self) -> None:
        root = Path(__file__).resolve().parent.parent
        script = (root / "build_support" / "Create-Source-Archive.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("status --porcelain", script)
        self.assertIn("ls-tree -r --name-only HEAD", script)
        self.assertIn("archive --format=zip", script)
        self.assertIn(".rxdata", script)
        self.assertIn("CSV non autorisé", script)
        self.assertNotIn("Copy-Item $_.FullName", script)
        self.assertNotIn("Compress-Archive", script)


if __name__ == "__main__":
    unittest.main()
