# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Écriture Ruby Marshal 4.8 pour les structures lues par ruby_marshal_reader.

Le module vise les données RPG Maker XP/Pokémon Essentials utilisées par le
traducteur. Il ne modifie jamais un fichier en place : l'appelant écrit dans un
fichier temporaire puis remplace uniquement la copie de travail.
"""
from __future__ import annotations

import math
from pathlib import Path

from ruby_marshal_reader import RubyHashKey, RubyObject, RubyString, RubyUserDefined, load
from safe_io import atomic_write_bytes


class MarshalWriter:
    def __init__(self):
        self.buffer = bytearray(b"\x04\x08")
        self.objects: dict[int, int] = {}
        self.symbols: dict[str, int] = {}

    @staticmethod
    def _long_bytes(value: int) -> bytes:
        if value == 0:
            return b"\x00"
        if 0 < value < 123:
            return bytes([value + 5])
        if -124 < value < 0:
            return bytes([(value - 5) & 0xFF])

        if value > 0:
            raw = bytearray()
            current = value
            while current:
                raw.append(current & 0xFF)
                current >>= 8
            if len(raw) > 4:
                raise OverflowError("Entier trop grand pour le format Ruby Marshal compact")
            return bytes([len(raw)]) + bytes(raw)

        # Complément à deux little-endian, sur le minimum d'octets nécessaire.
        for length in range(1, 5):
            minimum = -(1 << (8 * length - 1))
            maximum = (1 << (8 * length - 1)) - 1
            if minimum <= value <= maximum:
                raw = value.to_bytes(length, "little", signed=True)
                return bytes([(256 - length) & 0xFF]) + raw
        raise OverflowError("Entier négatif trop grand pour Ruby Marshal")

    def _long(self, value: int) -> None:
        self.buffer.extend(self._long_bytes(int(value)))

    def _raw_string(self, raw: bytes) -> None:
        self._long(len(raw))
        self.buffer.extend(raw)

    def _symbol(self, value: str) -> None:
        if value in self.symbols:
            self.buffer.extend(b";")
            self._long(self.symbols[value])
            return
        index = len(self.symbols)
        self.symbols[value] = index
        self.buffer.extend(b":")
        self._raw_string(value.encode("utf-8"))

    def _object_link(self, value) -> bool:
        object_id = id(value)
        index = self.objects.get(object_id)
        if index is None:
            self.objects[object_id] = len(self.objects)
            return False
        self.buffer.extend(b"@")
        self._long(index)
        return True

    def _write_ivars(self, ivars: dict) -> None:
        self._long(len(ivars))
        for key, child in ivars.items():
            self._symbol(str(key))
            self.write_object(child)

    def _write_string_core(self, value: RubyString) -> None:
        if self._object_link(value):
            return
        self.buffer.extend(b'"')
        self._raw_string(value.data)

    def _write_ruby_string(self, value: RubyString) -> None:
        if value.ivars:
            # Le lien d'objet est enregistré par le cœur chaîne, après le marqueur I.
            existing = self.objects.get(id(value))
            if existing is not None:
                self.buffer.extend(b"@")
                self._long(existing)
                return
            self.buffer.extend(b"I")
            self._write_string_core(value)
            self._write_ivars(value.ivars)
        else:
            self._write_string_core(value)

    def _write_user_defined_core(self, value: RubyUserDefined) -> None:
        if self._object_link(value):
            return
        self.buffer.extend(b"u")
        self._symbol(value.class_name)
        self._raw_string(value.data)

    def _write_user_defined(self, value: RubyUserDefined) -> None:
        if value.ivars:
            existing = self.objects.get(id(value))
            if existing is not None:
                self.buffer.extend(b"@")
                self._long(existing)
                return
            self.buffer.extend(b"I")
            self._write_user_defined_core(value)
            self._write_ivars(value.ivars)
        else:
            self._write_user_defined_core(value)

    def write_object(self, value) -> None:
        if value is None:
            self.buffer.extend(b"0")
            return
        if value is True:
            self.buffer.extend(b"T")
            return
        if value is False:
            self.buffer.extend(b"F")
            return
        if isinstance(value, int):
            self.buffer.extend(b"i")
            self._long(value)
            return
        if isinstance(value, float):
            if self._object_link(value):
                return
            self.buffer.extend(b"f")
            if math.isnan(value):
                raw = b"nan"
            elif math.isinf(value):
                raw = b"inf" if value > 0 else b"-inf"
            else:
                raw = repr(value).encode("ascii")
            self._raw_string(raw)
            return
        if isinstance(value, RubyHashKey):
            self.write_object(value.value)
            return
        if isinstance(value, RubyString):
            self._write_ruby_string(value)
            return
        if isinstance(value, RubyUserDefined):
            self._write_user_defined(value)
            return
        if isinstance(value, RubyObject):
            if self._object_link(value):
                return
            self.buffer.extend(b"o")
            self._symbol(value.class_name)
            self._write_ivars(value.ivars)
            return
        if isinstance(value, list):
            if self._object_link(value):
                return
            self.buffer.extend(b"[")
            self._long(len(value))
            for child in value:
                self.write_object(child)
            return
        if isinstance(value, dict):
            if self._object_link(value):
                return
            has_default = "__default__" in value
            self.buffer.extend(b"}" if has_default else b"{")
            items = [(key, child) for key, child in value.items() if key != "__default__"]
            self._long(len(items))
            for key, child in items:
                self.write_object(key)
                self.write_object(child)
            if has_default:
                self.write_object(value.get("__default__"))
            return
        if isinstance(value, tuple) and len(value) == 3 and value[0] == "regexp":
            if self._object_link(value):
                return
            _, raw, options = value
            self.buffer.extend(b"/")
            self._raw_string(bytes(raw))
            self.buffer.append(int(options) & 0xFF)
            return
        if isinstance(value, str):
            # Les chaînes Python produites par le lecteur représentent des symboles.
            self._symbol(value)
            return
        raise TypeError(f"Type non pris en charge par Ruby Marshal : {type(value).__name__}")

    def dumps(self, value) -> bytes:
        self.write_object(value)
        return bytes(self.buffer)


def dumps(value) -> bytes:
    return MarshalWriter().dumps(value)


def dump(value, path: str | Path) -> None:
    atomic_write_bytes(Path(path), dumps(value), validator=load)
