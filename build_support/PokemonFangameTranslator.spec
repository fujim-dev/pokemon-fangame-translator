# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "glossaire_v1.0.2.csv"), "."),
    (str(ROOT / "corrections_apprises_v1.0.2.csv"), "."),
    (str(ROOT / "README.txt"), "."),
    (str(ROOT / "GUIDE_DEMARRAGE_RAPIDE.txt"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "CHANGELOG.md"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ROOT / "THIRD_PARTY_PACKAGES.txt"), "."),
    (str(ROOT / "THIRD_PARTY_LICENSES"), "THIRD_PARTY_LICENSES"),
    (str(ROOT / "PRIVACY.md"), "."),
    (str(ROOT / "SECURITY.md"), "."),
]
binaries = []
hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
]

# Argos Translate et ses dépendances natives doivent être inclus dans le EXE.
for package_name in [
    "argostranslate",
    "ctranslate2",
    "sentencepiece",
    "stanza",
    "sacremoses",
    "networkx",
    "numpy",
    "requests",
    "certifi",
    "packaging",
]:
    try:
        package_datas, package_binaries, package_hidden = collect_all(package_name)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception:
        pass
    try:
        datas += copy_metadata(package_name)
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "Pokemon_Fangame_Translator.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PokemonFangameTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "build_support" / "installer" / "PokemonFangameTranslator.ico"),
    version=str(ROOT / "build_support" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PokemonFangameTranslator",
)
