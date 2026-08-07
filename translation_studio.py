# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import csv
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

EXPECTED_FIELDS = [
    "id_stable", "type", "fichier", "carte_id", "carte_nom",
    "evenement_id", "evenement_nom", "page", "commande", "sous_index",
    "texte_source", "traduction_fr", "codes_proteges", "statut",
]
EXTRA_FIELDS = ["niveau_relecture", "alertes_relecture", "groupe_doublon", "origine_traduction"]

PROTECTED_RE = re.compile(
    r"(\\(?:[Pp][Nn]|[Ss][Hh]|[Ww][Uu]|[NnLlGgBbRr])"
    r"|\\[A-Za-z]+\[[^\]]*\]"
    r"|\\[.!|^><]"
    r"|\\[0-9]+"
    r"|<[^>]+>"
    r"|\{\d+\}"
    r"|%\d*\$?[sSdDiIfF])"
)

DEFAULT_GLOSSARY = [
    ("Pokémon Fangame", "fangame Pokémon"),
    ("Pokemon Fangame", "fangame Pokémon"),
    ("Pokémon Center", "Centre Pokémon"),
    ("Pokemon Center", "Centre Pokémon"),
    ("Pokémon League", "Ligue Pokémon"),
    ("Pokemon League", "Ligue Pokémon"),
    ("Gym Leader", "Champion d'Arène"),
    ("Elite Four", "Conseil 4"),
    ("Tech Demo", "Démo technique"),
    ("Technical Demo", "Démo technique"),
    ("Non-Profit use", "usage non commercial"),
    ("non-profit use", "usage non commercial"),
    ("Poké Ball", "Poké Ball"),
    ("Poké Mart", "Poké Mart"),
    ("Poke Ball", "Poké Ball"),
    ("Everdusk Co.", "Everdusk Co."),
    ("Fakemon", "Fakemon"),
    ("Discord", "Discord"),
    ("Pokemon", "Pokémon"),
    ("Pokémon", "Pokémon"),
    ("Gym", "Arène"),
    ("OST", "OST"),
]

POST_EDITS = [
    (r"\bPokemon\b", "Pokémon"),
    (r"\bFangame Pokémon\b", "fangame Pokémon"),
    (r"\bFangame Pokemon\b", "fangame Pokémon"),
    (r"\bC'est un fangame Pokémon\b", "Ceci est un fangame Pokémon"),
    (r"\bdestiné à usage non commercial\b", "destiné à un usage non commercial"),
    (r"\bdestinée à usage non commercial\b", "destinée à un usage non commercial"),
    (r"\bl'utilisation non-bénéfice\b", "un usage non commercial"),
    (r"\bl'utilisation à but non lucratif\b", "un usage non commercial"),
    (r"\bNous avons des plans pour faire\b", "Nous prévoyons de créer"),
    (r"\bNous avons des plans pour créer\b", "Nous prévoyons de créer"),
    (r"\b([1-9])ème Arène\b", r"\1e Arène"),
    (r"\b([1-9])ème Gymnase\b", r"\1e Arène"),
    (r"\bGymnase\b", "Arène"),
    (r"\bLa compagnie Everdusk\b", "Everdusk Co."),
    (r"\bcompagnie Everdusk\b", "Everdusk Co."),
    (r"\bQuelles sont vos pensées\s*\?", "Qu’en pensez-vous ?"),
    (r"\bGardez à l['’]esprit,? s['’]il vous plaît,?", "N’oubliez pas que"),
    (r"\bJe suis rejoint ici aujourd['’]hui par\b", "Je suis accompagné aujourd’hui par"),
    (r"\bmon nom est ([A-ZÀ-Ý][^.!?]+)", r"je m’appelle \1"),
]

COMMON_ENGLISH = {
    "the", "and", "you", "your", "this", "that", "with", "from", "will",
    "have", "has", "are", "was", "were", "cannot", "can't", "please",
    "welcome", "where", "what", "when", "why", "how", "there", "here",
    "would", "could", "should", "into", "about", "after", "before", "battle",
    "trainer", "item", "items", "move", "moves", "game", "new", "created",
}

STRONG_ENGLISH = {
    "please", "welcome", "created", "thoughts", "missing", "trainer",
    "battle", "item", "items", "move", "moves", "game", "thank",
    "hello", "sorry", "goodbye", "choose", "cancel",
}

# Mots réellement valables en français et en anglais. Ils ne doivent pas créer
# une alerte à eux seuls.
SHARED_FR_EN_WORDS = {
    "continue", "final", "important", "possible", "normal", "simple",
    "local", "menu", "radio", "route", "type", "zone", "bonus", "mobile",
}

# Termes de marque, noms propres et libellés Pokémon pouvant rester identiques.
ALLOWED_IDENTICAL_TERMS = {
    "poké mart", "discord", "ost", "fakemon", "everdusk co", "pokémon",
    "mythan", "serene", "linda", "lily", "marcus",
}

WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)

LITERAL_FRENCH_PATTERNS = [
    r"\bNous avons des plans pour\b",
    r"\butilisation non[- ]bénéfice\b",
    r"\bLa compagnie Everdusk\b",
    r"\bQuelles sont vos pensées\b",
    r"\bGardez à l['’]esprit,? s['’]il vous plaît\b",
    r"\bJe suis rejoint ici aujourd['’]hui par\b",
    r"\bmon nom est\b",
    r"\bsi vous plaît rapportez-le\b",
]


def extract_protected(text: str) -> list[str]:
    return PROTECTED_RE.findall(text or "")


def strip_protected(text: str) -> str:
    return PROTECTED_RE.sub(" ", text or "")


def split_protected(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    position = 0
    for match in PROTECTED_RE.finditer(text or ""):
        if match.start() > position:
            parts.append(("text", text[position:match.start()]))
        parts.append(("code", match.group(0)))
        position = match.end()
    if position < len(text or ""):
        parts.append(("text", text[position:]))
    return parts


def normalize_for_compare(text: str) -> str:
    plain = strip_protected(text).lower()
    plain = re.sub(r"[^a-zà-ÿ0-9]+", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def unicode_words(text: str) -> list[str]:
    """Découpe en mots Unicode complets (évite trainer dans entraîner)."""
    return [word.casefold() for word in WORD_RE.findall(strip_protected(text or ""))]


def identical_text_is_allowed(source: str, translation: str) -> bool:
    source_norm = normalize_for_compare(source)
    target_norm = normalize_for_compare(translation)
    if not source_norm or source_norm != target_norm:
        return False
    return source_norm in ALLOWED_IDENTICAL_TERMS


def duplicate_key(row: dict[str, str]) -> tuple[str, str]:
    return row.get("type", ""), row.get("texte_source", "")


def duplicate_group_id(row: dict[str, str]) -> str:
    import hashlib
    payload = "\x1f".join(duplicate_key(row)).encode("utf-8", errors="replace")
    return hashlib.sha1(payload).hexdigest()[:12]


def load_glossary(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                for row in reader:
                    source = (row.get("anglais") or "").strip()
                    target = (row.get("francais") or "").strip()
                    enabled = (row.get("actif") or "oui").strip().lower()
                    if source and target and enabled not in {"non", "0", "false"}:
                        entries.append((source, target))
        except Exception:
            entries = []
    return entries or list(DEFAULT_GLOSSARY)


def save_glossary(path: Path, entries: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["anglais", "francais", "actif"],
            delimiter=";",
        )
        writer.writeheader()
        for english, french in entries:
            writer.writerow({"anglais": english, "francais": french, "actif": "oui"})


def load_correction_memory(path: Path) -> dict[str, str]:
    memory: dict[str, str] = {}
    if not path.exists():
        return memory
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                source = (row.get("texte_source") or "").strip()
                translation = (row.get("traduction_fr") or "").strip()
                if source and translation:
                    memory[source] = translation
    except Exception:
        return {}
    return memory


def save_correction_memory(path: Path, memory: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["texte_source", "traduction_fr"],
            delimiter=";",
        )
        writer.writeheader()
        for source in sorted(memory, key=str.casefold):
            writer.writerow({"texte_source": source, "traduction_fr": memory[source]})


def apply_glossary_placeholders(source: str, glossary: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
    prepared = source or ""
    mapping: dict[str, str] = {}
    for index, (english, french) in enumerate(sorted(glossary, key=lambda item: len(item[0]), reverse=True)):
        token = f"<PFTGLOSS{index:03d}>"
        pattern = re.compile(rf"(?<!\w){re.escape(english)}(?!\w)", re.IGNORECASE)
        if pattern.search(prepared):
            prepared = pattern.sub(token, prepared)
            mapping[token] = french
    return prepared, mapping


def restore_glossary(text: str, mapping: dict[str, str]) -> str:
    restored = text
    for token, french in mapping.items():
        restored = restored.replace(token, french)
    return restored


def polish_french(text: str) -> str:
    polished = text
    for pattern, replacement in POST_EDITS:
        polished = re.sub(pattern, replacement, polished, flags=re.IGNORECASE)

    # Correction observée pendant les tests : Argos produit parfois
    # « Voici \c[1]Démo technique » au lieu de « Voici la ... ».
    polished = re.sub(
        r"\bVoici\s+(\\c\[[^\]]+\])?Démo technique\b",
        lambda match: "Voici la " + (match.group(1) or "") + "Démo technique",
        polished,
        flags=re.IGNORECASE,
    )
    polished = re.sub(r"[ \t]+([,.;:!?])", r"\1", polished)
    polished = re.sub(r" {2,}", " ", polished)
    return polished.strip()


def translate_preserving_codes(translator, source: str, glossary: list[tuple[str, str]] | None = None) -> str:
    original_codes = extract_protected(source)
    prepared, glossary_mapping = apply_glossary_placeholders(source, glossary or DEFAULT_GLOSSARY)
    output: list[str] = []

    for kind, value in split_protected(prepared):
        if kind == "code":
            output.append(value)
            continue
        if not re.search(r"[A-Za-z]", value):
            output.append(value)
            continue
        match = re.match(r"^(\s*)(.*?)(\s*)$", value, flags=re.DOTALL)
        if not match:
            output.append(value)
            continue
        leading, core, trailing = match.groups()
        if not core or not re.search(r"[A-Za-z]", core):
            output.append(value)
            continue
        output.append(leading + translator.translate(core) + trailing)

    translated = polish_french(restore_glossary("".join(output), glossary_mapping))
    if extract_protected(translated) != original_codes:
        raise RuntimeError("Une commande du jeu a changé pendant la traduction.")
    return translated



def protected_command_diff(source: str, translation: str) -> tuple[list[str], list[str], list[str], list[str]]:
    expected = extract_protected(source)
    found = extract_protected(translation)
    expected_counts = Counter(expected)
    found_counts = Counter(found)
    missing: list[str] = []
    extra: list[str] = []
    for command, count in expected_counts.items():
        missing.extend([command] * max(0, count - found_counts.get(command, 0)))
    for command, count in found_counts.items():
        extra.extend([command] * max(0, count - expected_counts.get(command, 0)))
    return expected, found, missing, extra


def restore_simple_commands(source: str, translation: str) -> tuple[str, list[str], bool]:
    """Répare uniquement les cas déterministes sans deviner la langue.

    - transforme de vrais retours à la ligne en commandes littérales ``\\n`` ;
    - restaure les commandes situées strictement au début ou à la fin du texte ;
    - refuse les commandes internes complexes, qui exigent une vérification humaine.
    """
    result = (translation or "").replace("\r\n", "\n").replace("\r", "\n")
    actions: list[str] = []
    expected, found, missing, extra = protected_command_diff(source, result)
    if expected == found:
        return result, ["Aucune commande manquante."], True

    expected_newlines = expected.count("\\n")
    found_newlines = found.count("\\n")
    needed_newlines = max(0, expected_newlines - found_newlines)
    if needed_newlines and "\n" in result:
        chunks = result.split("\n")
        replacements = min(needed_newlines, len(chunks) - 1)
        rebuilt = chunks[0]
        for index, chunk in enumerate(chunks[1:], start=1):
            rebuilt += ("\\n" if index <= replacements else "\n") + chunk
        result = rebuilt
        actions.append(f"{replacements} retour(s) à la ligne converti(s) en \\n.")

    expected, found, missing, extra = protected_command_diff(source, result)
    source_parts = split_protected(source)
    leading: list[str] = []
    trailing: list[str] = []
    seen_text = False
    for kind, value in source_parts:
        if kind == "text" and value:
            seen_text = True
        elif kind == "code" and not seen_text:
            leading.append(value)
    seen_text = False
    for kind, value in reversed(source_parts):
        if kind == "text" and value:
            seen_text = True
        elif kind == "code" and not seen_text:
            trailing.insert(0, value)

    missing_counts = Counter(missing)
    for command in leading:
        if missing_counts.get(command, 0) > 0:
            result = command + result
            missing_counts[command] -= 1
            actions.append(f"Commande de début restaurée : {command}")
    for command in reversed(trailing):
        if missing_counts.get(command, 0) > 0:
            result = result + command
            missing_counts[command] -= 1
            actions.append(f"Commande de fin restaurée : {command}")

    expected, found, missing, extra = protected_command_diff(source, result)
    success = expected == found
    if not success:
        if missing:
            actions.append("Commandes internes encore manquantes : " + ", ".join(missing))
        if extra:
            actions.append("Commandes en trop : " + ", ".join(extra))
        actions.append("Le logiciel refuse de deviner leur position. Compare les deux textes manuellement.")
    return result, actions, success

def review_translation(source: str, translation: str) -> tuple[str, list[str]]:
    """Retourne (niveau, alertes). Niveau : bloque, verifier ou pret."""
    alerts: list[str] = []
    source = source or ""
    translation = translation or ""

    if not translation.strip():
        return "bloque", ["Traduction vide"]

    if extract_protected(source) != extract_protected(translation):
        return "bloque", ["Commande du jeu modifiée"]

    source_plain = normalize_for_compare(source)
    target_plain = normalize_for_compare(translation)
    if source_plain and source_plain == target_plain and not identical_text_is_allowed(source, translation):
        alerts.append("Identique à l’anglais")

    if "<pftgloss" in translation.lower():
        alerts.append("Marqueur technique restant")

    src_len = max(1, len(source_plain))
    ratio = len(target_plain) / src_len
    if src_len >= 12 and ratio < 0.38:
        alerts.append("Traduction très courte")
    elif src_len >= 12 and ratio > 2.8:
        alerts.append("Traduction très longue")

    english_words = unicode_words(translation)
    leftovers = sorted({
        word for word in english_words
        if word in COMMON_ENGLISH and word not in SHARED_FR_EN_WORDS
    })
    strong_leftovers = sorted({
        word for word in english_words
        if word in STRONG_ENGLISH and word not in SHARED_FR_EN_WORDS
    })
    if strong_leftovers:
        alerts.append("Anglais probable : " + ", ".join(strong_leftovers[:4]))
    elif len(leftovers) >= 2:
        alerts.append("Anglais restant : " + ", ".join(leftovers[:4]))

    if re.search(r"\b([A-Za-zÀ-ÿ]{2,})(?:\s+\1){2,}\b", translation, re.IGNORECASE):
        alerts.append("Mot répété")

    if translation.count("(") != translation.count(")") or translation.count("[") != translation.count("]"):
        alerts.append("Parenthèse ou crochet incomplet")

    if source.rstrip().endswith("?") and not translation.rstrip().endswith("?"):
        alerts.append("Point d’interrogation manquant")
    if source.rstrip().endswith("!") and not translation.rstrip().endswith(("!", "…")):
        alerts.append("Ponctuation finale différente")

    source_lines = source.count("\\n") + source.count("\n")
    target_lines = translation.count("\\n") + translation.count("\n")
    if source_lines != target_lines:
        alerts.append("Nombre de retours à la ligne différent")

    source_numbers = re.findall(r"\d+(?:[.,]\d+)?", strip_protected(source))
    target_numbers = re.findall(r"\d+(?:[.,]\d+)?", strip_protected(translation))
    if source_numbers != target_numbers:
        alerts.append("Nombre ou numéro modifié")

    for protected_name in ("Everdusk Co.", "Discord", "Fakemon", "OST"):
        if protected_name.lower() in source.lower() and protected_name.lower() not in translation.lower():
            alerts.append(f"Nom propre modifié : {protected_name}")

    if any(re.search(pattern, translation, re.IGNORECASE) for pattern in LITERAL_FRENCH_PATTERNS):
        alerts.append("Formulation française probablement maladroite")

    # Une majuscule isolée après une commande peut signaler un article manquant,
    # mais les titres connus du glossaire sont volontairement exclus.
    if re.search(r"\b(?:Voici|Ceci est)\s+\\c\[[^\]]+\][A-ZÀ-Ý]", translation):
        if not re.search(r"Démo technique|Everdusk|Pokémon|OST", translation, re.IGNORECASE):
            alerts.append("Article français peut-être manquant")

    # Dédupliquer les alertes tout en conservant leur ordre.
    alerts = list(dict.fromkeys(alerts))
    return ("verifier", alerts) if alerts else ("pret", [])

def status_from_review(level: str) -> str:
    return {"bloque": "Bloqué", "verifier": "À vérifier", "pret": "Prêt"}.get(level, "À vérifier")


def reconciled_status(previous_status: str, level: str) -> str:
    """Conserve une décision humaine seulement si la sécurité reste valide."""
    if previous_status == "Ignoré":
        return "Ignoré"
    if level == "bloque":
        return "Bloqué"
    if previous_status == "Accepté":
        return "Accepté"
    return status_from_review(level)


def _version_key(value: object) -> tuple[int, ...]:
    numbers = tuple(int(part) for part in re.findall(r"\d+", str(value or "")))
    return numbers or (0,)


def install_argos_english_french_model(package_module):
    """Télécharge et installe le modèle officiel disponible le plus récent."""
    package_module.update_package_index()
    candidates = [
        package
        for package in package_module.get_available_packages()
        if getattr(package, "from_code", "") == "en"
        and getattr(package, "to_code", "") == "fr"
    ]
    if not candidates:
        raise RuntimeError("Aucun modèle Argos anglais → français n'est disponible.")
    model = max(
        candidates,
        key=lambda package: _version_key(getattr(package, "package_version", "0")),
    )
    downloaded_path = model.download()
    package_module.install_from_path(downloaded_path)
    return model


class TranslationStudio(tk.Toplevel):
    PAGE_SIZE = 180

    def __init__(self, master, csv_path: Path | None, colors: dict, logger=None):
        super().__init__(master)
        self.title("Relecture intelligente — Pokémon Fangame Translator v1.0.2")
        self.geometry("1420x900")
        self.minsize(1100, 740)
        self.configure(bg=colors["bg"])
        self.transient(master)

        self.base_dir = Path(__file__).resolve().parent
        self.colors = colors
        self.logger = logger or (lambda _message: None)
        self.initial_csv_path = Path(csv_path).resolve() if csv_path else None
        self.project_dir = self.initial_csv_path.parent if self.initial_csv_path else self.base_dir
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.glossary_path = self.project_dir / "glossaire.csv"
        self.corrections_path = self.project_dir / "corrections_apprises.csv"
        self.backup_dir = self.project_dir / "Sauvegardes"
        self.reports_dir = self.project_dir / "Rapports"
        self.resume_state_path = self.project_dir / "etat_traduction.json"
        self._seed_project_files()
        self.glossary = load_glossary(self.glossary_path)
        self.corrections = load_correction_memory(self.corrections_path)
        self._recover_previous_preferences()

        self.csv_path: Path | None = None
        self.fieldnames = list(EXPECTED_FIELDS) + list(EXTRA_FIELDS)
        self.rows: list[dict[str, str]] = []
        self.filtered_indices: list[int] = []
        self.page = 0
        self.current_index: int | None = None
        self.offline_running = False
        self.offline_stop = False
        self.argos_ready = False
        self.developer_visible = False
        self.sample_indices: list[int] = []
        self.sample_active = False
        self.last_batch_indices: list[int] = []
        self.last_batch_summary = "Aucun lot lancé."
        self.last_bulk_acceptance: list[tuple[int, str, str, str]] = []
        self.last_bulk_action = ""
        self.previous_import_checked = False
        self.resume_state: dict[str, object] = {}
        self.autosave_every = 10
        self.close_requested = False

        self._configure_styles()
        self._build_ui()

        if csv_path and Path(csv_path).exists():
            self.load_csv(Path(csv_path))
        else:
            self.after(150, self.open_csv)
        self.after(350, self.check_argos)

    def _seed_project_files(self):
        """Crée les fichiers de travail persistants sans écraser l'utilisateur."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        bundled_glossary = self.base_dir / "glossaire_v1.0.2.csv"
        bundled_corrections = self.base_dir / "corrections_apprises_v1.0.2.csv"
        if not self.glossary_path.exists():
            if bundled_glossary.exists():
                import shutil
                shutil.copy2(bundled_glossary, self.glossary_path)
            else:
                save_glossary(self.glossary_path, DEFAULT_GLOSSARY)
        if not self.corrections_path.exists():
            if bundled_corrections.exists():
                import shutil
                shutil.copy2(bundled_corrections, self.corrections_path)
            else:
                save_correction_memory(self.corrections_path, {})

    def _recover_previous_preferences(self):
        """Récupère silencieusement glossaire et corrections exactes des anciennes v0.8.x."""
        parent = self.base_dir.parent
        current_glossary = self.glossary_path.resolve()
        current_corrections = self.corrections_path.resolve()

        merged_corrections = dict(self.corrections)
        for candidate in sorted(parent.glob("Pokemon_Fangame_Translator_v0.8*/corrections_apprises_v0.8*.csv")):
            try:
                if candidate.resolve() == current_corrections:
                    continue
            except OSError:
                continue
            for source, translation in load_correction_memory(candidate).items():
                merged_corrections.setdefault(source, translation)
        if merged_corrections != self.corrections:
            self.corrections = merged_corrections
            save_correction_memory(self.corrections_path, self.corrections)

        merged_glossary = list(self.glossary)
        existing_sources = {source.casefold() for source, _target in merged_glossary}
        for candidate in sorted(parent.glob("Pokemon_Fangame_Translator_v0.8*/glossaire_v0.8*.csv")):
            try:
                if candidate.resolve() == current_glossary:
                    continue
            except OSError:
                continue
            for source, target in load_glossary(candidate):
                if source.casefold() in existing_sources:
                    continue
                merged_glossary.append((source, target))
                existing_sources.add(source.casefold())
        if merged_glossary != self.glossary:
            self.glossary = merged_glossary
            save_glossary(self.glossary_path, self.glossary)

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Review.Treeview",
            background=self.colors["panel"],
            fieldbackground=self.colors["panel"],
            foreground=self.colors["text"],
            rowheight=32,
            bordercolor=self.colors["border"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Review.Treeview.Heading",
            background=self.colors["panel3"],
            foreground=self.colors["accent"],
            font=("Segoe UI Semibold", 9),
        )
        style.map(
            "Review.Treeview",
            background=[("selected", self.colors["accent_dark"])],
            foreground=[("selected", "white")],
        )
        style.configure(
            "Review.TCombobox",
            fieldbackground=self.colors["panel2"],
            background=self.colors["panel2"],
            foreground=self.colors["text"],
            arrowcolor=self.colors["accent"],
        )
        style.configure(
            "Review.Horizontal.TProgressbar",
            troughcolor=self.colors["panel3"],
            background=self.colors["purple"],
            lightcolor=self.colors["purple"],
            darkcolor=self.colors["purple"],
        )

    def _card(self, parent, accent=None, padx=14, pady=12):
        return tk.Frame(
            parent,
            bg=self.colors["panel"],
            highlightbackground=accent or self.colors["border"],
            highlightthickness=1,
            padx=padx,
            pady=pady,
        )

    def _button(self, parent, text, command, accent=None, large=False, width=None):
        accent = accent or self.colors["accent"]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.colors["panel2"],
            fg=self.colors["text"],
            activebackground=accent,
            activeforeground="#ffffff",
            highlightbackground=accent,
            highlightthickness=1,
            relief="flat",
            bd=0,
            padx=18 if large else 12,
            pady=11 if large else 8,
            cursor="hand2",
            font=("Segoe UI Semibold", 10 if large else 9),
        )
        if width:
            button.configure(width=width)
        return button

    def _build_ui(self):
        header = tk.Frame(self, bg=self.colors["bg"])
        header.pack(fill="x", padx=18, pady=(14, 8))

        title_box = tk.Frame(header, bg=self.colors["bg"])
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text="TRADUCTION ET RELECTURE",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI Black", 19),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Ton projet est sauvegardé automatiquement et reste disponible dans les prochaines versions.",
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))
        self.project_path_var = tk.StringVar(value=f"Projet : {self.project_dir}")
        tk.Label(
            title_box,
            textvariable=self.project_path_var,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 7),
        ).pack(anchor="w", pady=(2, 0))

        header_actions = tk.Frame(header, bg=self.colors["bg"])
        header_actions.pack(side="right")
        self._button(header_actions, "ENREGISTRER", self.save_csv, self.colors["success"]).pack(side="left")
        self.dev_button = self._button(
            header_actions,
            "MODE DÉVELOPPEUR",
            self.toggle_developer,
            self.colors["accent"],
        )
        self.dev_button.pack(side="left", padx=(8, 0))

        notice = tk.Frame(self, bg="#211b11", highlightbackground="#6f5726", highlightthickness=1, padx=12, pady=8)
        notice.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(notice, text="Traduction automatique : relisez au minimum les textes signalés et quelques exemples au hasard.",
                 bg="#211b11", fg="#ded4bd", font=("Segoe UI", 8), anchor="w").pack(fill="x")

        steps = tk.Frame(self, bg=self.colors["bg"])
        steps.pack(fill="x", padx=18, pady=(0, 8))
        for number, title, detail, accent in [
            ("1", "TRADUIRE", "Choisis librement la taille du lot", self.colors["purple"]),
            ("2", "VÉRIFIER", "Alertes + 20 exemples au hasard", self.colors["orange"]),
            ("3", "ACCEPTER", "Valide les textes prêts en lot", self.colors["success"]),
        ]:
            card = self._card(steps, accent, padx=12, pady=8)
            card.pack(side="left", fill="x", expand=True, padx=(0, 8))
            tk.Label(card, text=number, bg=self.colors["panel"], fg=accent, font=("Segoe UI Black", 15)).pack(side="left", padx=(0, 9))
            labels = tk.Frame(card, bg=self.colors["panel"])
            labels.pack(side="left")
            tk.Label(labels, text=title, bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI Semibold", 9)).pack(anchor="w")
            tk.Label(labels, text=detail, bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")

        quick = self._card(self, self.colors["purple"], padx=14, pady=10)
        quick.pack(fill="x", padx=18)

        quick_left = tk.Frame(quick, bg=self.colors["panel"])
        quick_left.pack(side="left", fill="x", expand=True)
        tk.Label(
            quick_left,
            text="Traduire les dialogues",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 11),
        ).pack(anchor="w")
        tk.Label(
            quick_left,
            text="Les phrases identiques sont traduites une seule fois puis recopiées automatiquement.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(2, 0))

        quick_right = tk.Frame(quick, bg=self.colors["panel"])
        quick_right.pack(side="right")
        self.argos_status_var = tk.StringVar(value="Vérification de la traduction hors ligne…")
        self.argos_status_label = tk.Label(
            quick_right,
            textvariable=self.argos_status_var,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        )
        self.argos_status_label.grid(row=0, column=0, columnspan=8, sticky="e", pady=(0, 5))

        tk.Label(quick_right, text="Nombre", bg=self.colors["panel"], fg=self.colors["muted"]).grid(row=1, column=0, padx=(0, 6))
        self.batch_var = tk.StringVar(value="100")
        self.batch_entry = tk.Entry(
            quick_right,
            textvariable=self.batch_var,
            bg=self.colors["panel2"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            width=8,
            justify="center",
            font=("Segoe UI Semibold", 10),
        )
        self.batch_entry.grid(row=1, column=1, padx=(0, 6), ipady=7)
        self.batch_entry.bind("<KeyRelease>", lambda _event: self._update_batch_info())

        for column, label, value in [
            (2, "20", "20"),
            (3, "100", "100"),
            (4, "500", "500"),
            (5, "TOUT", "Tout"),
        ]:
            self._button(
                quick_right,
                label,
                lambda selected=value: self._set_batch(selected),
                self.colors["accent"],
            ).grid(row=1, column=column, padx=(0, 5))

        self.prepare_btn = self._button(quick_right, "PRÉPARER", self.prepare_argos, self.colors["orange"])
        self.prepare_btn.grid(row=1, column=6, padx=(3, 8))
        self.translate_btn = self._button(quick_right, "TRADUIRE 100", self.start_translation, self.colors["purple"], large=True)
        self.translate_btn.grid(row=1, column=7)

        self.batch_info_var = tk.StringVar(value="Calcul du nombre restant…")
        tk.Label(
            quick_right,
            textvariable=self.batch_info_var,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        ).grid(row=2, column=0, columnspan=8, sticky="e", pady=(5, 0))

        summary = tk.Frame(self, bg=self.colors["bg"])
        summary.pack(fill="x", padx=18, pady=(8, 0))
        self.stats_var = tk.StringVar(value="Aucun texte chargé")
        tk.Label(summary, textvariable=self.stats_var, bg=self.colors["bg"], fg=self.colors["accent2"], font=("Segoe UI Semibold", 9)).pack(side="left")

        views = tk.Frame(summary, bg=self.colors["bg"])
        views.pack(side="right")
        self.view_var = tk.StringVar(value="À vérifier")
        for label, value in [("À VÉRIFIER", "À vérifier"), ("ÉCHANTILLON", "Échantillon"), ("PRÊTS", "Prêts"), ("ACCEPTÉS", "Acceptés"), ("NON TRADUITS", "Non traduits"), ("TOUS", "Tous")]:
            tk.Radiobutton(
                views,
                text=label,
                value=value,
                variable=self.view_var,
                command=self.apply_filters,
                indicatoron=False,
                bg=self.colors["panel2"],
                fg=self.colors["text"],
                selectcolor=self.colors["accent_dark"],
                activebackground=self.colors["accent_dark"],
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                padx=11,
                pady=6,
                font=("Segoe UI Semibold", 8),
            ).pack(side="left", padx=(5, 0))

        review_actions = self._card(self, self.colors["orange"], padx=12, pady=8)
        review_actions.pack(fill="x", padx=18, pady=(8, 0))
        tk.Label(
            review_actions,
            text="Relecture rapide",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        tk.Label(
            review_actions,
            text="Contrôle les alertes, puis quelques exemples au hasard.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(10, 0))
        self._button(review_actions, "VOIR LES ALERTES", self.show_flagged, self.colors["orange"]).pack(side="right")
        self._button(review_actions, "TESTER 20 AU HASARD", self.show_random_sample, self.colors["accent"]).pack(side="right", padx=(0, 7))
        self._button(review_actions, "ACCEPTER TOUS LES PRÊTS", self.accept_all_ready, self.colors["success"]).pack(side="right", padx=(0, 7))
        self.resume_button = self._button(review_actions, "REPRENDRE LE LOT", self.resume_last_batch, self.colors["purple"])
        self.resume_button.pack(side="right", padx=(0, 7))
        self.resume_button.pack_forget()
        self.undo_button = self._button(review_actions, "ANNULER LE DERNIER LOT", self.undo_last_bulk_acceptance, self.colors["pink"])
        self.undo_button.pack(side="right", padx=(0, 7))

        self.last_batch_summary_var = tk.StringVar(value="Dernier lot : aucun.")
        batch_summary = self._card(self, self.colors["border"], padx=12, pady=6)
        batch_summary.pack(fill="x", padx=18, pady=(6, 0))
        tk.Label(
            batch_summary,
            textvariable=self.last_batch_summary_var,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        # Panneau développeur masqué par défaut.
        self.developer_panel = self._card(self, self.colors["accent"], padx=12, pady=9)
        dev_title = tk.Frame(self.developer_panel, bg=self.colors["panel"])
        dev_title.pack(fill="x")
        tk.Label(
            dev_title,
            text="⚙ MODE DÉVELOPPEUR — outils avancés",
            bg=self.colors["panel"],
            fg=self.colors["accent"],
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        tk.Label(
            dev_title,
            text="Réservé aux tests et au contrôle détaillé.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(10, 0))

        dev_row1 = tk.Frame(self.developer_panel, bg=self.colors["panel"])
        dev_row1.pack(fill="x", pady=(8, 0))
        self._button(dev_row1, "OUVRIR UN CSV", self.open_csv).pack(side="left")
        self._button(dev_row1, "ENREGISTRER SOUS", self.save_csv_as).pack(side="left", padx=(6, 0))
        self._button(dev_row1, "RECALCULER LES ALERTES", self.recalculate_all_reviews, self.colors["orange"]).pack(side="left", padx=(6, 0))
        self._button(dev_row1, "ACCEPTER LES PRÊTS AFFICHÉS", self.accept_ready_displayed, self.colors["success"]).pack(side="left", padx=(6, 0))
        self._button(dev_row1, "REMETTRE À VÉRIFIER", self.mark_displayed_for_review, self.colors["pink"]).pack(side="left", padx=(6, 0))
        self._button(dev_row1, "EXPORTER LES ALERTES", self.export_flagged, self.colors["orange"]).pack(side="left", padx=(6, 0))
        self._button(dev_row1, "IMPORTER UN ANCIEN PROJET", lambda: self.import_previous_project(manual=True), self.colors["purple"]).pack(side="left", padx=(6, 0))

        dev_row2 = tk.Frame(self.developer_panel, bg=self.colors["panel"])
        dev_row2.pack(fill="x", pady=(8, 0))
        tk.Label(dev_row2, text="Recherche", bg=self.colors["panel"], fg=self.colors["muted"]).pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(dev_row2, textvariable=self.search_var, bg=self.colors["panel2"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", width=22)
        search_entry.pack(side="left", padx=(6, 10), ipady=5)
        search_entry.bind("<KeyRelease>", lambda _event: self.apply_filters())

        tk.Label(dev_row2, text="Type", bg=self.colors["panel"], fg=self.colors["muted"]).pack(side="left")
        self.type_var = tk.StringVar(value="Dialogues et choix")
        self.type_combo = ttk.Combobox(dev_row2, textvariable=self.type_var, state="readonly", style="Review.TCombobox", width=21)
        self.type_combo.pack(side="left", padx=(6, 10))
        self.type_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_filters())

        self.protected_only_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dev_row2,
            text="Avec commandes uniquement",
            variable=self.protected_only_var,
            command=self.apply_filters,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            activebackground=self.colors["panel"],
            activeforeground=self.colors["text"],
            selectcolor=self.colors["panel2"],
        ).pack(side="left")

        self.translate_all_types_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            dev_row2,
            text="Inclure banques et PBS",
            variable=self.translate_all_types_var,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            activebackground=self.colors["panel"],
            activeforeground=self.colors["text"],
            selectcolor=self.colors["panel2"],
        ).pack(side="left", padx=(12, 0))

        self.propagate_duplicates_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            dev_row2,
            text="Propager les doublons",
            variable=self.propagate_duplicates_var,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            activebackground=self.colors["panel"],
            activeforeground=self.colors["text"],
            selectcolor=self.colors["panel2"],
        ).pack(side="left", padx=(12, 0))

        dev_row3 = tk.Frame(self.developer_panel, bg=self.colors["panel"])
        dev_row3.pack(fill="x", pady=(8, 0))
        self._button(dev_row3, "OUVRIR LE GLOSSAIRE", self.open_glossary, self.colors["purple"]).pack(side="left")
        self._button(dev_row3, "RECHARGER LE GLOSSAIRE", self.reload_glossary, self.colors["purple"]).pack(side="left", padx=(6, 0))
        self._button(dev_row3, "CORRECTIONS APPRISES", self.open_corrections, self.colors["accent"]).pack(side="left", padx=(6, 0))
        self._button(dev_row3, "OUVRIR LES RAPPORTS", lambda: self.open_folder(self.reports_dir)).pack(side="left", padx=(6, 0))
        self._button(dev_row3, "OUVRIR LES SAUVEGARDES", lambda: self.open_folder(self.backup_dir)).pack(side="left", padx=(6, 0))
        self.stop_btn = self._button(dev_row3, "ARRÊTER", self.stop_translation, self.colors["danger"])
        self.stop_btn.pack(side="right")

        table_card = self._card(self, self.colors["border"], padx=8, pady=8)
        table_card.pack(fill="both", expand=True, padx=18, pady=(8, 0))
        self.table_card = table_card

        columns = ("source", "traduction", "alerte", "etat")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings", style="Review.Treeview", selectmode="extended")
        for column, title, width in [
            ("source", "Texte anglais", 470),
            ("traduction", "Traduction française", 470),
            ("alerte", "À surveiller", 285),
            ("etat", "État", 95),
        ]:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=80)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        scrollbar = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        page_line = tk.Frame(self, bg=self.colors["bg"])
        page_line.pack(fill="x", padx=18, pady=(4, 0))
        self.page_var = tk.StringVar(value="Page 0/0")
        tk.Label(page_line, textvariable=self.page_var, bg=self.colors["bg"], fg=self.colors["muted"]).pack(side="right", padx=8)
        self._button(page_line, "›", lambda: self.change_page(1), width=3).pack(side="right")
        self._button(page_line, "‹", lambda: self.change_page(-1), width=3).pack(side="right", padx=(0, 5))

        editor = self._card(self, self.colors["purple"], padx=12, pady=9)
        editor.pack(fill="x", padx=18, pady=(6, 0))
        self.context_var = tk.StringVar(value="Clique sur une ligne signalée pour la vérifier.")
        tk.Label(editor, textvariable=self.context_var, bg=self.colors["panel"], fg=self.colors["accent"], font=("Segoe UI Semibold", 8)).pack(anchor="w")

        text_frame = tk.Frame(editor, bg=self.colors["panel"])
        text_frame.pack(fill="x", pady=(6, 0))
        text_frame.grid_columnconfigure(0, weight=1)
        text_frame.grid_columnconfigure(1, weight=1)

        self.source_text = tk.Text(text_frame, wrap="word", bg="#070a10", fg="#c9d3df", insertbackground="#ffffff", relief="flat", highlightbackground=self.colors["border"], highlightthickness=1, height=4, width=44, padx=9, pady=7, font=("Segoe UI", 10), state="disabled")
        self.source_text.grid(row=0, column=0, sticky="nsew")
        self.translation_text = tk.Text(text_frame, wrap="word", bg="#0d0a16", fg="#f2eaff", insertbackground="#ffffff", relief="flat", highlightbackground=self.colors["purple"], highlightthickness=1, height=4, width=44, padx=9, pady=7, font=("Segoe UI", 10))
        self.translation_text.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.translation_text.bind("<KeyRelease>", lambda _event: self.update_review_preview())

        commands_panel = tk.Frame(editor, bg=self.colors["panel2"], highlightbackground=self.colors["border"], highlightthickness=1, padx=9, pady=7)
        commands_panel.pack(fill="x", pady=(7, 0))
        tk.Label(commands_panel, text="Commandes du jeu à conserver", bg=self.colors["panel2"], fg=self.colors["muted"], font=("Segoe UI Semibold", 8)).pack(side="left")
        self.command_badges_frame = tk.Frame(commands_panel, bg=self.colors["panel2"])
        self.command_badges_frame.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.command_hint_var = tk.StringVar(value="Aucune commande sur cette ligne")
        tk.Label(commands_panel, textvariable=self.command_hint_var, bg=self.colors["panel2"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(side="right")

        actions = tk.Frame(editor, bg=self.colors["panel"])
        actions.pack(fill="x", pady=(7, 0))
        self._button(actions, "CORRIGER ET ACCEPTER", self.accept_current, self.colors["success"]).pack(side="left")
        self._button(actions, "GARDER TEL QUEL", self.keep_current, self.colors["accent"]).pack(side="left", padx=(7, 0))
        self._button(actions, "PASSER", self.select_next_row, self.colors["muted"]).pack(side="left", padx=(7, 0))
        self._button(actions, "RESTAURER LES COMMANDES", self.restore_current_commands, self.colors["orange"]).pack(side="left", padx=(7, 0))
        self.review_preview_var = tk.StringVar(value="Aucune analyse en cours")
        tk.Label(actions, textvariable=self.review_preview_var, bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(side="right")

        footer = self._card(self, self.colors["border"], padx=12, pady=7)
        footer.pack(fill="x", padx=18, pady=(7, 14))
        self.offline_progress = ttk.Progressbar(footer, maximum=100, style="Review.Horizontal.TProgressbar")
        self.offline_progress.pack(fill="x")
        self.offline_status_var = tk.StringVar(value="Prêt.")
        tk.Label(footer, textvariable=self.offline_status_var, bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Control-s>", lambda _event: self.save_csv())

    def _read_resume_state(self) -> dict[str, object]:
        try:
            if self.resume_state_path.exists():
                data = json.loads(self.resume_state_path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
        return {}

    def _write_resume_state(self, *, total: int, completed: int, remaining: int, active: bool):
        payload = {
            "version": "1.0",
            "active": bool(active),
            "total": int(total),
            "completed": int(completed),
            "remaining": int(max(0, remaining)),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "csv": str(self.csv_path or ""),
        }
        temp = self.resume_state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.resume_state_path)
        self.resume_state = payload

    def _refresh_resume_button(self):
        if not hasattr(self, "resume_button"):
            return
        state = self.resume_state or self._read_resume_state()
        remaining = int(state.get("remaining", 0) or 0)
        active = bool(state.get("active")) and remaining > 0
        if active:
            self.resume_button.configure(text=f"REPRENDRE ({remaining})")
            self.resume_button.pack(side="right", padx=(0, 7))
        else:
            self.resume_button.pack_forget()

    def resume_last_batch(self):
        state = self._read_resume_state()
        remaining = int(state.get("remaining", 0) or 0)
        if not state.get("active") or remaining <= 0:
            messagebox.showinfo("Aucun lot interrompu", "Aucun lot à reprendre n’a été trouvé.", parent=self)
            self.resume_state = {}
            self._refresh_resume_button()
            return
        self.batch_var.set(str(remaining))
        self._update_batch_info()
        if messagebox.askyesno(
            "Reprendre le lot",
            f"Reprendre les {remaining} texte(s) unique(s) encore non traduits ?\n\n"
            "Les textes déjà enregistrés ne seront pas retraduits.",
            parent=self,
        ):
            self.start_translation(skip_confirmation=True)

    def log(self, message: str):
        self.logger(f"Studio v1.0.2 : {message}")

    def toggle_developer(self):
        if not self.developer_visible:
            if not messagebox.askyesno(
                "Mode développeur",
                "Ces outils servent aux tests avancés et peuvent changer les filtres ou la portée de traduction.\n\nOuvrir quand même ?",
                parent=self,
            ):
                return
            self.developer_panel.pack(fill="x", padx=18, pady=(8, 0), before=self.table_card)
            self.developer_visible = True
            self.dev_button.configure(text="FERMER LE MODE DÉVELOPPEUR")
        else:
            self.developer_panel.pack_forget()
            self.developer_visible = False
            self.dev_button.configure(text="MODE DÉVELOPPEUR")

    def open_csv(self):
        chosen = filedialog.askopenfilename(parent=self, title="Ouvrir les textes extraits", filetypes=[("CSV Pokémon Fangame Translator", "*.csv"), ("Tous les fichiers", "*.*")])
        if chosen:
            self.load_csv(Path(chosen))

    def _previous_project_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        parent = self.base_dir.parent
        patterns = [
            "Pokemon_Fangame_Translator_v0.8*/Sortie_Extraction/textes_structures.csv",
            "Pokemon_Fangame_Translator_v0.8*/Sortie_Extraction/textes_structures_fr.csv",
        ]
        current = self.csv_path.resolve() if self.csv_path and self.csv_path.exists() else None
        for pattern in patterns:
            for candidate in parent.glob(pattern):
                try:
                    if current and candidate.resolve() == current:
                        continue
                except OSError:
                    pass
                candidates.append(candidate)
        unique = list(dict.fromkeys(candidates))
        return sorted(unique, key=lambda path: path.stat().st_mtime, reverse=True)

    def import_previous_project(self, manual: bool = False):
        candidates = self._previous_project_candidates()
        if not candidates:
            if manual:
                messagebox.showinfo("Aucun ancien projet", "Aucun CSV d’une ancienne version n’a été trouvé à côté de ce dossier.", parent=self)
            return 0

        previous = candidates[0]
        try:
            with previous.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                previous_rows = list(reader)
        except Exception as exc:
            if manual:
                messagebox.showerror("Import impossible", str(exc), parent=self)
            return 0

        translated_previous = [row for row in previous_rows if (row.get("traduction_fr") or "").strip()]
        if not translated_previous:
            if manual:
                messagebox.showinfo("Aucune traduction", "L’ancien projet trouvé ne contient aucune traduction.", parent=self)
            return 0

        current_translated = sum(1 for row in self.rows if row.get("traduction_fr", "").strip())
        if not manual and len(translated_previous) <= current_translated:
            return 0

        if not messagebox.askyesno(
            "Ancien travail détecté",
            f"Un ancien projet contient {len(translated_previous)} traduction(s) :\n\n{previous}\n\n"
            "Importer uniquement les traductions manquantes dans ce projet ?",
            parent=self,
        ):
            return 0

        by_id = {row.get("id_stable", ""): row for row in previous_rows if row.get("id_stable")}
        by_key = {(row.get("type", ""), row.get("texte_source", "")): row for row in previous_rows if row.get("texte_source")}
        imported = 0
        for row in self.rows:
            if row.get("traduction_fr", "").strip():
                continue
            source_by_id = by_id.get(row.get("id_stable", ""))
            if source_by_id and source_by_id.get("texte_source", "") == row.get("texte_source", ""):
                source_row = source_by_id
            else:
                source_row = by_key.get(duplicate_key(row))
            if not source_row:
                continue
            translation = (source_row.get("traduction_fr") or "").strip()
            if not translation:
                continue
            level, alerts = review_translation(row.get("texte_source", ""), translation)
            if level == "bloque":
                continue
            row["traduction_fr"] = translation
            row["niveau_relecture"] = source_row.get("niveau_relecture") or level
            row["alertes_relecture"] = source_row.get("alertes_relecture") or " | ".join(alerts)
            row["statut"] = source_row.get("statut") or status_from_review(level)
            row["origine_traduction"] = "import_ancienne_version"
            imported += 1

        if imported:
            self.save_csv(silent=True, save_editor=False)
            self.apply_filters()
            self.offline_status_var.set(f"{imported} traduction(s) récupérée(s) depuis une ancienne version.")
            messagebox.showinfo("Import terminé", f"{imported} traduction(s) ont été récupérées.", parent=self)
        elif manual:
            messagebox.showinfo("Rien à importer", "Aucune traduction manquante compatible n’a été trouvée.", parent=self)
        return imported

    def load_csv(self, path: Path):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                source_fields = list(reader.fieldnames or [])
                missing = [field for field in EXPECTED_FIELDS if field not in source_fields]
                if missing:
                    raise ValueError("Fichier incompatible : colonnes manquantes.")
                self.fieldnames = source_fields + [field for field in EXTRA_FIELDS if field not in source_fields]
                self.rows = []
                for raw in reader:
                    row = {field: raw.get(field, "") for field in self.fieldnames}
                    row["groupe_doublon"] = row.get("groupe_doublon") or duplicate_group_id(row)
                    translation = row.get("traduction_fr", "").strip()
                    if translation:
                        level, alerts = review_translation(row.get("texte_source", ""), translation)
                        row["niveau_relecture"] = level
                        row["alertes_relecture"] = " | ".join(alerts)
                        row["statut"] = reconciled_status(row.get("statut", ""), level)
                    else:
                        row["niveau_relecture"] = "non_traduit"
                        row["alertes_relecture"] = ""
                        if row.get("statut") != "Ignoré":
                            row["statut"] = "À traduire"
                    self.rows.append(row)
        except Exception as exc:
            messagebox.showerror("Ouverture impossible", str(exc), parent=self)
            return

        self.csv_path = path
        self.sample_indices = []
        self.sample_active = False
        types = sorted({row.get("type", "") for row in self.rows if row.get("type", "")})
        self.type_combo["values"] = list(dict.fromkeys(["Dialogues et choix", "Tous", "Banques de messages", "Tous les PBS"] + types))
        self.type_var.set("Dialogues et choix")
        flagged = sum(1 for row in self.rows if row.get("statut") in {"À vérifier", "Bloqué"})
        self.view_var.set("À vérifier" if flagged else "Non traduits")
        self.page = 0
        self.apply_filters()
        self._update_batch_info()
        self.log(f"Textes chargés : {len(self.rows)} lignes.")
        self.resume_state = self._read_resume_state()
        self._refresh_resume_button()
        if self.resume_state.get("active") and int(self.resume_state.get("remaining", 0) or 0) > 0:
            self.offline_status_var.set(
                f"Un lot interrompu peut être repris : {self.resume_state.get('remaining')} texte(s) restant(s)."
            )
        if not self.previous_import_checked:
            self.previous_import_checked = True
            self.after(250, lambda: self.import_previous_project(manual=False))

    def _row_matches_type(self, row: dict[str, str], selected: str) -> bool:
        row_type = row.get("type", "")
        if selected == "Tous":
            return True
        if selected == "Dialogues et choix":
            return row_type in {"Dialogue", "Choix"}
        if selected == "Banques de messages":
            return row_type == "Banque de messages"
        if selected == "Tous les PBS":
            return row_type.startswith("PBS —")
        return row_type == selected

    def apply_filters(self):
        if not self.rows:
            self.filtered_indices = []
            self.refresh_table()
            return
        query = self.search_var.get().strip().lower()
        selected_type = self.type_var.get()
        selected_view = self.view_var.get()
        protected_only = self.protected_only_var.get()
        matches: list[int] = []
        candidate_indices = self.sample_indices if selected_view == "Échantillon" else range(len(self.rows))
        for index in candidate_indices:
            row = self.rows[index]
            if not self._row_matches_type(row, selected_type):
                continue
            status = row.get("statut", "")
            translated = bool(row.get("traduction_fr", "").strip())
            if selected_view == "À vérifier" and status not in {"À vérifier", "Bloqué"}:
                continue
            if selected_view == "Prêts" and status != "Prêt":
                continue
            if selected_view == "Acceptés" and status != "Accepté":
                continue
            if selected_view == "Non traduits" and (translated or status == "Ignoré"):
                continue
            if protected_only and not row.get("codes_proteges", "").strip():
                continue
            if query:
                haystack = " ".join([row.get("texte_source", ""), row.get("traduction_fr", ""), row.get("fichier", ""), row.get("carte_nom", ""), row.get("evenement_nom", ""), row.get("type", ""), row.get("alertes_relecture", "")]).lower()
                if query not in haystack:
                    continue
            matches.append(index)
        self.filtered_indices = matches
        max_page = max(0, (len(matches) - 1) // self.PAGE_SIZE)
        self.page = min(self.page, max_page)
        self.refresh_table()

    @staticmethod
    def _short(text: str, limit: int = 115) -> str:
        value = (text or "").replace("\r", " ").replace("\n", " ").replace("\\n", " ↵ ")
        return value if len(value) <= limit else value[:limit - 1] + "…"

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        total = len(self.filtered_indices)
        pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page_var.set(f"Page {self.page + 1 if total else 0}/{pages if total else 0}")
        start = self.page * self.PAGE_SIZE
        end = min(total, start + self.PAGE_SIZE)
        for index in self.filtered_indices[start:end]:
            row = self.rows[index]
            self.tree.insert("", "end", iid=str(index), values=(
                self._short(row.get("texte_source", ""), 105),
                self._short(row.get("traduction_fr", ""), 105),
                self._short(row.get("alertes_relecture", "") or "Aucune alerte", 75),
                row.get("statut", ""),
            ))
        self.update_stats()

    def update_stats(self):
        total = len(self.rows)
        translated = sum(1 for row in self.rows if row.get("traduction_fr", "").strip())
        ready = sum(1 for row in self.rows if row.get("statut") == "Prêt")
        accepted = sum(1 for row in self.rows if row.get("statut") == "Accepté")
        flagged = sum(1 for row in self.rows if row.get("statut") in {"À vérifier", "Bloqué"})
        groups = len({row.get("groupe_doublon") for row in self.rows if row.get("groupe_doublon")})
        self.stats_var.set(
            f"PROJET COMPLET  •  {translated}/{total} traduits  •  {ready} prêts  •  "
            f"{accepted} acceptés  •  {flagged} à vérifier  •  {groups} groupes uniques"
        )
        if hasattr(self, "batch_info_var"):
            self._update_batch_info(update_button=False)

    def change_page(self, delta: int):
        pages = max(1, (len(self.filtered_indices) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = max(0, min(pages - 1, self.page + delta))
        self.refresh_table()

    def _context(self, row: dict[str, str]) -> str:
        bits = [row.get("type", "")]
        if row.get("carte_nom"):
            bits.append(f"Carte {row.get('carte_id')} — {row.get('carte_nom')}")
        elif row.get("fichier"):
            bits.append(row.get("fichier", ""))
        if row.get("evenement_nom"):
            bits.append(row.get("evenement_nom", ""))
        if row.get("page"):
            bits.append(f"Page {row.get('page')}")
        return " • ".join(bit for bit in bits if bit)

    def on_tree_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        self.current_index = index
        row = self.rows[index]
        self.context_var.set(self._context(row))
        self.source_text.configure(state="normal")
        self.source_text.delete("1.0", "end")
        self.source_text.insert("1.0", row.get("texte_source", ""))
        self.source_text.configure(state="disabled")
        self.translation_text.delete("1.0", "end")
        self.translation_text.insert("1.0", row.get("traduction_fr", ""))
        self.update_review_preview()

    def _render_command_badges(self, source: str, translation: str):
        for child in self.command_badges_frame.winfo_children():
            child.destroy()
        expected, _found, missing, extra = protected_command_diff(source, translation)
        if not expected:
            self.command_hint_var.set("Aucune commande sur cette ligne")
            return
        missing_counts = Counter(missing)
        seen = Counter()
        for command in expected:
            seen[command] += 1
            is_missing = seen[command] > (Counter(expected)[command] - missing_counts.get(command, 0))
            bg = "#3a171b" if is_missing else "#153124"
            fg = self.colors["danger"] if is_missing else self.colors["success"]
            label = tk.Label(
                self.command_badges_frame,
                text=command,
                bg=bg,
                fg=fg,
                padx=7,
                pady=3,
                font=("Cascadia Mono", 8),
            )
            label.pack(side="left", padx=(0, 5))
        if missing or extra:
            self.command_hint_var.set("Rouge = manquante • utilise Restaurer les commandes")
        else:
            self.command_hint_var.set("✓ Toutes les commandes sont conservées")

    def restore_current_commands(self):
        if self.current_index is None:
            messagebox.showinfo("Aucune ligne", "Clique d'abord sur une ligne.", parent=self)
            return
        source = self.rows[self.current_index].get("texte_source", "")
        current = self.translation_text.get("1.0", "end-1c")
        repaired, actions, success = restore_simple_commands(source, current)
        self.translation_text.delete("1.0", "end")
        self.translation_text.insert("1.0", repaired)
        self.update_review_preview()
        message = "\n".join(actions)
        if success:
            messagebox.showinfo("Commandes restaurées", message, parent=self)
        else:
            messagebox.showwarning(
                "Réparation partielle",
                message + "\n\nLa traduction reste bloquée tant que les commandes ne sont pas identiques.",
                parent=self,
            )

    def update_review_preview(self):
        if self.current_index is None:
            return
        source = self.rows[self.current_index].get("texte_source", "")
        translation = self.translation_text.get("1.0", "end-1c")
        self._render_command_badges(source, translation)
        level, alerts = review_translation(source, translation)
        if level == "bloque":
            self.review_preview_var.set("⛔ " + " • ".join(alerts) + " — bouton Restaurer les commandes disponible")
        elif alerts:
            self.review_preview_var.set("⚠ " + " • ".join(alerts))
        else:
            self.review_preview_var.set("✓ Aucune alerte technique")

    def _save_current_to_memory(self, accepted: bool) -> bool:
        if self.current_index is None:
            return False
        row = self.rows[self.current_index]
        translation = self.translation_text.get("1.0", "end-1c").strip()
        level, alerts = review_translation(row.get("texte_source", ""), translation)
        if level == "bloque":
            messagebox.showerror(
                "Traduction bloquée",
                "\n".join(alerts) +
                "\n\nLes éléments comme \\n, \\c[1], \\PN ou \\v[1] sont des commandes du jeu. "
                "Utilise « Restaurer les commandes » pour les cas simples.",
                parent=self,
            )
            return False
        row["traduction_fr"] = translation
        row["niveau_relecture"] = level
        row["alertes_relecture"] = " | ".join(alerts)
        row["statut"] = "Accepté" if accepted else status_from_review(level)
        return True

    def accept_current(self):
        if self.current_index is None:
            messagebox.showinfo("Aucune ligne", "Clique d'abord sur une ligne.", parent=self)
            return
        row = self.rows[self.current_index]
        source = row.get("texte_source", "")
        before = row.get("traduction_fr", "").strip()
        if not self._save_current_to_memory(accepted=True):
            return
        corrected = row.get("traduction_fr", "").strip()

        # Le bouton de correction mémorise une règle exacte. Elle ne sera
        # réutilisée que si le texte anglais est strictement identique.
        if source and corrected:
            self.corrections[source] = corrected
            save_correction_memory(self.corrections_path, self.corrections)
            propagated = 0
            for other in self.rows:
                if other is row or duplicate_key(other) != duplicate_key(row):
                    continue
                if other.get("traduction_fr", "").strip() != corrected or other.get("statut") != "Accepté":
                    other["traduction_fr"] = corrected
                    other["niveau_relecture"] = "pret"
                    other["alertes_relecture"] = ""
                    other["statut"] = "Accepté"
                    other["origine_traduction"] = "correction_apprise"
                    propagated += 1
            row["origine_traduction"] = "correction_apprise" if corrected != before else (row.get("origine_traduction") or "validation_humaine")
        else:
            propagated = 0

        self.save_csv(silent=True, save_editor=False)
        self.apply_filters()
        self.select_next_row()
        self.offline_status_var.set(
            f"Correction mémorisée et appliquée à {propagated} doublon(s)."
            if corrected != before else "Traduction acceptée et enregistrée."
        )

    def keep_current(self):
        if self.current_index is None:
            return
        if not self._save_current_to_memory(accepted=True):
            return
        self.save_csv(silent=True, save_editor=False)
        self.apply_filters()
        self.select_next_row()

    def select_next_row(self):
        children = list(self.tree.get_children())
        if not children:
            return
        current = str(self.current_index) if self.current_index is not None else None
        next_pos = min(children.index(current) + 1, len(children) - 1) if current in children else 0
        item = children[next_pos]
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)
        self.on_tree_select()

    def save_csv(self, silent: bool = False, save_editor: bool = True):
        if not self.csv_path:
            return self.save_csv_as()
        if save_editor and self.current_index is not None:
            if not self._save_current_to_memory(accepted=False):
                return False
        try:
            self._write_csv(self.csv_path)
        except Exception as exc:
            messagebox.showerror("Enregistrement impossible", str(exc), parent=self)
            return False
        if not silent:
            self.offline_status_var.set("Enregistré. Aucun fichier du jeu n'a été modifié.")
        self.log(f"CSV enregistré : {self.csv_path}")
        return True

    def save_csv_as(self):
        chosen = filedialog.asksaveasfilename(parent=self, title="Enregistrer les traductions", defaultextension=".csv", initialfile="textes_structures_fr.csv", filetypes=[("CSV", "*.csv")])
        if not chosen:
            return False
        self.csv_path = Path(chosen)
        return self.save_csv()

    def _write_csv(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames, delimiter=";")
            writer.writeheader()
            for row in self.rows:
                writer.writerow({field: row.get(field, "") for field in self.fieldnames})
        temp.replace(path)

    def show_flagged(self):
        self.sample_active = False
        self.view_var.set("À vérifier")
        self.page = 0
        self.apply_filters()
        if not self.filtered_indices:
            messagebox.showinfo("Aucune alerte", "Aucun texte n'est actuellement signalé.", parent=self)

    def show_random_sample(self, auto: bool = False):
        # Un seul représentant par groupe de doublons pour éviter de relire
        # vingt fois la même phrase.
        representatives: dict[str, int] = {}
        pool = self.last_batch_indices if auto and self.last_batch_indices else range(len(self.rows))
        for index in pool:
            row = self.rows[index]
            if row.get("statut") not in {"Prêt", "Accepté"}:
                continue
            group = row.get("groupe_doublon") or duplicate_group_id(row)
            representatives.setdefault(group, index)
        candidates = list(representatives.values())
        if not candidates:
            if not auto:
                messagebox.showinfo("Aucun texte prêt", "Traduis d'abord un lot de dialogues.", parent=self)
            return
        sample_size = min(20, len(candidates))
        self.sample_indices = random.sample(candidates, sample_size)
        self.sample_active = True
        self.view_var.set("Échantillon")
        self.page = 0
        self.apply_filters()
        self.offline_status_var.set(f"Échantillon aléatoire : {sample_size} texte(s) à contrôler.")
        if auto:
            self.offline_status_var.set(
                f"Aucune alerte détectée : {sample_size} exemple(s) du dernier lot sont affichés."
            )

    def _remember_bulk_acceptance(self, indices: list[int], action_name: str):
        self.last_bulk_acceptance = [
            (
                index,
                self.rows[index].get("statut", ""),
                self.rows[index].get("niveau_relecture", ""),
                self.rows[index].get("alertes_relecture", ""),
            )
            for index in indices
        ]
        self.last_bulk_action = action_name

    def undo_last_bulk_acceptance(self):
        if not self.last_bulk_acceptance:
            messagebox.showinfo("Rien à annuler", "Aucune acceptation en lot n’a été effectuée pendant cette session.", parent=self)
            return
        count = len(self.last_bulk_acceptance)
        if not messagebox.askyesno(
            "Annuler l’acceptation",
            f"Restaurer l’état précédent de {count} texte(s) ?",
            parent=self,
        ):
            return
        for index, status, level, alerts in self.last_bulk_acceptance:
            if 0 <= index < len(self.rows):
                self.rows[index]["statut"] = status
                self.rows[index]["niveau_relecture"] = level
                self.rows[index]["alertes_relecture"] = alerts
        action = self.last_bulk_action
        self.last_bulk_acceptance = []
        self.last_bulk_action = ""
        self.save_csv(silent=True, save_editor=False)
        self.apply_filters()
        self.offline_status_var.set(f"Acceptation annulée : {count} texte(s) restauré(s).")
        messagebox.showinfo("Annulation terminée", f"Le lot « {action} » a été annulé.", parent=self)

    def accept_all_ready(self):
        indices = [index for index, row in enumerate(self.rows) if row.get("statut") == "Prêt"]
        if not indices:
            messagebox.showinfo("Rien à accepter", "Aucun texte n’est actuellement classé « Prêt ».", parent=self)
            return
        if not messagebox.askyesno(
            "Accepter les textes prêts",
            f"Accepter {len(indices)} texte(s) sans alerte technique ?\n\n"
            "Conseil : contrôle d’abord les alertes et l’échantillon aléatoire.",
            parent=self,
        ):
            return
        self._remember_bulk_acceptance(indices, "Accepter tous les prêts")
        for index in indices:
            self.rows[index]["statut"] = "Accepté"
        self.save_csv(silent=True, save_editor=False)
        self.apply_filters()
        self.offline_status_var.set(f"{len(indices)} texte(s) prêts ont été acceptés en lot.")


    def recalculate_all_reviews(self):
        changed = 0
        for row in self.rows:
            translation = row.get("traduction_fr", "").strip()
            if not translation:
                continue
            level, alerts = review_translation(row.get("texte_source", ""), translation)
            row["niveau_relecture"] = level
            row["alertes_relecture"] = " | ".join(alerts)
            row["statut"] = reconciled_status(row.get("statut", ""), level)
            changed += 1
        self.save_csv(silent=True, save_editor=False)
        self.apply_filters()
        messagebox.showinfo("Analyse terminée", f"{changed} traduction(s) analysée(s).", parent=self)

    def _displayed_indices(self) -> list[int]:
        return [int(item) for item in self.tree.get_children()]

    def accept_ready_displayed(self):
        indices = [i for i in self._displayed_indices() if self.rows[i].get("statut") == "Prêt"]
        if not indices:
            messagebox.showinfo("Rien à accepter", "Aucun texte prêt sur cette page.", parent=self)
            return
        if not messagebox.askyesno("Accepter en lot", f"Accepter {len(indices)} texte(s) sans alerte technique ?", parent=self):
            return
        self._remember_bulk_acceptance(indices, "Accepter les prêts affichés")
        for index in indices:
            self.rows[index]["statut"] = "Accepté"
        self.save_csv(silent=True, save_editor=False)
        self.apply_filters()

    def mark_displayed_for_review(self):
        indices = [i for i in self._displayed_indices() if self.rows[i].get("traduction_fr", "").strip()]
        for index in indices:
            self.rows[index]["statut"] = "À vérifier"
            note = self.rows[index].get("alertes_relecture", "")
            self.rows[index]["alertes_relecture"] = note or "Relecture demandée manuellement"
        self.save_csv(silent=True, save_editor=False)
        self.apply_filters()

    def export_flagged(self):
        chosen = filedialog.asksaveasfilename(parent=self, title="Exporter les textes signalés", defaultextension=".csv", initialfile="textes_a_verifier.csv", filetypes=[("CSV", "*.csv")])
        if not chosen:
            return
        flagged = [row for row in self.rows if row.get("statut") in {"À vérifier", "Bloqué"}]
        with Path(chosen).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.fieldnames, delimiter=";")
            writer.writeheader()
            writer.writerows({field: row.get(field, "") for field in self.fieldnames} for row in flagged)
        messagebox.showinfo("Export terminé", f"{len(flagged)} texte(s) exporté(s).", parent=self)

    def open_glossary(self):
        try:
            if os.name == "nt":
                os.startfile(str(self.glossary_path))
            else:
                subprocess.Popen(["xdg-open", str(self.glossary_path)])
        except Exception as exc:
            messagebox.showerror("Ouverture impossible", str(exc), parent=self)

    def reload_glossary(self):
        self.glossary = load_glossary(self.glossary_path)
        messagebox.showinfo("Glossaire rechargé", f"{len(self.glossary)} terme(s) actif(s).", parent=self)

    def open_corrections(self):
        if not self.corrections_path.exists():
            save_correction_memory(self.corrections_path, self.corrections)
        try:
            if os.name == "nt":
                os.startfile(str(self.corrections_path))
            else:
                subprocess.Popen(["xdg-open", str(self.corrections_path)])
        except Exception as exc:
            messagebox.showerror("Ouverture impossible", str(exc), parent=self)

    def reload_corrections(self):
        self.corrections = load_correction_memory(self.corrections_path)
        messagebox.showinfo("Corrections rechargées", f"{len(self.corrections)} correction(s) mémorisée(s).", parent=self)

    def open_folder(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Ouverture impossible", str(exc), parent=self)

    @staticmethod
    def _import_argos():
        import argostranslate.package
        import argostranslate.translate
        return argostranslate.package, argostranslate.translate

    def check_argos(self) -> bool:
        try:
            _package, translate_module = self._import_argos()
            languages = translate_module.get_installed_languages()
            english = next((lang for lang in languages if lang.code == "en"), None)
            french = next((lang for lang in languages if lang.code == "fr"), None)
            if english and french:
                english.get_translation(french)
                self.argos_ready = True
                self.argos_status_var.set("✓ Traduction hors ligne prête")
                self.argos_status_label.configure(fg=self.colors["success"])
                self.prepare_btn.grid_remove()
                return True
        except Exception:
            pass
        self.argos_ready = False
        self.argos_status_var.set("Traduction hors ligne à préparer une seule fois")
        self.argos_status_label.configure(fg=self.colors["orange"])
        self.prepare_btn.grid()
        return False

    def prepare_argos(self):
        if self.offline_running:
            return
        if not messagebox.askyesno("Préparer la traduction", "Le logiciel va installer Argos Translate et le modèle anglais → français.\n\nUne connexion Internet est nécessaire pour cette préparation unique.", parent=self):
            return
        self.offline_running = True
        self.prepare_btn.configure(state="disabled")
        self.translate_btn.configure(state="disabled")
        self.argos_status_var.set("Préparation en cours…")
        self.offline_status_var.set("Installation de la traduction hors ligne…")
        self.offline_progress["value"] = 10

        def worker():
            try:
                try:
                    package_module, _translate = self._import_argos()
                except ImportError as exc:
                    if getattr(sys, "frozen", False):
                        raise RuntimeError(
                            "Le composant Argos manque dans l'installation. Réinstalle le Setup officiel."
                        ) from exc
                    raise RuntimeError(
                        "Argos Translate n'est pas installé dans cet environnement Python. "
                        "Installe les dépendances de développement avant de relancer l'application."
                    ) from exc
                self.after(0, lambda: self.offline_progress.configure(value=45))
                install_argos_english_french_model(package_module)
                self.after(0, self._prepare_finished)
            except Exception as exc:
                self.after(0, lambda error=exc: self._prepare_failed(error))
        threading.Thread(target=worker, daemon=True).start()

    def _prepare_finished(self):
        self.offline_running = False
        self.offline_progress["value"] = 100
        self.prepare_btn.configure(state="normal")
        self.translate_btn.configure(state="normal")
        self.check_argos()
        self.offline_status_var.set("Préparation terminée.")
        if self.close_requested:
            return
        messagebox.showinfo("Prêt", "La traduction hors ligne est prête.", parent=self)

    def _prepare_failed(self, exc):
        self.offline_running = False
        self.offline_progress["value"] = 0
        self.prepare_btn.configure(state="normal")
        self.translate_btn.configure(state="normal")
        self.argos_status_var.set("Préparation impossible")
        if self.close_requested:
            return
        messagebox.showerror("Préparation impossible", f"{exc}\n\nVérifie ta connexion Internet puis réessaie.", parent=self)

    def _translation_scope_indices(self) -> list[int]:
        include_all = self.developer_visible and self.translate_all_types_var.get()
        result = []
        for index, row in enumerate(self.rows):
            if row.get("traduction_fr", "").strip() or row.get("statut") == "Ignoré":
                continue
            if not include_all and row.get("type") not in {"Dialogue", "Choix"}:
                continue
            result.append(index)
        return result

    def _set_batch(self, value: str):
        self.batch_var.set(value)
        self._update_batch_info()

    def _batch_limit(self) -> int | None:
        value = self.batch_var.get().strip()
        if value.casefold() in {"tout", "all", "*"}:
            return None
        if not value.isdigit():
            raise ValueError("Entre un nombre entier positif, par exemple 100, ou clique sur TOUT.")
        limit = int(value)
        if limit <= 0:
            raise ValueError("Le nombre de textes doit être supérieur à zéro.")
        if limit > 100000:
            raise ValueError("La limite maximale est de 100 000 textes uniques par lot.")
        return limit

    def _all_translation_groups(self) -> list[list[int]]:
        grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index in self._translation_scope_indices():
            grouped[duplicate_key(self.rows[index])].append(index)
        return list(grouped.values())

    def _build_translation_groups(self) -> list[list[int]]:
        groups = self._all_translation_groups()
        limit = self._batch_limit()
        return groups if limit is None else groups[:limit]

    def _update_batch_info(self, update_button: bool = True):
        if not hasattr(self, "batch_info_var"):
            return
        remaining = len(self._all_translation_groups()) if self.rows else 0
        value = self.batch_var.get().strip() if hasattr(self, "batch_var") else "100"
        try:
            limit = self._batch_limit()
            selected = remaining if limit is None else min(limit, remaining)
            label = "TOUT" if limit is None else str(limit)
            self.batch_info_var.set(
                f"{remaining} dialogue(s) unique(s) encore à traduire  •  lot prévu : {selected}"
            )
            if update_button and hasattr(self, "translate_btn"):
                self.translate_btn.configure(text=f"TRADUIRE {label}")
        except ValueError:
            self.batch_info_var.set("Entre un nombre entier positif ou clique sur TOUT.")
            if update_button and hasattr(self, "translate_btn"):
                self.translate_btn.configure(text="VÉRIFIER LE NOMBRE")

    def start_translation(self, skip_confirmation: bool = False):
        if self.offline_running:
            return
        if not self.rows:
            messagebox.showerror("Textes absents", "Extrais d'abord les textes du fangame.", parent=self)
            return
        if not self.check_argos():
            self.prepare_argos()
            return
        try:
            groups = self._build_translation_groups()
        except ValueError as exc:
            messagebox.showerror("Nombre incorrect", str(exc), parent=self)
            self.batch_entry.focus_set()
            return
        if not groups:
            messagebox.showinfo("Rien à traduire", "Les dialogues sélectionnés sont déjà traduits.", parent=self)
            return
        affected = sum(len(group) for group in groups)
        if not skip_confirmation and not messagebox.askyesno(
            "Lancer la traduction",
            f"Traduire {len(groups)} texte(s) unique(s) ?\n\nLes doublons peuvent remplir jusqu'à {affected} ligne(s).\nSeules les traductions suspectes seront placées dans « À vérifier ».",
            parent=self,
        ):
            return

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = self.backup_dir / f"avant_traduction_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self._write_csv(backup)
        self.offline_running = True
        self.offline_stop = False
        self.last_batch_indices = []
        self.translate_btn.configure(state="disabled")
        self.offline_progress["value"] = 0
        self.offline_status_var.set(f"Traduction de {len(groups)} texte(s) unique(s)…")
        self.log(f"Sauvegarde créée : {backup}")
        self._write_resume_state(total=len(groups), completed=0, remaining=len(groups), active=True)
        self._refresh_resume_button()
        propagate_duplicates = bool(self.propagate_duplicates_var.get())
        threading.Thread(
            target=self._translation_worker,
            args=(groups, propagate_duplicates),
            daemon=True,
        ).start()

    def stop_translation(self):
        if self.offline_running:
            self.offline_stop = True
            self.offline_status_var.set("Arrêt demandé…")

    def _translation_worker(self, groups: list[list[int]], propagate_duplicates: bool):
        successes = 0
        filled_rows = 0
        flagged_rows = 0
        failures: list[str] = []
        blocked_groups = 0
        try:
            _package, translate_module = self._import_argos()
            languages = translate_module.get_installed_languages()
            english = next(lang for lang in languages if lang.code == "en")
            french = next(lang for lang in languages if lang.code == "fr")
            translator = english.get_translation(french)

            for position, group in enumerate(groups, start=1):
                if self.offline_stop:
                    break
                representative = group[0]
                source = self.rows[representative].get("texte_source", "")
                try:
                    learned = self.corrections.get(source)
                    translated = learned or translate_preserving_codes(translator, source, self.glossary)
                    if not translated:
                        raise RuntimeError("Traduction vide")
                    level, alerts = review_translation(source, translated)
                    if level == "bloque":
                        raise RuntimeError(" | ".join(alerts))
                    targets = group if propagate_duplicates else [representative]
                    for index in targets:
                        row = self.rows[index]
                        if row.get("traduction_fr", "").strip():
                            continue
                        row["traduction_fr"] = translated
                        row["niveau_relecture"] = level
                        row["alertes_relecture"] = " | ".join(alerts)
                        row["origine_traduction"] = "correction_apprise" if learned else "argos"
                        row["statut"] = "Accepté" if learned and not alerts else status_from_review(level)
                        filled_rows += 1
                        if alerts:
                            flagged_rows += 1
                        self.last_batch_indices.append(index)
                    successes += 1
                except Exception as exc:
                    blocked_groups += 1
                    failures.append(f"{self.rows[representative].get('id_stable', representative)} : {exc}")

                if position % 3 == 0 or position == len(groups):
                    percent = position * 100 / max(1, len(groups))
                    self.after(0, lambda p=position, pc=percent, s=successes, r=filled_rows, f=len(failures): self._update_translation_progress(p, len(groups), pc, s, r, f))
                if position % self.autosave_every == 0 or position == len(groups):
                    if self.csv_path:
                        self._write_csv(self.csv_path)
                    self._write_resume_state(
                        total=len(groups),
                        completed=position,
                        remaining=max(0, len(groups) - position),
                        active=(position < len(groups)),
                    )
                time.sleep(0.01)
            self.after(0, lambda: self._translation_finished(successes, filled_rows, flagged_rows, blocked_groups, failures, len(groups)))
        except Exception as exc:
            self.after(0, lambda error=exc: self._translation_failed(error))

    def _update_translation_progress(self, position, total, percent, successes, rows, failures):
        self.offline_progress["value"] = percent
        self.offline_status_var.set(f"{position}/{total} textes uniques • {rows} lignes remplies • {failures} erreur(s)")

    def _translation_finished(self, successes, filled_rows, flagged_rows, blocked_groups, failures, total):
        self.offline_running = False
        self.translate_btn.configure(state="normal")
        self.offline_progress["value"] = 100 if not self.offline_stop else self.offline_progress["value"]
        if self.csv_path:
            self._write_csv(self.csv_path)

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report = self.reports_dir / "RAPPORT_TRADUCTION_V0.9.txt"
        ready_rows = max(0, filled_rows - flagged_rows)
        sample_size = min(20, len({self.rows[index].get("groupe_doublon") for index in self.last_batch_indices if 0 <= index < len(self.rows)}))
        report.write_text("\n".join([
            "POKÉMON FANGAME TRANSLATOR v1.0 — RAPPORT DE TRADUCTION",
            "=" * 78,
            f"Textes uniques demandés : {total}",
            f"Textes uniques réussis : {successes}",
            f"Lignes remplies avec doublons : {filled_rows}",
            f"Lignes prêtes : {ready_rows}",
            f"Lignes à vérifier : {flagged_rows}",
            f"Groupes bloqués ou en erreur : {blocked_groups}",
            f"Échantillon proposé : {sample_size}",
            f"Arrêt demandé : {'OUI' if self.offline_stop else 'NON'}",
            "",
            "ÉCHECS",
            "-" * 78,
            *(failures or ["Aucun."]),
        ]), encoding="utf-8")

        self.last_batch_summary = (
            f"DERNIER LOT  •  {successes}/{total} texte(s) unique(s) réussi(s)  •  "
            f"{filled_rows} ligne(s) remplie(s)  •  {ready_rows} prête(s)  •  "
            f"{flagged_rows} à vérifier  •  {blocked_groups} bloqué(s)  •  "
            f"échantillon : {sample_size}"
        )
        self.last_batch_summary_var.set(self.last_batch_summary)
        if not self.offline_stop:
            self._write_resume_state(total=total, completed=total, remaining=0, active=False)
        else:
            remaining = max(0, total - successes - blocked_groups)
            self._write_resume_state(total=total, completed=successes + blocked_groups, remaining=remaining, active=remaining > 0)
        self._refresh_resume_button()
        self.offline_status_var.set(
            f"Terminé : {ready_rows} prêt(s), {flagged_rows} à vérifier, {blocked_groups} bloqué(s)."
        )
        self._update_batch_info()

        if self.close_requested:
            return
        if flagged_rows:
            self.show_flagged()
        else:
            self.show_random_sample(auto=True)

        messagebox.showinfo(
            "Résumé du lot",
            f"{successes} texte(s) unique(s) traduit(s) sur {total}.\n\n"
            f"{filled_rows} ligne(s) remplies avec les doublons.\n"
            f"{ready_rows} prête(s).\n"
            f"{flagged_rows} à vérifier.\n"
            f"{blocked_groups} bloqué(s) ou en erreur.\n"
            f"{sample_size} exemple(s) proposé(s) pour le contrôle rapide.",
            parent=self,
        )

    def _translation_failed(self, exc):
        self.offline_running = False
        self.translate_btn.configure(state="normal")
        self.resume_state = self._read_resume_state()
        self._refresh_resume_button()
        self.offline_progress["value"] = 0
        self.offline_status_var.set("La traduction s'est arrêtée.")
        if self.close_requested:
            return
        messagebox.showerror("Traduction interrompue", str(exc), parent=self)

    def on_close(self):
        if self.offline_running:
            if not messagebox.askyesno(
                "Opération en cours",
                "Une opération est en cours. Demander son arrêt puis fermer la fenêtre dès que les fichiers sont en sécurité ?",
                parent=self,
            ):
                return
            self.offline_stop = True
            self.close_requested = True
            self.offline_status_var.set("Arrêt demandé. Fermeture dès que les fichiers sont en sécurité…")
            self.after(100, self._close_when_idle)
            return
        self.destroy()

    def _close_when_idle(self):
        if not self.winfo_exists():
            return
        if self.offline_running:
            self.after(100, self._close_when_idle)
            return
        self.destroy()
