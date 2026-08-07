from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from analysis import analyze_game, write_analysis_reports
from ruby_marshal_reader import RubyObject, RubyString
from ruby_marshal_writer import dumps


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def command(code: int, parameters: list) -> RubyObject:
    return RubyObject(
        "RPG::EventCommand",
        {"@code": code, "@parameters": parameters},
    )


def write_synthetic_map(path: Path) -> tuple[str, str]:
    french_text = "Bonjour, je suis heureux de vous rencontrer dans ce village."
    english_text = "Hello, I am happy to meet you in this village."
    missing_audio = RubyObject(
        "RPG::AudioFile",
        {"@name": ruby_text("missing_theme")},
    )
    first_page = RubyObject(
        "RPG::Event::Page",
        {
            "@list": [
                command(101, [ruby_text(french_text)]),
                command(241, [missing_audio]),
                command(355, [ruby_text("File.write('never-created.txt', 'bad')")]),
            ]
        },
    )
    conditional_page = RubyObject(
        "RPG::Event::Page",
        {
            "@condition": RubyObject("RPG::Event::Page::Condition", {}),
            "@list": [command(102, [[ruby_text(english_text)]])],
        },
    )
    event = RubyObject(
        "RPG::Event",
        {
            "@name": ruby_text("Synthetic event"),
            "@pages": [first_page, conditional_page],
        },
    )
    root = RubyObject("RPG::Map", {"@events": {1: event}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dumps(root))
    return french_text, english_text


class DeepAnalysisTests(unittest.TestCase):
    def test_unknown_profile_performs_inventory_without_interpreting_game_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_unknown_analysis_") as temp_dir:
            game = Path(temp_dir) / "UnknownGame"
            data = game / "Data"
            data.mkdir(parents=True)
            (data / "Map001.rxdata").write_bytes(b"invalid and intentionally unknown")

            report = analyze_game(
                game,
                adapter_id="unknown",
                adapter_display_name="Structure inconnue",
                adapter_confidence=25,
            )

            self.assertEqual(1, report.files_seen)
            self.assertEqual(0, report.map_files_found)
            self.assertFalse(any(issue.code == "unreadable_file" for issue in report.issues))
            self.assertTrue(any("Structure inconnue" in item for item in report.unsupported))

    def test_static_analysis_continues_after_invalid_file_and_never_executes_ruby(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pft_test_deep_") as temp_dir:
            base = Path(temp_dir)
            game = base / "SyntheticGame"
            french_text, english_text = write_synthetic_map(
                game / "Data" / "Map001.rxdata"
            )
            (game / "Game.exe").write_bytes(b"synthetic executable marker")
            (game / "Game.ini").write_text("[Game]\nLibrary=RGSS104E.dll\n", encoding="utf-8")
            (game / "Data" / "System.rxdata").write_bytes(b"synthetic database marker")
            (game / "Data" / "Map002.rxdata").write_bytes(b"not marshal")
            common_text = "Merci, nous sommes heureux de vous rencontrer ici."
            common_event = RubyObject(
                "RPG::CommonEvent",
                {"@list": [command(101, [ruby_text(common_text)])]},
            )
            (game / "Data" / "CommonEvents.rxdata").write_bytes(
                dumps([None, common_event])
            )
            scripts = game / "Scripts"
            scripts.mkdir()
            side_effect_marker = base / "never-created.txt"
            (scripts / "dangerous.rb").write_text(
                f"File.write('{side_effect_marker}', 'executed')",
                encoding="utf-8",
            )
            (game / "PBS").mkdir()
            (game / "PBS" / "pokemon.txt").write_text(
                "[TEST]\nDescription = Bonjour, bienvenue dans ce village avec nous.\n",
                encoding="utf-8",
            )
            (game / "PBS" / "items.txt").write_bytes(
                b"[ITEM]\nDescription = Caf\xe9 pr\xe9par\xe9 avec soin pour vous.\n"
            )

            report = analyze_game(
                game,
                adapter_id="pokemon_essentials",
                adapter_display_name="Pokémon Essentials classique",
                adapter_confidence=95,
            )
            paths = write_analysis_reports(report, base / "reports")

            self.assertFalse(side_effect_marker.exists())
            self.assertEqual(2, report.map_files_found)
            self.assertEqual(1, report.maps_analyzed)
            self.assertEqual(1, report.map_events)
            self.assertEqual(2, report.map_pages)
            self.assertEqual(1, report.common_events_found)
            self.assertEqual(1, report.common_events_analyzed)
            self.assertEqual(2, report.pbs_files_analyzed)
            self.assertEqual(1, report.pbs_legacy_encoding_files)
            self.assertEqual(1, report.dynamic_script_commands)
            self.assertEqual(1, report.ruby_script_files)
            self.assertEqual(1, report.missing_static_references)
            self.assertGreaterEqual(
                report.coverage.line_counts["francais_probable"],
                2,
            )
            self.assertGreaterEqual(report.coverage.line_counts["anglais_probable"], 1)
            self.assertTrue(report.coverage.incomplete_sources)
            self.assertFalse(report.coverage.can_claim_complete_coverage)

            unreadable = [issue for issue in report.issues if issue.code == "unreadable_file"]
            self.assertEqual(1, len(unreadable))
            self.assertEqual("Data/Map002.rxdata", unreadable[0].relative_path)
            missing = [
                issue for issue in report.issues
                if issue.code == "missing_static_reference"
            ]
            self.assertEqual("Audio/BGM/missing_theme", missing[0].relative_path)
            self.assertTrue(all(str(base) not in issue.relative_path for issue in report.issues))

            text_report = paths["text"].read_text(encoding="utf-8")
            self.assertIn("validation analytique", text_report.casefold())
            self.assertIn("n'a pas été jouée physiquement", text_report)
            self.assertIn("Aucun script Ruby du jeu n'a été exécuté", text_report)
            self.assertNotIn(str(base), text_report)
            self.assertNotIn(french_text, text_report)
            self.assertNotIn(english_text, text_report)
            self.assertNotIn(common_text, text_report)

            json_report = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertNotIn("game_root", json_report)
            self.assertEqual("SyntheticGame", json_report["game_label"])
            self.assertNotIn(french_text, json.dumps(json_report, ensure_ascii=False))

            with self.assertRaisesRegex(ValueError, "fangame original"):
                write_analysis_reports(report, game / "Rapports", original_root=game)
            self.assertFalse((game / "Rapports").exists())


if __name__ == "__main__":
    unittest.main()
