from __future__ import annotations

import json
import unittest

from rpg_dialogue import (
    DialogueSegmentationError,
    segment_dialogue_commands,
    split_dialogue_translation,
    validate_dialogue_command_stream,
)
from ruby_marshal_reader import RubyObject, RubyString


def ruby_text(value: str) -> RubyString:
    return RubyString(value.encode("utf-8"), {"E": True})


def command(
    code: int,
    parameters: list,
    *,
    indent: int = 0,
    marker: str = "",
) -> RubyObject:
    ivars = {
        "@code": code,
        "@indent": indent,
        "@parameters": parameters,
    }
    if marker:
        ivars["@synthetic_marker"] = ruby_text(marker)
    return RubyObject("RPG::EventCommand", ivars)


class RpgDialogueSegmentationTests(unittest.TestCase):
    def test_multiline_dialogue_keeps_internal_controls_and_command_boundaries(self) -> None:
        commands = [
            command(108, [ruby_text("Synthetic neighbor before")]),
            command(101, [ruby_text(r"First \n internal")], marker="first"),
            command(401, [ruby_text("Second segment")], marker="second"),
            command(401, [ruby_text(r"Third \n internal")], marker="third"),
            command(102, [[ruby_text("Synthetic neighbor after")]]),
        ]

        segmentation = segment_dialogue_commands(commands, 1)

        self.assertEqual((1, 3), (segmentation.start_index, segmentation.end_index))
        self.assertEqual((101, 401, 401), tuple(
            segment.command_code for segment in segmentation.segments
        ))
        self.assertEqual((1, 0, 1), tuple(
            segment.internal_line_control_count for segment in segmentation.segments
        ))
        self.assertEqual(
            [r"Premier \n interne", "Deuxième segment", r"Troisième \n interne"],
            split_dialogue_translation(
                segmentation,
                r"Premier \n interne\nDeuxième segment\nTroisième \n interne",
            ),
        )
        metadata = json.loads(segmentation.metadata)
        self.assertEqual("pft_rpg_dialogue_segments_v1", metadata["format"])
        self.assertEqual([1, 2, 3], [item["command_index"] for item in metadata["segments"]])
        self.assertNotIn("First", segmentation.metadata)
        self.assertNotIn("Second", segmentation.metadata)
        self.assertNotIn("Third", segmentation.metadata)
        self.assertEqual(108, commands[0].ivars["@code"])
        self.assertEqual(102, commands[4].ivars["@code"])

    def test_equivalent_dialogues_produce_identical_metadata(self) -> None:
        def fixture() -> list[RubyObject]:
            return [
                command(101, [ruby_text(r"First \n internal")], indent=2),
                command(401, [ruby_text("Second")], indent=2),
            ]

        first = segment_dialogue_commands(fixture(), 0)
        second = segment_dialogue_commands(fixture(), 0)

        self.assertEqual(first.metadata, second.metadata)
        self.assertEqual(first.source_text, second.source_text)

    def test_neighboring_commands_define_independent_dialogue_boundaries(self) -> None:
        commands = [
            command(101, [ruby_text("First")]),
            command(401, [ruby_text("Continuation")]),
            command(108, [ruby_text("Comment between dialogues")]),
            command(101, [ruby_text("Second")]),
            command(0, []),
        ]

        segments = validate_dialogue_command_stream(commands)

        self.assertEqual([(0, 1), (3, 3)], [
            (segment.start_index, segment.end_index) for segment in segments
        ])

    def test_translation_with_missing_or_extra_line_control_is_refused(self) -> None:
        segmentation = segment_dialogue_commands(
            [
                command(101, [ruby_text(r"First \n internal")]),
                command(401, [ruby_text("Second")]),
            ],
            0,
        )

        for translation in (
            r"Premier sans contrôle\nDeuxième",
            r"Premier \n avec\ncontrôle ajouté\nDeuxième",
        ):
            with self.subTest(translation=translation), self.assertRaisesRegex(
                DialogueSegmentationError,
                "contrôles.*\\\\n",
            ):
                split_dialogue_translation(segmentation, translation)

    def test_mismatched_indent_invalid_parameters_and_orphan_401_are_refused(self) -> None:
        invalid_streams = (
            [
                command(101, [ruby_text("First")], indent=0),
                command(401, [ruby_text("Second")], indent=1),
            ],
            [
                command(101, [ruby_text("First")]),
                command(401, []),
            ],
            [command(401, [ruby_text("Orphan")])],
        )

        for commands in invalid_streams:
            with self.subTest(commands=commands), self.assertRaises(
                DialogueSegmentationError
            ):
                validate_dialogue_command_stream(commands)


if __name__ == "__main__":
    unittest.main()
