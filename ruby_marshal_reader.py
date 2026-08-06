# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecteur minimal Ruby Marshal 4.8, suffisant pour les données RPG Maker XP.
Lecture seule : aucun support d'écriture volontairement dans la v0.7.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(eq=False)
class RubyString:
    data: bytes
    ivars: dict = field(default_factory=dict)

    def text(self) -> str:
        encodings: list[str] = []
        if self.ivars.get("E") is True:
            encodings.append("utf-8")
        encoding = self.ivars.get("encoding")
        if isinstance(encoding, RubyString):
            encodings.append(encoding.text())
        elif isinstance(encoding, str):
            encodings.append(encoding)
        encodings.extend(["utf-8", "cp1252", "latin-1"])
        for encoding_name in encodings:
            try:
                return self.data.decode(encoding_name)
            except (UnicodeDecodeError, LookupError):
                continue
        return self.data.decode("utf-8", errors="replace")

    def __str__(self) -> str:
        return self.text()

    def __repr__(self) -> str:
        return f"RubyString({self.text()!r})"

    def __hash__(self) -> int:
        return hash(self.data)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RubyString) and self.data == other.data


@dataclass
class RubyObject:
    class_name: str
    ivars: dict = field(default_factory=dict)


@dataclass
class RubyUserDefined:
    class_name: str
    data: bytes
    ivars: dict = field(default_factory=dict)


class MarshalReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.objects: list[object] = []
        self.symbols: list[str] = []

    def _byte(self) -> int:
        if self.pos >= len(self.data):
            raise EOFError("Fin inattendue du flux Ruby Marshal")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def _read(self, count: int) -> bytes:
        value = self.data[self.pos:self.pos + count]
        if len(value) != count:
            raise EOFError("Fin inattendue du flux Ruby Marshal")
        self.pos += count
        return value

    def _long(self) -> int:
        lead = self._byte()
        if lead >= 128:
            lead -= 256
        if lead == 0:
            return 0
        if 5 <= lead <= 127:
            return lead - 5
        if -128 <= lead <= -5:
            return lead + 5
        if 1 <= lead <= 4:
            value = 0
            for index in range(lead):
                value |= self._byte() << (8 * index)
            return value
        if -4 <= lead <= -1:
            value = -1
            for index in range(-lead):
                value &= ~(0xFF << (8 * index))
                value |= self._byte() << (8 * index)
            return value
        raise ValueError(f"Entier Ruby Marshal invalide : {lead}")

    def _raw_string(self) -> bytes:
        return self._read(self._long())

    def _register(self, value):
        self.objects.append(value)
        return value

    def _symbol(self) -> str:
        value = self.read_object()
        if not isinstance(value, str):
            raise TypeError(f"Symbole Ruby attendu, reçu : {value!r}")
        return value

    def read_object(self):
        marker = chr(self._byte())
        if marker == "0":
            return None
        if marker == "T":
            return True
        if marker == "F":
            return False
        if marker == "i":
            return self._long()
        if marker == ":":
            symbol = self._raw_string().decode("utf-8", errors="replace")
            self.symbols.append(symbol)
            return symbol
        if marker == ";":
            return self.symbols[self._long()]
        if marker == "@":
            return self.objects[self._long()]
        if marker == '"':
            return self._register(RubyString(self._raw_string()))
        if marker == "I":
            value = self.read_object()
            attributes = {}
            for _ in range(self._long()):
                key = self._symbol()
                attributes[key] = self.read_object()
            if hasattr(value, "ivars"):
                value.ivars.update(attributes)
            return value
        if marker == "[":
            result: list = []
            self._register(result)
            for _ in range(self._long()):
                result.append(self.read_object())
            return result
        if marker in ("{", "}"):
            result: dict = {}
            self._register(result)
            for _ in range(self._long()):
                key = self.read_object()
                result[key] = self.read_object()
            if marker == "}":
                result["__default__"] = self.read_object()
            return result
        if marker == "o":
            result = RubyObject(self._symbol())
            self._register(result)
            for _ in range(self._long()):
                key = self._symbol()
                result.ivars[key] = self.read_object()
            return result
        if marker == "u":
            class_name = self._symbol()
            result = RubyUserDefined(class_name, b"")
            self._register(result)
            result.data = self._raw_string()
            return result
        if marker == "U":
            class_name = self._symbol()
            result = RubyObject(class_name)
            self._register(result)
            result.ivars["__marshal__"] = self.read_object()
            return result
        if marker == "f":
            raw = self._raw_string().decode("ascii", errors="replace")
            return {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}.get(raw, float(raw))
        if marker == "l":
            sign = chr(self._byte())
            word_count = self._long()
            raw = self._read(word_count * 2)
            value = 0
            for index in range(word_count):
                value |= int.from_bytes(raw[index * 2:index * 2 + 2], "little") << (16 * index)
            return -value if sign == "-" else value
        if marker == "/":
            raw = self._raw_string()
            options = self._byte()
            return self._register(("regexp", raw, options))
        if marker in ("c", "m", "M"):
            return (marker, self._raw_string().decode("utf-8", errors="replace"))
        if marker == "e":
            self._symbol()
            return self.read_object()
        if marker == "C":
            self._symbol()
            return self.read_object()
        if marker == "S":
            result = RubyObject(self._symbol())
            self._register(result)
            for _ in range(self._long()):
                key = self._symbol()
                result.ivars[key] = self.read_object()
            return result
        raise NotImplementedError(
            f"Marqueur Ruby Marshal non pris en charge : {marker!r} à l'offset {self.pos - 1}"
        )


def load(path: str | Path):
    data = Path(path).read_bytes()
    if data[:2] != b"\x04\x08":
        raise ValueError("Ce fichier n'est pas un flux Ruby Marshal 4.8")
    reader = MarshalReader(data)
    reader.pos = 2
    return reader.read_object()


def as_text(value) -> str:
    if isinstance(value, RubyString):
        return value.text()
    if isinstance(value, str):
        return value
    return str(value)
