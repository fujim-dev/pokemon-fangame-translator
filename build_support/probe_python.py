# -*- coding: utf-8 -*-
"""Sonde robuste utilisée par le créateur d'installateur Windows."""
from __future__ import annotations

import json
import platform
import struct
import sys
import traceback

result = {
    "ok": False,
    "executable": sys.executable,
    "version": platform.python_version(),
    "major": sys.version_info.major,
    "minor": sys.version_info.minor,
    "bits": struct.calcsize("P") * 8,
    "tkinter": False,
    "tk_version": None,
    "error": "",
}

try:
    import tkinter

    result["tkinter"] = True
    result["tk_version"] = str(tkinter.TkVersion)

    version_ok = (
        result["major"] == 3
        and 10 <= result["minor"] <= 13
    )
    result["ok"] = bool(
        version_ok
        and result["bits"] == 64
        and result["tkinter"]
    )
except Exception:
    result["error"] = traceback.format_exc(limit=2)

# Une seule ligne JSON, afin d'éviter les problèmes de guillemets et de retours
# à la ligne de PowerShell 5.1 avec l'option Python "-c".
print(json.dumps(result, ensure_ascii=True))
