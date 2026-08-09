# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Segmentation statique et déterministe des dialogues RPG Maker XP 101/401.

Le séparateur historique du CSV (``\\n``) peut aussi être présent comme contrôle
à l'intérieur d'un paramètre de commande. Ce module conserve donc une preuve
structurelle séparée, sans texte, puis utilise l'ordre des contrôles protégés
pour retrouver les frontières exactes lors d'une validation privée.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from ruby_marshal_reader import RubyObject, RubyString
from ruby_marshal_writer import dumps


DIALOGUE_SEGMENTATION_FORMAT = "pft_rpg_dialogue_segments_v1"
DIALOGUE_BOUNDARY = r"\n"
_LINE_CONTROL_RE = re.compile(r"\\[nN]")


class DialogueSegmentationError(ValueError):
    """Une séquence 101/401 ne peut pas être segmentée sans approximation."""


@dataclass(frozen=True)
class DialogueCommandSegment:
    command_index: int
    command_code: int
    indent: int
    parameter_index: int
    parameter_count: int
    parameter_sha256: str
    command_sha256: str
    internal_line_control_count: int
    source_text: str

    def metadata_record(self) -> dict[str, int | str]:
        return {
            "command_code": self.command_code,
            "command_index": self.command_index,
            "command_sha256": self.command_sha256,
            "indent": self.indent,
            "internal_line_control_count": self.internal_line_control_count,
            "parameter_count": self.parameter_count,
            "parameter_index": self.parameter_index,
            "parameter_sha256": self.parameter_sha256,
        }


@dataclass(frozen=True)
class DialogueSegmentation:
    start_index: int
    end_index: int
    indent: int
    segments: tuple[DialogueCommandSegment, ...]

    @property
    def source_text(self) -> str:
        return DIALOGUE_BOUNDARY.join(segment.source_text for segment in self.segments)

    @property
    def metadata(self) -> str:
        payload = {
            "boundary": DIALOGUE_BOUNDARY,
            "end_index": self.end_index,
            "format": DIALOGUE_SEGMENTATION_FORMAT,
            "indent": self.indent,
            "segments": [segment.metadata_record() for segment in self.segments],
            "start_index": self.start_index,
        }
        return json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


def _segment_from_command(
    command: object,
    command_index: int,
    *,
    expected_code: int,
    expected_indent: int | None = None,
) -> DialogueCommandSegment:
    if not isinstance(command, RubyObject) or command.class_name != "RPG::EventCommand":
        raise DialogueSegmentationError(
            f"Commande RPG {command_index} non standard dans une séquence 101/401."
        )
    code = command.ivars.get("@code")
    indent = command.ivars.get("@indent")
    parameters = command.ivars.get("@parameters")
    if code != expected_code:
        raise DialogueSegmentationError(
            f"Code RPG inattendu à la commande {command_index}."
        )
    if not isinstance(indent, int):
        raise DialogueSegmentationError(
            f"Indentation RPG invalide à la commande {command_index}."
        )
    if expected_indent is not None and indent != expected_indent:
        raise DialogueSegmentationError(
            f"Indentation 401 incohérente à la commande {command_index}."
        )
    if (
        not isinstance(parameters, list)
        or not parameters
        or not isinstance(parameters[0], RubyString)
    ):
        raise DialogueSegmentationError(
            f"Paramètre textuel 101/401 invalide à la commande {command_index}."
        )
    source_text = parameters[0].text()
    return DialogueCommandSegment(
        command_index=command_index,
        command_code=code,
        indent=indent,
        parameter_index=0,
        parameter_count=len(parameters),
        parameter_sha256=hashlib.sha256(parameters[0].data).hexdigest(),
        command_sha256=hashlib.sha256(dumps(command)).hexdigest(),
        internal_line_control_count=len(_LINE_CONTROL_RE.findall(source_text)),
        source_text=source_text,
    )


def segment_dialogue_commands(
    commands: list,
    start_index: int,
) -> DialogueSegmentation:
    """Retourne la séquence contiguë 101/401 commençant à ``start_index``.

    Une continuation 401 de classe, d'indentation ou de paramètre inattendu rend
    toute la séquence invalide. Aucun texte n'est deviné ou ignoré.
    """
    if not isinstance(commands, list) or not (0 <= start_index < len(commands)):
        raise DialogueSegmentationError("Index de dialogue 101 invalide.")
    first = _segment_from_command(
        commands[start_index],
        start_index,
        expected_code=101,
    )
    segments = [first]
    cursor = start_index + 1
    while cursor < len(commands):
        candidate = commands[cursor]
        candidate_code = (
            candidate.ivars.get("@code") if isinstance(candidate, RubyObject) else None
        )
        if candidate_code != 401:
            break
        segments.append(
            _segment_from_command(
                candidate,
                cursor,
                expected_code=401,
                expected_indent=first.indent,
            )
        )
        cursor += 1
    return DialogueSegmentation(
        start_index=start_index,
        end_index=cursor - 1,
        indent=first.indent,
        segments=tuple(segments),
    )


def validate_dialogue_command_stream(commands: list) -> tuple[DialogueSegmentation, ...]:
    """Segmente tous les dialogues et refuse chaque continuation 401 orpheline."""
    if not isinstance(commands, list):
        raise DialogueSegmentationError("Liste de commandes RPG invalide.")
    result: list[DialogueSegmentation] = []
    index = 0
    while index < len(commands):
        command = commands[index]
        code = command.ivars.get("@code") if isinstance(command, RubyObject) else None
        if code == 101:
            segmentation = segment_dialogue_commands(commands, index)
            result.append(segmentation)
            index = segmentation.end_index + 1
            continue
        if code == 401:
            raise DialogueSegmentationError(
                f"Continuation 401 orpheline à la commande {index}."
            )
        index += 1
    return tuple(result)


def split_dialogue_translation(
    segmentation: DialogueSegmentation,
    translation: str,
) -> list[str]:
    """Répartit une traduction selon les frontières structurelles enregistrées.

    Les contrôles internes ``\\n`` et les frontières utilisent historiquement le
    même texte. Le nombre de contrôles internes de chaque segment détermine donc
    quel contrôle ordinal représente la frontière suivante.
    """
    controls = list(_LINE_CONTROL_RE.finditer(translation or ""))
    expected = sum(
        segment.internal_line_control_count for segment in segmentation.segments
    ) + max(0, len(segmentation.segments) - 1)
    if len(controls) != expected:
        raise DialogueSegmentationError(
            "La traduction ne conserve pas le nombre exact de contrôles \\n "
            "nécessaire à la segmentation 101/401."
        )

    pieces: list[str] = []
    text_start = 0
    control_cursor = 0
    for segment in segmentation.segments[:-1]:
        control_cursor += segment.internal_line_control_count
        boundary = controls[control_cursor]
        pieces.append(translation[text_start:boundary.start()])
        text_start = boundary.end()
        control_cursor += 1
    pieces.append(translation[text_start:])

    for segment, piece in zip(segmentation.segments, pieces):
        if len(_LINE_CONTROL_RE.findall(piece)) != segment.internal_line_control_count:
            raise DialogueSegmentationError(
                "Les frontières 101/401 de la traduction restent ambiguës."
            )
    return pieces
