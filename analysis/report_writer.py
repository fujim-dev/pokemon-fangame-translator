from __future__ import annotations

import json
from pathlib import Path

from .models import DeepAnalysisReport


DISCLAIMER = (
    "Cette validation analytique repose uniquement sur les données statiquement accessibles. "
    "L'aventure complète n'a pas été jouée physiquement de bout en bout."
)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _coverage_lines(report: DeepAnalysisReport) -> list[str]:
    coverage = report.coverage
    lines = [
        f"Textes classés : {coverage.total_lines}",
        f"Français probable : {coverage.line_counts['francais_probable']}",
        f"Anglais probable : {coverage.line_counts['anglais_probable']}",
        f"Mixtes : {coverage.line_counts['mixte']}",
        f"Ambigus ou noms courts : {coverage.line_counts['ambigu']}",
        f"Commandes techniques seules exclues : {coverage.line_counts['technique_exclu']}",
        f"Lignes contenant des commandes techniques protégées : {coverage.protected_command_lines}",
        f"Couverture française estimée des lignes classables : {coverage.french_line_percent:.2f}%",
        f"Couverture française estimée par mots classés : {coverage.french_word_percent:.2f}%",
        f"Couverture française estimée par caractères classés : {coverage.french_character_percent:.2f}%",
        f"Sources incomplètes ou non vérifiables : {'OUI' if coverage.incomplete_sources else 'NON'}",
        f"Méthode : {coverage.method}",
    ]
    if coverage.french_line_percent == 100.0 and coverage.incomplete_sources:
        lines.append(
            "Attention : 100 % des lignes classables ne signifie pas 100 % du jeu, "
            "car certaines sources n'ont pas pu être vérifiées."
        )
    return lines


def report_text(report: DeepAnalysisReport) -> str:
    issues = [
        f"[{issue.severity.upper()}] {issue.code} — "
        f"{issue.relative_path + ' — ' if issue.relative_path else ''}{issue.message}"
        for issue in report.issues
    ]
    return "\n".join(
        [
            "POKÉMON FANGAME TRANSLATOR — VALIDATION ANALYTIQUE APPROFONDIE",
            "=" * 82,
            f"Date : {report.analyzed_at}",
            f"Jeu : {report.game_label}",
            f"Adaptateur : {report.adapter_display_name} ({report.adapter_id})",
            f"Confiance de détection : {report.adapter_confidence}/100",
            f"Mode : {report.mode}",
            f"Statut analytique : {report.status.upper()}",
            "",
            DISCLAIMER,
            "Aucun script Ruby du jeu n'a été exécuté.",
            "",
            "INVENTAIRE ET PORTÉE",
            "-" * 82,
            f"Fichiers inventoriés : {report.files_seen}",
            f"Volume inventorié : {report.bytes_seen} octet(s)",
            f"Cartes relues : {report.maps_analyzed}/{report.map_files_found}",
            f"Événements de cartes : {report.map_events}",
            f"Pages d'événements : {report.map_pages}",
            f"Commandes d'événements : {report.event_commands}",
            f"Événements communs relus : {report.common_events_analyzed}/{report.common_events_found}",
            f"Banques de messages relues : {report.message_banks_analyzed}/{report.message_banks_found}",
            f"Fichiers PBS relus : {report.pbs_files_analyzed}/{report.pbs_files_found}",
            f"Fichiers PBS avec encodage historique détecté : {report.pbs_legacy_encoding_files}",
            f"Références statiques manquantes : {report.missing_static_references}",
            "",
            "COUVERTURE FRANÇAISE ESTIMÉE",
            "-" * 82,
            *_coverage_lines(report),
            "",
            "ÉLÉMENTS VÉRIFIÉS",
            "-" * 82,
            *(report.verified or ["Aucun élément compatible vérifiable."]),
            "",
            "ÉLÉMENTS NON VÉRIFIABLES STATIQUEMENT",
            "-" * 82,
            *(report.unverified or ["Aucun élément dynamique signalé."]),
            "",
            "FORMATS NON SUPPORTÉS",
            "-" * 82,
            *(report.unsupported or ["Aucun format non supporté détecté."]),
            "",
            "ALERTES",
            "-" * 82,
            *(issues or ["Aucune alerte analytique."]),
        ]
    )


def discord_summary(report: DeepAnalysisReport) -> str:
    coverage = report.coverage
    return "\n".join(
        [
            "**Pokémon Fangame Translator — validation analytique**",
            f"Jeu : {report.game_label}",
            f"Profil : {report.adapter_display_name} — confiance {report.adapter_confidence}/100",
            f"Statut : {report.status.upper()}",
            f"Cartes : {report.maps_analyzed}/{report.map_files_found} • Pages : {report.map_pages}",
            f"Couverture française estimée : {coverage.french_line_percent:.2f}% des lignes classables",
            f"Alertes : {len(report.issues)} • Références manquantes : {report.missing_static_references}",
            DISCLAIMER,
        ]
    )


def write_analysis_reports(
    report: DeepAnalysisReport,
    output_dir: Path,
    *,
    original_root: Path | None = None,
) -> dict[str, Path]:
    output_dir = output_dir.expanduser().resolve()
    if original_root is not None:
        resolved_original = original_root.expanduser().resolve()
        try:
            output_dir.relative_to(resolved_original)
        except ValueError:
            pass
        else:
            raise ValueError(
                "Les rapports d'analyse ne peuvent pas être écrits dans le fangame original."
            )
    txt_path = output_dir / "ANALYSE_APPROFONDIE.txt"
    json_path = output_dir / "ANALYSE_APPROFONDIE.json"
    discord_path = output_dir / "RESUME_DISCORD.txt"
    _atomic_write_text(txt_path, report_text(report))
    _atomic_write_text(
        json_path,
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
    )
    _atomic_write_text(discord_path, discord_summary(report))
    return {"text": txt_path, "json": json_path, "discord": discord_path}
