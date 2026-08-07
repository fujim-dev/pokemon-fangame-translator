from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parent.parent
python_files = [
    "Pokemon_Fangame_Translator.py",
    "translation_studio.py",
    "reconstruction_studio.py",
    "reconstruction_engine.py",
    "structured_extractor.py",
    "ruby_marshal_reader.py",
    "ruby_marshal_writer.py",
    "adapters/__init__.py",
    "adapters/base.py",
    "adapters/pokemon_essentials.py",
    "adapters/registry.py",
    "adapters/unknown.py",
    "analysis/__init__.py",
    "analysis/deep_analyzer.py",
    "analysis/integrity.py",
    "analysis/language_coverage.py",
    "analysis/models.py",
    "analysis/report_writer.py",
]
required_files = [
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "INSTALLATION_AVERTISSEMENT.txt",
]
for name in python_files:
    path = ROOT / name
    if not path.exists():
        raise SystemExit(f"Fichier manquant : {name}")
    py_compile.compile(str(path), doraise=True)
for name in required_files:
    if not (ROOT / name).exists():
        raise SystemExit(f"Document public manquant : {name}")
print("Sources et documents publics vérifiés.")
