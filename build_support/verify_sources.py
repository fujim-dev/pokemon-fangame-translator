from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parent.parent
python_files = [
    "Pokemon_Fangame_Translator.py",
    "translation_studio.py",
    "translation_project.py",
    "reconstruction_studio.py",
    "reconstruction_engine.py",
    "structured_extractor.py",
    "rpg_dialogue.py",
    "ruby_marshal_reader.py",
    "ruby_marshal_writer.py",
    "flux_archive.py",
    "flux_extractor.py",
    "flux_import_validator.py",
    "flux_import_plan.py",
    "flux_reinjection.py",
    "extraction_project.py",
    "project_identity.py",
    "safe_io.py",
    "adapters/__init__.py",
    "adapters/base.py",
    "adapters/pokemon_essentials.py",
    "adapters/pokemon_flux.py",
    "adapters/probe_isolation.py",
    "adapters/registry.py",
    "adapters/unknown.py",
    "analysis/__init__.py",
    "analysis/deep_analyzer.py",
    "analysis/flux_analyzer.py",
    "analysis/integrity.py",
    "analysis/language_coverage.py",
    "analysis/models.py",
    "analysis/report_writer.py",
    "repair/__init__.py",
    "repair/engine.py",
    "repair/models.py",
    "repair/planner.py",
    "repair/rollback.py",
    "repair/safe_fixes.py",
    "build_support/validate_private_flux_working_copy.py",
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
