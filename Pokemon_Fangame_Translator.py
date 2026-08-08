# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
import csv
import hashlib
import json
import os
import platform
import sys
import re
import shutil
import tempfile
import zipfile
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from translation_studio import TranslationStudio
from reconstruction_studio import ReconstructionStudio
from adapters import DetectionResult, GameCapability, create_default_registry
from analysis import report_text as deep_report_text, write_analysis_reports
from project_identity import ProjectIdentityError, write_project_identity
from safe_io import atomic_text_writer, atomic_write_bytes, atomic_write_text


APP_TITLE = "Pokémon Fangame Translator v1.0.2 — Bêta publique"
APP_SUBTITLE = "Bêta publique • Traduction automatique guidée • Original préservé"


@dataclass
class Diagnostic:
    root: str
    adapter_id: str
    adapter_display_name: str
    detection_confidence: int
    write_actions_allowed: bool
    adapter_ambiguous: bool
    detection_evidence: list[str]
    rpg_maker_xp_detected: bool
    pokemon_essentials_detected: bool
    probable_essentials_version: str
    game_exe_present: bool
    game_ini_present: bool
    data_folder_present: bool
    graphics_folder_present: bool
    audio_folder_present: bool
    scripts_rxdata_present: bool
    common_events_present: bool
    system_rxdata_present: bool
    map_infos_present: bool
    map_count: int
    rxdata_count: int
    dat_count: int
    encrypted_archives: list[str]
    message_banks: list[str]
    probable_text_sources: list[str]
    excluded_technical_files: list[str]
    compatibility_level: str
    compatibility_score: int
    warnings: list[str]
    notes: list[str]


PROJECT_EXTRA_FIELDS = ["niveau_relecture", "alertes_relecture", "groupe_doublon", "origine_traduction"]
PROJECT_REQUIRED_FIELDS = {"id_stable", "texte_source", "traduction_fr", "statut"}


class ProjectMergeError(RuntimeError):
    """Le projet existant doit être conservé au lieu d'être remplacé."""


def default_projects_root() -> Path:
    documents = Path.home() / "Documents"
    base = documents if documents.exists() else Path.home()
    return base / "Pokemon Fangame Translator" / "Projets"


def project_directory_for_game(game_root: Path, projects_root: Path | None = None) -> Path:
    resolved = game_root.expanduser().resolve()
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", resolved.name).strip("_") or "Fangame"
    digest = hashlib.sha1(str(resolved).casefold().encode("utf-8", errors="replace")).hexdigest()[:8]
    requested_root = (projects_root or default_projects_root()).expanduser()
    display_root = Path(os.path.abspath(requested_root))
    project = display_root / f"{safe_name}_{digest}"
    if _is_same_or_within(project, resolved):
        raise ValueError(
            "Le dossier de projets serait créé à l'intérieur du fangame original. "
            "Choisis le véritable dossier du jeu, pas un dossier parent comme Documents."
        )
    return project


def merge_project_rows(new_rows: list[dict[str, str]], existing_csv: Path | None) -> tuple[list[dict[str, str]], int, list[str]]:
    """Réinjecte uniquement le travail de traduction dans une nouvelle extraction."""
    base_fields = list(new_rows[0].keys()) if new_rows else []
    fields = base_fields + [field for field in PROJECT_EXTRA_FIELDS if field not in base_fields]
    if not existing_csv or not existing_csv.exists():
        return [{field: row.get(field, "") for field in fields} for row in new_rows], 0, fields

    if not new_rows:
        raise ProjectMergeError(
            "L'extraction n'a produit aucune ligne ; le projet existant est conservé."
        )

    try:
        with existing_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            missing = sorted(PROJECT_REQUIRED_FIELDS - set(reader.fieldnames or []))
            if missing:
                raise ProjectMergeError(
                    "Le CSV existant est incompatible, colonnes manquantes : "
                    + ", ".join(missing)
                )
            old_rows = list(reader)
            for field in reader.fieldnames or []:
                if field not in fields:
                    fields.append(field)
    except ProjectMergeError:
        raise
    except Exception as exc:
        raise ProjectMergeError(
            f"Le CSV existant est illisible ({type(exc).__name__})."
        ) from exc

    by_id = {row.get("id_stable", ""): row for row in old_rows if row.get("id_stable")}
    by_exact = {
        (
            row.get("type", ""), row.get("fichier", ""), row.get("carte_id", ""),
            row.get("evenement_id", ""), row.get("page", ""), row.get("commande", ""),
            row.get("sous_index", ""), row.get("texte_source", ""),
        ): row
        for row in old_rows if row.get("texte_source")
    }
    preserved = 0
    merged: list[dict[str, str]] = []
    copy_fields = ["traduction_fr", "statut", *PROJECT_EXTRA_FIELDS]
    for source in new_rows:
        row = {field: source.get(field, "") for field in fields}
        exact_key = (
            source.get("type", ""), source.get("fichier", ""), source.get("carte_id", ""),
            source.get("evenement_id", ""), source.get("page", ""), source.get("commande", ""),
            source.get("sous_index", ""), source.get("texte_source", ""),
        )
        previous_by_id = by_id.get(source.get("id_stable", ""))
        source_unchanged = bool(
            previous_by_id
            and previous_by_id.get("texte_source", "") == source.get("texte_source", "")
        )
        previous = previous_by_id if source_unchanged else by_exact.get(exact_key)
        if previous and (previous.get("traduction_fr") or "").strip():
            for field in copy_fields:
                if field in fields:
                    row[field] = previous.get(field, "")
            preserved += 1
        elif previous_by_id and not source_unchanged:
            row["niveau_relecture"] = "source_modifiee"
            row["alertes_relecture"] = (
                "Texte source modifié depuis la précédente extraction ; ancienne traduction non réutilisée"
            )
            row["origine_traduction"] = "source_modifiee"
        merged.append(row)
    return merged, preserved, fields


def write_project_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with atomic_text_writer(path, encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def backup_project_csv(csv_path: Path) -> Path | None:
    """Crée une sauvegarde exacte et unique avant toute réextraction."""
    if not csv_path.exists():
        return None
    is_junction = getattr(csv_path, "is_junction", None)
    if csv_path.is_symlink() or bool(is_junction and is_junction()):
        raise ProjectMergeError("Le CSV du projet ne peut pas être un lien ou une jonction.")
    try:
        before = csv_path.stat()
        payload = csv_path.read_bytes()
        after = csv_path.stat()
    except OSError as exc:
        raise ProjectMergeError("Le CSV existant ne peut pas être sauvegardé.") from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ProjectMergeError("Le CSV existant a changé pendant sa sauvegarde.")

    backup_dir = csv_path.parent / "Sauvegardes"
    backup = backup_dir / (
        "avant_reextraction_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".csv"
    )
    atomic_write_bytes(backup, payload)
    if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(payload).digest():
        raise ProjectMergeError("La sauvegarde du CSV n'est pas identique à l'original.")
    return backup


def _is_same_or_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _copy_private_sample_file(source: Path, destination: Path, game_root: Path) -> bool:
    """Copie un fichier uniquement s'il appartient réellement au fangame."""
    if not source.is_file():
        return False
    resolved_source = source.resolve()
    if source.is_symlink() or not _is_same_or_within(resolved_source, game_root):
        raise ValueError(f"Fichier lié hors du fangame refusé : {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved_source, destination)
    return True


def create_private_diagnostic_sample(
    game_root: Path,
    zip_path: Path,
    *,
    application_dir: Path | None = None,
) -> list[str]:
    """Crée un échantillon privé sans laisser de données près des sources.

    L'archive peut contenir des dialogues et d'autres données appartenant au
    créateur du fangame. Elle n'est donc jamais créée dans le jeu original ni
    dans le dossier de l'application, et son dossier de travail est temporaire.
    """
    root = game_root.expanduser().resolve()
    data = root / "Data"
    if not data.is_dir():
        raise ValueError("Le dossier Data est introuvable.")

    destination = zip_path.expanduser().resolve()
    if _is_same_or_within(destination, root):
        raise ValueError("L'échantillon privé ne peut pas être enregistré dans le fangame original.")
    if application_dir and _is_same_or_within(destination, application_dir):
        raise ValueError("L'échantillon privé ne peut pas être enregistré dans le dossier de l'application.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", root.name).strip("_") or "Fangame"
    copied: list[str] = []

    with tempfile.TemporaryDirectory(prefix="pft_diagnostic_prive_") as temp_dir:
        work_root = Path(temp_dir)
        sample_root = work_root / f"Echantillon_prive_{safe_name}_v1.0.2"
        sample_data = sample_root / "Data"
        sample_pbs = sample_root / "PBS"
        sample_data.mkdir(parents=True)

        for name in (
            "messages_game.dat",
            "messages_core.dat",
            "MapInfos.rxdata",
            "CommonEvents.rxdata",
        ):
            source = data / name
            if _copy_private_sample_file(source, sample_data / name, root):
                copied.append(f"Data/{name}")

        maps = [path for path in data.glob("Map*.rxdata") if path.is_file()]
        maps.sort(key=lambda path: path.stat().st_size)
        if maps:
            selected: list[Path] = []
            useful = [path for path in maps if path.stat().st_size >= 12_000] or maps
            for index in (0, len(useful) // 2, len(useful) - 1):
                path = useful[index]
                if path not in selected:
                    selected.append(path)
            for source in selected:
                if _copy_private_sample_file(source, sample_data / source.name, root):
                    copied.append(f"Data/{source.name}")

        pbs = root / "PBS"
        pbs_count = 0
        if pbs.is_dir():
            for source in pbs.rglob("*.txt"):
                relative = source.relative_to(pbs)
                if any("backup" in part.casefold() for part in relative.parts):
                    continue
                if _copy_private_sample_file(source, sample_pbs / relative, root):
                    pbs_count += 1
            if pbs_count:
                copied.append(f"PBS/ ({pbs_count} fichier(s), hors sauvegardes)")

        manifest = sample_root / "CONTENU_ECHANTILLON_PRIVE.txt"
        manifest.write_text(
            "POKÉMON FANGAME TRANSLATOR v1.0.2 — ÉCHANTILLON PRIVÉ\n\n"
            "ATTENTION : cette archive peut contenir des cartes, des dialogues et des données PBS "
            "appartenant au créateur du fangame. Ne la publiez pas et transmettez-la seulement "
            "à une personne de confiance pour un diagnostic privé.\n\n"
            "Fichiers copiés :\n- " + ("\n- ".join(copied) if copied else "Aucun") +
            "\n\nFichiers volontairement exclus : Scripts.rxdata, PluginScripts.rxdata, "
            "Game.exe, DLL, graphismes, musiques et sauvegardes.\n",
            encoding="utf-8",
        )

        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.stem}_",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temp_handle:
            temporary_zip = Path(temp_handle.name)
        try:
            with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sample_root.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(work_root))
            temporary_zip.replace(destination)
        finally:
            temporary_zip.unlink(missing_ok=True)

    return copied


class NeonButton(tk.Canvas):
    """Bouton Canvas arrondi avec survol néon et gestion d'état."""
    def __init__(self, master, text, command=None, accent="#00e5ff", width=230, height=52, icon="", enabled=True):
        super().__init__(master, width=width, height=height, bg=master.cget("bg"), highlightthickness=0, bd=0)
        self.button_text = text
        self.command = command
        self.accent = accent
        self.icon = icon
        self.enabled = enabled
        self._hover = False
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _rounded(self, x1, y1, x2, y2, radius, **kwargs):
        points = [x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1]
        return self.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _draw(self):
        self.delete("all")
        w=max(10,self.winfo_width()); h=max(10,self.winfo_height())
        if self.enabled:
            fill = "#151d28" if not self._hover else "#1b2735"
            outline = self.accent if self._hover else "#334154"
            fg = "#edf2f7"
        else:
            fill = "#111720"; outline="#26313e"; fg="#687587"
        self._rounded(2,2,w-2,h-2,11,fill=fill,outline=outline,width=1)
        if self.enabled:
            self.create_rectangle(3,3,7,h-3,fill=self.accent,outline="")
        label=(self.icon+"  " if self.icon else "")+self.button_text
        font_size = 9 if len(label) > 23 else 10
        self.create_text(w/2+2,h/2,text=label,fill=fg,font=("Segoe UI Semibold",font_size),width=max(24,w-24),justify="center")

    def _on_enter(self, _e):
        self._hover=True; self._draw()
    def _on_leave(self, _e):
        self._hover=False; self._draw()
    def _on_click(self, _e):
        if self.enabled and self.command:
            self.command()
    def set_enabled(self, enabled):
        self.enabled=bool(enabled); self._draw()


class NeonCard(tk.Canvas):
    """Carte arrondie contenant un Frame utilisable avec pack/grid."""
    def __init__(self, master, accent="#202838", radius=16, bg="#090d14", **kwargs):
        super().__init__(master, bg=master.cget("bg"), highlightthickness=0, bd=0, **kwargs)
        self.card_bg=bg; self.accent=accent; self.radius=radius
        self.content=tk.Frame(self,bg=bg)
        self.window_id=self.create_window(16,16,anchor="nw",window=self.content)
        self.bind("<Configure>",self._redraw)
    def _rounded(self,x1,y1,x2,y2,radius,**kwargs):
        pts=[x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1]
        return self.create_polygon(pts,smooth=True,splinesteps=24,**kwargs)
    def _redraw(self,_e=None):
        w=max(40,self.winfo_width()); h=max(40,self.winfo_height())
        self.delete("card_shape")
        self._rounded(2,2,w-2,h-2,self.radius,fill=self.card_bg,outline=self.accent,width=1,tags="card_shape")
        self.tag_lower("card_shape")
        self.coords(self.window_id,16,16)
        self.itemconfigure(self.window_id,width=max(10,w-32),height=max(10,h-32))


class FangameTranslatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1320x840")
        self.minsize(1040, 700)

        self.base_dir = Path(__file__).resolve().parent
        self.game_dir: Path | None = None
        self.last_diagnostic: Diagnostic | None = None
        self.last_deep_analysis = None
        self.adapter_registry = create_default_registry()
        self.detection_result: DetectionResult | None = None
        self.extracted_count = 0
        self.project_status = tk.StringVar(value="En attente")
        self.compatibility_display = tk.StringVar(value="-- / 100")
        self.maps_display = tk.StringVar(value="--")
        self.texts_display = tk.StringVar(value="--")
        self.engine_display = tk.StringVar(value="--")
        self.essentials_display = tk.StringVar(value="--")
        self.files_display = tk.StringVar(value="--")
        self.game_name_display = tk.StringVar(value="Aucun")
        self.translation_csv_path: Path | None = None
        self.project_dir: Path | None = None
        self.translation_windows = []

        self.colors = {
            "bg": "#0b0f14",
            "panel": "#111720",
            "panel2": "#151d28",
            "panel3": "#1b2531",
            "accent": "#5b9cff",
            "pink": "#5b9cff",
            "purple": "#7399d8",
            "orange": "#d2a34f",
            "accent_dark": "#274b78",
            "accent2": "#dce9fa",
            "accent3": "#5b9cff",
            "text": "#e8edf3",
            "muted": "#98a4b3",
            "success": "#62c891",
            "warning": "#e0b85b",
            "danger": "#ed727a",
            "border": "#293442",
            "glass": "#0e141c",
        }

        self.configure(bg=self.colors["bg"])
        try:
            self._app_icon = tk.PhotoImage(file=str(self.base_dir / "assets" / "pft_icon_v06.png"))
            self.iconphoto(True, self._app_icon)
        except tk.TclError:
            self._app_icon = None
        self._configure_styles()
        self._build_menu()
        self._build_ui()

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("Muted.TLabel", background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=self.colors["panel2"], foreground=self.colors["muted"], padding=(16,9), borderwidth=0, font=("Segoe UI Semibold",9))
        style.map("TNotebook.Tab", background=[("selected",self.colors["panel3"])], foreground=[("selected",self.colors["accent"])])
        style.configure("Treeview", background=self.colors["panel"], fieldbackground=self.colors["panel"], foreground=self.colors["text"], rowheight=29, bordercolor=self.colors["border"])
        style.configure("Treeview.Heading", background=self.colors["panel3"], foreground=self.colors["accent"], relief="flat", font=("Segoe UI Semibold",9))
        style.map("Treeview", background=[("selected",self.colors["accent_dark"])], foreground=[("selected","white")])
        style.configure("Neon.Horizontal.TProgressbar", troughcolor="#111722", background=self.colors["accent"], bordercolor="#111722", lightcolor=self.colors["accent"], darkcolor=self.colors["accent"])
        style.configure("Vertical.TScrollbar", background=self.colors["panel3"], troughcolor=self.colors["panel"], arrowcolor=self.colors["muted"], bordercolor=self.colors["border"])

    def _build_menu(self):
        menu = tk.Menu(
            self,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            activebackground=self.colors["accent"],
            activeforeground="white",
            tearoff=False,
        )

        file_menu = tk.Menu(
            menu, tearoff=0,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            activebackground=self.colors["accent"],
            activeforeground="white",
        )
        file_menu.add_command(label="Choisir un fangame", command=self.choose_game)
        file_menu.add_command(label="Créer un échantillon privé de diagnostic", command=self.create_diagnostic_sample)
        file_menu.add_command(
            label="Ouvrir le studio de traduction",
            command=self.open_translation_studio,
            state="disabled",
        )
        file_menu.add_command(
            label="Analyser en profondeur",
            command=self.run_deep_analysis,
            state="disabled",
        )
        file_menu.add_command(label="Exporter le rapport", command=self.export_report)
        file_menu.add_command(label="Exporter un diagnostic public", command=self.export_public_diagnostic)
        file_menu.add_separator()
        file_menu.add_command(label="Quitter", command=self.destroy)

        help_menu = tk.Menu(
            menu, tearoff=0,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            activebackground=self.colors["accent"],
            activeforeground="white",
        )
        help_menu.add_command(label="Tutoriel", command=self.open_tutorial)
        help_menu.add_command(label="À propos", command=self.open_about)

        menu.add_cascade(label="Fichier", menu=file_menu)
        menu.add_cascade(label="Aide", menu=help_menu)
        self.file_menu = file_menu
        self.config(menu=menu)

    def _build_ui(self):
        self.details_visible = False

        root_shell = tk.Frame(self, bg=self.colors["bg"])
        root_shell.pack(fill="both", expand=True)

        # Compact header: identity on the left, security status on the right.
        header = tk.Frame(root_shell, bg=self.colors["panel"], height=82,
                          highlightbackground=self.colors["border"], highlightthickness=1)
        header.pack(fill="x")
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=self.colors["panel"])
        brand.pack(side="left", fill="y", padx=22)
        try:
            self._header_icon = tk.PhotoImage(file=str(self.base_dir / "assets" / "pft_icon_v06.png"))
            tk.Label(brand, image=self._header_icon, bg=self.colors["panel"]).pack(side="left", padx=(0, 14))
        except tk.TclError:
            tk.Label(brand, text="◉", bg=self.colors["panel"], fg=self.colors["accent"],
                     font=("Segoe UI", 26)).pack(side="left", padx=(0, 14))

        title_box = tk.Frame(brand, bg=self.colors["panel"])
        title_box.pack(side="left", pady=15)
        tk.Label(title_box, text="Pokémon Fangame Translator", bg=self.colors["panel"],
                 fg=self.colors["text"], font=("Segoe UI Semibold", 18)).pack(anchor="w")
        tk.Label(title_box, text="Bêta publique — Traduire un fangame compatible sans modifier l'original",
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

        status_badge = tk.Frame(header, bg="#11241b", highlightbackground="#315b46",
                                highlightthickness=1, padx=14, pady=9)
        status_badge.pack(side="right", padx=22)
        tk.Label(status_badge, text="●  MODE SÉCURISÉ", bg="#11241b",
                 fg=self.colors["success"], font=("Segoe UI Semibold", 9)).pack(anchor="w")
        tk.Label(status_badge, text="L'original reste intact", bg="#11241b",
                 fg="#b7c8be", font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

        # Mandatory, visible disclaimer requested for public distribution.
        warning = tk.Frame(root_shell, bg="#211b11", highlightbackground="#6f5726",
                           highlightthickness=1, padx=16, pady=10)
        warning.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(warning, text="ATTENTION", bg="#211b11", fg=self.colors["warning"],
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=(0, 12))
        tk.Label(
            warning,
            text=("Traduction automatique : le résultat peut contenir des erreurs et ne remplace pas une équipe humaine. "
                  "Projet indépendant et non officiel ; aucun fangame ni fichier de jeu n'est fourni. "
                  "Relisez les passages importants."),
            bg="#211b11", fg="#ded4bd", justify="left", anchor="w",
            wraplength=1020, font=("Segoe UI", 9),
        ).pack(side="left", fill="x", expand=True)

        scroll_shell = tk.Frame(root_shell, bg=self.colors["bg"])
        scroll_shell.pack(fill="both", expand=True, pady=(12, 0))
        scroll_canvas = tk.Canvas(scroll_shell, bg=self.colors["bg"], highlightthickness=0)
        scroll_bar = ttk.Scrollbar(scroll_shell, orient="vertical", command=scroll_canvas.yview)
        scroll_canvas.configure(yscrollcommand=scroll_bar.set)
        scroll_bar.pack(side="right", fill="y")
        scroll_canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(scroll_canvas, bg=self.colors["bg"])
        content_window = scroll_canvas.create_window((18, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")))
        scroll_canvas.bind("<Configure>", lambda event: scroll_canvas.itemconfigure(content_window, width=max(760, event.width - 38)))
        scroll_canvas.bind_all("<MouseWheel>", lambda event: scroll_canvas.yview_scroll(int(-event.delta / 120), "units"))

        # A single, calm four-step overview.
        steps = tk.Frame(content, bg=self.colors["bg"])
        steps.pack(fill="x")
        for number, title, detail in [
            ("1", "Choisir le jeu", "Dossier principal"),
            ("2", "Analyser", "Compatibilité"),
            ("3", "Traduire", "Par petits ou grands lots"),
            ("4", "Créer la version FR", "Copie séparée et jouable"),
        ]:
            card = tk.Frame(steps, bg=self.colors["panel"], highlightbackground=self.colors["border"],
                            highlightthickness=1, padx=12, pady=10)
            card.pack(side="left", fill="x", expand=True, padx=(0, 8))
            tk.Label(card, text=number, bg=self.colors["panel"], fg=self.colors["accent"],
                     font=("Segoe UI Semibold", 15), width=2).pack(side="left")
            labels = tk.Frame(card, bg=self.colors["panel"])
            labels.pack(side="left", padx=(8, 0))
            tk.Label(labels, text=title, bg=self.colors["panel"], fg=self.colors["text"],
                     font=("Segoe UI Semibold", 9)).pack(anchor="w")
            tk.Label(labels, text=detail, bg=self.colors["panel"], fg=self.colors["muted"],
                     font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(content, bg=self.colors["bg"])
        body.pack(fill="x", pady=(12, 0))
        left = tk.Frame(body, bg=self.colors["bg"])
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=self.colors["bg"], width=330)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        # Game selection.
        choose = NeonCard(left, accent=self.colors["border"], bg=self.colors["panel"], height=132)
        choose.pack(fill="x")
        cc = choose.content
        tk.Label(cc, text="1. Choisir le fangame", bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(cc, text="Sélectionnez le dossier qui contient Data, Game.exe et Game.ini.",
                 bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 9))
        select_row = tk.Frame(cc, bg=self.colors["panel"])
        select_row.pack(fill="x")
        path_box = tk.Frame(select_row, bg=self.colors["panel2"], highlightbackground=self.colors["border"],
                            highlightthickness=1, padx=12, pady=11)
        path_box.pack(side="left", fill="x", expand=True)
        self.path_var = tk.StringVar(value="Aucun fangame sélectionné")
        tk.Label(path_box, textvariable=self.path_var, bg=self.colors["panel2"], fg=self.colors["muted"],
                 anchor="w", font=("Segoe UI", 9)).pack(fill="x")
        NeonButton(select_row, "CHOISIR LE JEU", self.choose_game, self.colors["accent"],
                   width=190, height=44, icon="▰").pack(side="left", padx=(10, 0))

        # Analysis and progress.
        analyse = NeonCard(left, accent=self.colors["border"], bg=self.colors["panel"], height=196)
        analyse.pack(fill="x", pady=(12, 0))
        ac = analyse.content
        tk.Label(ac, text="2. Analyser la compatibilité", bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(ac, text="Le logiciel vérifie automatiquement le moteur, les cartes et les sources de texte.",
                 bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 10))
        analysis_row = tk.Frame(ac, bg=self.colors["panel"])
        analysis_row.pack(fill="x")
        self.analyze_btn = NeonButton(analysis_row, "ANALYSER LE JEU", self.run_diagnostic,
                                      self.colors["accent"], width=210, height=54, icon="⌕", enabled=False)
        self.analyze_btn.pack(side="left")
        self.quick_analyze_btn = self.analyze_btn
        self.deep_analyze_btn = NeonButton(
            analysis_row,
            "ANALYSE APPROFONDIE",
            self.run_deep_analysis,
            self.colors["purple"],
            width=230,
            height=54,
            icon="◎",
            enabled=False,
        )
        self.deep_analyze_btn.pack(side="left", padx=(10, 0))
        checklist = tk.Frame(analysis_row, bg=self.colors["panel2"], padx=12, pady=7)
        checklist.pack(side="left", fill="both", expand=True, padx=(12, 0))
        tk.Label(checklist, text="RPG Maker XP / Essentials / fichiers de texte / sécurité",
                 bg=self.colors["panel2"], fg=self.colors["muted"], font=("Segoe UI", 8),
                 wraplength=520, justify="left").pack(anchor="w")
        self.progress = ttk.Progressbar(ac, maximum=100, style="Neon.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(14, 4))
        progress_row = tk.Frame(ac, bg=self.colors["panel"])
        progress_row.pack(fill="x")
        self.status_var = tk.StringVar(value="Prêt à analyser")
        tk.Label(progress_row, textvariable=self.status_var, bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(side="left")
        self.progress_percent = tk.StringVar(value="0%")
        tk.Label(progress_row, textvariable=self.progress_percent, bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 8)).pack(side="right")

        # Three next actions, shown only once and in the expected order.
        actions = NeonCard(left, accent=self.colors["border"], bg=self.colors["panel"], height=150)
        actions.pack(fill="x", pady=(12, 0))
        action_content = actions.content
        tk.Label(action_content, text="Continuer", bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(action_content, text="Suivez les boutons de gauche à droite.", bg=self.colors["panel"],
                 fg=self.colors["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 9))
        action_row = tk.Frame(action_content, bg=self.colors["panel"])
        action_row.pack(fill="both", expand=True)
        self.extract_btn = NeonButton(action_row, "2. EXTRAIRE LES TEXTES", self.extract_texts,
                                      self.colors["accent"], width=220, height=66, icon="▤", enabled=False)
        self.extract_btn.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.translate_btn = NeonButton(
            action_row, "3. TRADUIRE", self.open_translation_studio,
            self.colors["accent"], width=185, height=66, icon="◎", enabled=False,
        )
        self.translate_btn.pack(side="left", fill="both", expand=True, padx=6)
        self.reconstruction_btn = NeonButton(
            action_row, "4. CRÉER LA VERSION FR", self.open_reconstruction_studio,
            self.colors["orange"], width=225, height=66, icon="◇", enabled=False,
        )
        self.reconstruction_btn.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # Compact project summary; no duplicate statistics card.
        summary = NeonCard(right, accent=self.colors["border"], bg=self.colors["panel"], height=405)
        summary.pack(fill="x")
        sc = summary.content
        tk.Label(sc, text="État du projet", bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(0, 5))
        self._cyber_summary_row(sc, "▰", "Jeu", self.game_name_display, self.colors["accent"])
        self._cyber_summary_row(sc, "●", "Statut", self.project_status, self.colors["success"])
        self._cyber_summary_row(sc, "◉", "Compatibilité", self.compatibility_display, self.colors["accent"])
        self._cyber_summary_row(sc, "⌘", "Moteur", self.engine_display, self.colors["accent"])
        self._cyber_summary_row(sc, "◇", "Essentials", self.essentials_display, self.colors["accent"])
        self._cyber_summary_row(sc, "▧", "Cartes", self.maps_display, self.colors["accent"])
        self._cyber_summary_row(sc, "▤", "Textes", self.texts_display, self.colors["accent"])

        reminder = tk.Frame(sc, bg="#101b17", highlightbackground="#2f5744", highlightthickness=1,
                            padx=11, pady=9)
        reminder.pack(fill="x", pady=(12, 0))
        tk.Label(reminder, text="Conseil", bg="#101b17", fg=self.colors["success"],
                 font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Label(reminder, text="Jouez dans la VERSION_FR créée par le logiciel et conservez toujours le dossier original.",
                 bg="#101b17", fg="#b7c8be", wraplength=275, justify="left",
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

        help_card = NeonCard(right, accent=self.colors["border"], bg=self.colors["panel"], height=205)
        help_card.pack(fill="x", pady=(12, 0))
        hc = help_card.content
        tk.Label(hc, text="Besoin d'aide ?", bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        tk.Label(hc, text="Le tutoriel explique le parcours débutant. Les détails techniques restent cachés par défaut.",
                 bg=self.colors["panel"], fg=self.colors["muted"], wraplength=275,
                 justify="left", font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 9))
        help_row = tk.Frame(hc, bg=self.colors["panel"])
        help_row.pack(fill="x")
        NeonButton(help_row, "TUTORIEL", self.open_tutorial, self.colors["accent"],
                   width=132, height=40, icon="?").pack(side="left")
        self.details_toggle = NeonButton(help_row, "DÉTAILS", self._toggle_technical_details,
                                         self.colors["accent"], width=132, height=40, icon="▣")
        self.details_toggle.pack(side="right")
        NeonButton(hc, "EXPORTER UN DIAGNOSTIC", self.export_public_diagnostic,
                   self.colors["accent"], width=272, height=38, icon="⇩").pack(fill="x", pady=(8, 0))

        # Technical workspace is built but hidden for beginners.
        self.details_card = NeonCard(content, accent=self.colors["border"], bg=self.colors["panel"], height=245)
        dc = self.details_card.content
        details_header = tk.Frame(dc, bg=self.colors["panel"])
        details_header.pack(fill="x", pady=(0, 8))
        tk.Label(details_header, text="Détails techniques", bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 10)).pack(side="left")
        tk.Label(details_header, text="Facultatif", bg=self.colors["panel2"], fg=self.colors["muted"],
                 padx=8, pady=3, font=("Segoe UI", 7)).pack(side="right")
        self.notebook = ttk.Notebook(dc)
        self.notebook.pack(fill="both", expand=True)
        tabs = []
        for title in ["RÉSUMÉ", "FICHIERS", "EXTRACTION", "STATISTIQUES", "JOURNAL"]:
            frame = tk.Frame(self.notebook, bg=self.colors["panel"])
            self.notebook.add(frame, text=title)
            tabs.append(frame)
        self.summary_text = self._make_text(tabs[0]); self.summary_text.pack(fill="both", expand=True)
        columns = ("type", "file", "role")
        self.files_tree = ttk.Treeview(tabs[1], columns=columns, show="headings")
        for column, title, width in [("type", "Type", 150), ("file", "Fichier", 470), ("role", "Rôle probable", 420)]:
            self.files_tree.heading(column, text=title)
            self.files_tree.column(column, width=width)
        self.files_tree.pack(side="left", fill="both", expand=True)
        files_scroll = ttk.Scrollbar(tabs[1], orient="vertical", command=self.files_tree.yview)
        files_scroll.pack(side="right", fill="y")
        self.files_tree.configure(yscrollcommand=files_scroll.set)
        self.extraction_text = self._make_text(tabs[2]); self.extraction_text.pack(fill="both", expand=True)
        self.stats_text = self._make_text(tabs[3]); self.stats_text.pack(fill="both", expand=True)
        self.log_text = self._make_text(tabs[4]); self.log_text.pack(fill="both", expand=True)

        footer = tk.Frame(root_shell, bg=self.colors["panel"], highlightbackground=self.colors["border"],
                          highlightthickness=1, height=30)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Label(footer, text="●  Prêt", bg=self.colors["panel"], fg=self.colors["success"],
                 font=("Segoe UI Semibold", 8)).pack(side="left", padx=18)
        tk.Label(footer, text="Pokémon Fangame Translator v1.0.2 • Bêta publique", bg=self.colors["panel"],
                 fg=self.colors["muted"], font=("Segoe UI", 8)).pack(side="left", expand=True)
        tk.Label(footer, text="Original préservé", bg=self.colors["panel"], fg=self.colors["success"],
                 font=("Segoe UI", 8)).pack(side="right", padx=18)

    def _toggle_technical_details(self, force=None):
        target = (not self.details_visible) if force is None else bool(force)
        if target == self.details_visible:
            return
        self.details_visible = target
        if target:
            self.details_card.pack(fill="both", expand=True, pady=(12, 14))
            self.details_toggle.button_text = "MASQUER"
        else:
            self.details_card.pack_forget()
            self.details_toggle.button_text = "DÉTAILS"
        self.details_toggle._draw()

    def _draw_header(self, canvas):
        def redraw(_e=None):
            canvas.delete("all")
            w=max(900,canvas.winfo_width()); h=150
            canvas.create_rectangle(0,0,w,h,fill="#05070b",outline="")
            # abstract cyberpunk skyline
            canvas.create_rectangle(w*0.55,0,w,h,fill="#090318",outline="")
            for i in range(18):
                x=int(w*0.55+i*(w*0.45/18)); bh=35+(i*23)%75
                canvas.create_rectangle(x,h-bh,x+18,h,fill="#100827",outline="#24114b")
                if i%2==0: canvas.create_line(x+4,h-bh+10,x+4,h-5,fill="#ff2d7d",width=1)
                else: canvas.create_line(x+9,h-bh+8,x+9,h-6,fill="#00e5ff",width=1)
            canvas.create_line(0,h-2,w,h-2,fill="#ff2d7d",width=2)
            canvas.create_line(w*0.35,h-4,w,h-4,fill="#00e5ff",width=1)
            try:
                self._header_logo=tk.PhotoImage(file=str(self.base_dir/"assets"/"pft_neon_logo_v06.png")).subsample(2,2)
                canvas.create_image(74,75,image=self._header_logo)
            except tk.TclError:
                canvas.create_oval(25,25,123,123,outline=self.colors["accent"],width=3)
            canvas.create_text(150,45,text="POKÉMON",anchor="w",fill="#f3f5f8",font=("Segoe UI Black",28))
            canvas.create_text(150,83,text="FANGAME",anchor="w",fill=self.colors["pink"],font=("Segoe UI Black",24))
            canvas.create_text(315,83,text="TRANSLATOR",anchor="w",fill="#f3f5f8",font=("Segoe UI Black",24))
            canvas.create_text(152,113,text="Traduisez vos fangames Pokémon en quelques clics.",anchor="w",fill="#b4becd",font=("Segoe UI",9))
            canvas.create_rectangle(w-258,20,w-20,100,fill="#07120c",outline="#1edb74",width=1)
            canvas.create_text(w-235,42,text="♢  MODE SÉCURISÉ",anchor="w",fill="#41ef8c",font=("Segoe UI Semibold",10))
            canvas.create_text(w-235,67,text="Aucune modification du jeu",anchor="w",fill="#b3c0bb",font=("Segoe UI",8))
            canvas.create_text(w-235,87,text="✓  100% sécurisé",anchor="w",fill="#41ef8c",font=("Segoe UI",8))
        canvas.bind("<Configure>",redraw); redraw()

    def _side_item(self, parent, icon, text, command, accent, active=False):
        bg="#170b12" if active else "#070a10"
        f=tk.Frame(parent,bg=bg,highlightbackground=accent if active else "#070a10",highlightthickness=1 if active else 0,cursor="hand2")
        f.pack(fill="x",padx=8,pady=3)
        l1=tk.Label(f,text=icon,bg=bg,fg=accent,font=("Segoe UI",13),width=3,pady=9); l1.pack(side="left")
        l2=tk.Label(f,text=text,bg=bg,fg=accent if active else "#b3becf",font=("Segoe UI Semibold",8)); l2.pack(side="left")
        for widget in (f,l1,l2): widget.bind("<Button-1>",lambda _e: command())
        self.side_buttons.append(f)

    def _workflow_step(self, parent, num, title, sub, accent, active):
        bg="#160b12" if active else "#070a11"
        f=tk.Frame(parent,bg=bg,padx=8,pady=5)
        tk.Label(f,text=num,bg=bg,fg=accent,font=("Segoe UI Semibold",15),width=3).pack(side="left")
        tx=tk.Frame(f,bg=bg); tx.pack(side="left")
        tk.Label(tx,text=title,bg=bg,fg=accent if active else "#d6dce6",font=("Segoe UI Semibold",9)).pack(anchor="w")
        tk.Label(tx,text=sub,bg=bg,fg="#8a96aa",font=("Segoe UI",7)).pack(anchor="w")
        return f

    def _workflow_arrow(self, parent):
        return tk.Label(parent,text="→",bg="#070a11",fg="#596273",font=("Segoe UI",16))

    def _cyber_summary_row(self, parent, icon, label, variable, accent):
        row = tk.Frame(parent, bg=self.colors["panel"], pady=7)
        row.pack(fill="x")
        tk.Label(row, text=icon, bg=self.colors["panel"], fg=accent,
                 font=("Segoe UI", 9), width=3).pack(side="left")
        tk.Label(row, text=label, bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(side="left")
        tk.Label(row, textvariable=variable, bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI Semibold", 8), anchor="e").pack(side="right")
        tk.Frame(parent, bg=self.colors["border"], height=1).pack(fill="x")

    def _stat_tile(self,parent,icon,variable,label,row,col):
        f=tk.Frame(parent,bg="#110b19",highlightbackground="#5a2a78",highlightthickness=1,padx=9,pady=6)
        f.grid(row=row,column=col,sticky="nsew",padx=4,pady=4)
        parent.grid_columnconfigure(col,weight=1); parent.grid_rowconfigure(row,weight=1)
        tk.Label(f,text=icon,bg="#110b19",fg=self.colors["purple"],font=("Segoe UI",11)).pack(side="left")
        t=tk.Frame(f,bg="#110b19"); t.pack(side="left",padx=(8,0))
        tk.Label(t,textvariable=variable,bg="#110b19",fg=self.colors["purple"],font=("Segoe UI Semibold",11)).pack(anchor="w")
        tk.Label(t,text=label,bg="#110b19",fg="#b49ac6",font=("Segoe UI",7)).pack(anchor="w")

    def _show_tab(self,index):
        if hasattr(self, "notebook"):
            if hasattr(self, "details_visible") and not self.details_visible:
                self._toggle_technical_details(force=True)
            self.notebook.select(index)

    def _focus_analysis(self):
        self._show_tab(0)
        if hasattr(self,"analyze_btn"):
            self.analyze_btn.focus_set()

    def _adapter_can(self, capability: GameCapability) -> bool:
        return bool(self.detection_result and self.detection_result.can(capability))

    def _refresh_action_buttons(self) -> None:
        has_project_csv = bool(
            self.translation_csv_path and Path(self.translation_csv_path).is_file()
        )
        pending_detection = self.detection_result is None
        extract_allowed = self._adapter_can(GameCapability.EXTRACT)
        translate_allowed = self._adapter_can(GameCapability.TRANSLATE)
        reconstruct_allowed = self._adapter_can(GameCapability.RECONSTRUCT)
        deep_analysis_allowed = self._adapter_can(GameCapability.DEEP_ANALYZE)

        layouts = (
            (self.extract_btn, pending_detection or extract_allowed, {"side": "left", "fill": "both", "expand": True, "padx": (0, 6)}),
            (self.translate_btn, pending_detection or translate_allowed, {"side": "left", "fill": "both", "expand": True, "padx": 6}),
            (self.reconstruction_btn, pending_detection or reconstruct_allowed, {"side": "left", "fill": "both", "expand": True, "padx": (6, 0)}),
        )
        for button, visible, pack_options in layouts:
            if visible and not button.winfo_manager():
                button.pack(**pack_options)
            elif not visible and button.winfo_manager():
                button.pack_forget()

        self.extract_btn.set_enabled(extract_allowed)
        self.translate_btn.set_enabled(has_project_csv and translate_allowed)
        self.reconstruction_btn.set_enabled(has_project_csv and reconstruct_allowed)
        if hasattr(self, "deep_analyze_btn"):
            self.deep_analyze_btn.set_enabled(deep_analysis_allowed)
        self.file_menu.entryconfigure(
            "Ouvrir le studio de traduction",
            state="normal" if has_project_csv and translate_allowed else "disabled",
        )
        self.file_menu.entryconfigure(
            "Analyser en profondeur",
            state="normal" if deep_analysis_allowed else "disabled",
        )

    def open_translation_studio(self):
        if not self._adapter_can(GameCapability.TRANSLATE):
            messagebox.showerror(
                "Analyse compatible requise",
                "La traduction reste désactivée tant que l'analyse n'a pas reconnu "
                "une structure prise en charge avec une confiance suffisante.",
            )
            return

        csv_path = self.translation_csv_path
        if not csv_path:
            candidate = self._outputs_dir() / "textes_structures.csv"
            if candidate.exists():
                csv_path = candidate

        if not csv_path or not Path(csv_path).exists():
            chosen = filedialog.askopenfilename(
                title="Ouvrir le CSV structuré à traduire",
                filetypes=[("CSV Pokémon Fangame Translator", "*.csv"), ("Tous les fichiers", "*.*")],
            )
            if not chosen:
                messagebox.showinfo(
                    "CSV nécessaire",
                    "Extrayez d'abord les textes ou ouvrez un fichier textes_structures.csv."
                )
                return
            csv_path = Path(chosen)

        window = TranslationStudio(
            self,
            Path(csv_path),
            self.colors,
            logger=self._log,
        )
        self.translation_windows.append(window)

    def _future_translation(self):
        self.open_translation_studio()

    def open_reconstruction_studio(self):
        if not self._adapter_can(GameCapability.RECONSTRUCT):
            messagebox.showerror(
                "Reconstruction bloquée",
                "La reconstruction est interdite pour une structure inconnue, "
                "incomplète ou détectée avec ambiguïté.",
            )
            return

        if not self.game_dir:
            messagebox.showinfo(
                "Fangame nécessaire",
                "Choisis d'abord le dossier complet du fangame."
            )
            return

        csv_path = self.translation_csv_path
        if not csv_path:
            candidate = self._outputs_dir() / "textes_structures.csv"
            if candidate.exists():
                csv_path = candidate
        if not csv_path or not Path(csv_path).exists():
            messagebox.showinfo(
                "Traductions nécessaires",
                "Analyse et extrais d'abord les textes, puis traduis au moins un petit lot."
            )
            return

        project_dir = self.project_dir or self._activate_project(self.game_dir)
        window = ReconstructionStudio(
            self,
            self.game_dir,
            Path(csv_path),
            Path(project_dir),
            self.colors,
            logger=self._log,
        )
        self.translation_windows.append(window)

    def _future_reconstruction(self):
        self.open_reconstruction_studio()
    def _make_text(self, parent):
        return tk.Text(parent,wrap="word",bg=self.colors["panel2"],fg=self.colors["text"],insertbackground=self.colors["text"],selectbackground=self.colors["accent_dark"],relief="flat",highlightbackground=self.colors["border"],highlightthickness=1,padx=12,pady=10,font=("Cascadia Mono",9),state="disabled")

    def _set_text(self, widget: tk.Text, value: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _append_text(self, widget: tk.Text, value: str):
        widget.configure(state="normal")
        widget.insert("end", value)
        widget.see("end")
        widget.configure(state="disabled")

    def _log(self, message: str):
        self._append_text(
            self.log_text,
            f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
        )
        self.update_idletasks()

    def _activate_project(self, game_root: Path) -> Path:
        project = project_directory_for_game(game_root)
        project.mkdir(parents=True, exist_ok=True)
        (project / "Sauvegardes").mkdir(exist_ok=True)
        (project / "Rapports").mkdir(exist_ok=True)
        write_project_identity(project, game_root)
        self.project_dir = project
        existing = project / "textes_structures.csv"
        self.translation_csv_path = existing if existing.exists() else None
        return project

    def _backup_existing_project_csv(self, csv_path: Path) -> Path | None:
        return backup_project_csv(csv_path)

    def choose_game(self):
        chosen = filedialog.askdirectory(title="Choisir le dossier principal de votre fangame")
        if not chosen:
            return

        self.game_dir = Path(chosen)
        try:
            project = self._activate_project(self.game_dir)
        except (OSError, ValueError) as exc:
            self.game_dir = None
            messagebox.showerror("Dossier refusé", str(exc))
            return
        self.last_diagnostic = None
        self.last_deep_analysis = None
        self.detection_result = None
        self.extracted_count = 0
        self.path_var.set(str(self.game_dir))
        self.game_name_display.set(self.game_dir.name)
        self.analyze_btn.set_enabled(True)
        if hasattr(self, "quick_analyze_btn"):
            self.quick_analyze_btn.set_enabled(True)
        self._refresh_action_buttons()
        self.compatibility_display.set("-- / 100")
        self.maps_display.set("--")
        self.texts_display.set("--")
        self.engine_display.set("--")
        self.essentials_display.set("--")
        self.files_display.set("--")
        self._clear_views()
        self.status_var.set("Jeu sélectionné. Vous pouvez lancer l’analyse.")
        self.project_status.set("Prêt à analyser")
        self._log(f"Dossier sélectionné : {self.game_dir}")
        self._log(f"Projet persistant : {project}")
        if self.translation_csv_path:
            self.project_status.set("Projet existant chargé")
            self.status_var.set("Projet existant chargé automatiquement. Tes traductions sont conservées.")
            self._log("Projet existant chargé automatiquement : traductions et relecture conservées.")

    @staticmethod
    def _safe_relative(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(path)

    @staticmethod
    def _read_game_ini(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        parser = configparser.ConfigParser()
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except UnicodeDecodeError:
            parser.read(path, encoding="cp1252")
        result = {}
        for section in parser.sections():
            for key, value in parser.items(section):
                result[f"{section}.{key}"] = value
        return result

    @staticmethod
    def _detect_essentials_version(root: Path) -> str:
        candidates = [
            root / "Data" / "Scripts.rxdata",
            root / "Data" / "PluginScripts.rxdata",
            root / "PBS" / "metadata.txt",
            root / "PBS" / "pokemon.txt",
            root / "Scripts" / "Settings.rb",
            root / "Data" / "messages.dat",
        ]

        patterns = [
            re.compile(rb"Essentials\s+v?(\d+(?:\.\d+)*)", re.I),
            re.compile(rb"Pokemon Essentials\s+v?(\d+(?:\.\d+)*)", re.I),
            re.compile(rb"ESSENTIALS_VERSION.{0,30}?(\d+(?:\.\d+)*)", re.I),
        ]

        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            try:
                raw = path.read_bytes()[:8_000_000]
            except OSError:
                continue

            for pattern in patterns:
                match = pattern.search(raw)
                if match:
                    return match.group(1).decode("ascii", "ignore")

        if (root / "PBS").is_dir():
            return "inconnue (structure PBS détectée)"
        return "inconnue"

    def run_diagnostic(self):
        if not self.game_dir:
            return

        root = self.game_dir
        detection = self.adapter_registry.detect(root)
        self.detection_result = detection
        self.progress["value"] = 0
        self.progress_percent.set("0%")
        self.status_var.set("Analyse en cours…")
        self._clear_views()
        self._log("Début du diagnostic. Aucun fichier ne sera modifié.")
        if self.project_dir:
            try:
                write_project_identity(
                    self.project_dir,
                    root,
                    adapter_id=detection.adapter_id,
                    adapter_version=detection.recognized_version,
                )
            except (OSError, ProjectIdentityError) as exc:
                self._log(
                    "Avertissement : identité du projet non mise à jour ; "
                    f"la reconstruction restera bloquée ({exc})."
                )

        data = root / "Data"
        graphics = root / "Graphics"
        audio = root / "Audio"
        game_exe = root / "Game.exe"
        game_ini = root / "Game.ini"
        ini_values = self._read_game_ini(game_ini)

        self.progress["value"] = 12

        maps = sorted(data.glob("Map*.rxdata")) if data.is_dir() else []
        map_files = [p for p in maps if re.fullmatch(r"Map\d{3,4}\.rxdata", p.name, re.I)]
        rxdata_files = sorted(data.rglob("*.rxdata")) if data.is_dir() else []
        dat_files = sorted(data.rglob("*.dat")) if data.is_dir() else []

        self.progress["value"] = 36

        scripts_rxdata = data / "Scripts.rxdata"
        common_events = data / "CommonEvents.rxdata"
        system_rxdata = data / "System.rxdata"
        map_infos = data / "MapInfos.rxdata"

        encrypted = []
        for name in [
            "Game.rgssad", "Game.rgss2a", "Game.rgss3a",
            "Data.rgssad", "Data.rgss2a", "Data.rgss3a",
        ]:
            if (root / name).exists():
                encrypted.append(name)

        message_banks = []
        probable_sources = []
        excluded = []

        for p in dat_files:
            lower = p.name.lower()
            rel = self._safe_relative(p, root)
            if any(word in lower for word in ["message", "text", "dialog"]):
                message_banks.append(rel)
            if any(word in lower for word in ["message", "item", "move", "abilit", "trainer", "town_map"]):
                probable_sources.append(rel)

        for p in rxdata_files:
            rel = self._safe_relative(p, root)
            if p.name.startswith("Map") or p.name in {"CommonEvents.rxdata", "MapInfos.rxdata"}:
                probable_sources.append(rel)
            if p.name in {"Scripts.rxdata", "PluginScripts.rxdata"}:
                excluded.append(rel)

        for p in root.rglob("*.rb"):
            excluded.append(self._safe_relative(p, root))

        self.progress["value"] = 60

        rpg_maker_xp = bool(
            game_exe.exists()
            and game_ini.exists()
            and data.is_dir()
            and (system_rxdata.exists() or map_infos.exists())
        )

        essentials = detection.adapter_id == "pokemon_essentials"
        version = detection.recognized_version or self._detect_essentials_version(root)

        warnings = list(detection.warnings)
        notes = [f"Adaptateur sélectionné : {detection.display_name} ({detection.adapter_id})."]
        notes.extend(item.explanation for item in detection.evidence)

        if encrypted:
            warnings.append(
                "Archive RPG Maker chiffrée détectée : l'extraction peut être partielle."
            )
        if not data.is_dir():
            warnings.append("Le dossier Data est absent.")
        if scripts_rxdata.exists():
            notes.append("Scripts.rxdata détecté : toujours exclu des modifications.")
        if map_files:
            notes.append(f"{len(map_files)} carte(s) RPG Maker détectée(s).")
        if message_banks:
            notes.append(f"{len(message_banks)} banque(s) de messages probable(s).")
        if ini_values.get("Game.Library"):
            notes.append(f"Bibliothèque déclarée : {ini_values['Game.Library']}")

        score = detection.confidence
        if detection.adapter_recognized and not detection.write_actions_allowed:
            level = "Profil reconnu — analyse en lecture seule"
        elif not detection.write_actions_allowed:
            level = "Structure inconnue ou détection incertaine"
        else:
            level = "Élevée" if score >= 80 else "Moyenne"

        diagnostic = Diagnostic(
            root=str(root),
            adapter_id=detection.adapter_id,
            adapter_display_name=detection.display_name,
            detection_confidence=detection.confidence,
            write_actions_allowed=detection.write_actions_allowed,
            adapter_ambiguous=detection.ambiguous,
            detection_evidence=[
                f"{item.relative_path} — {item.explanation} (+{item.weight})"
                for item in detection.evidence
            ],
            rpg_maker_xp_detected=rpg_maker_xp,
            pokemon_essentials_detected=essentials,
            probable_essentials_version=version,
            game_exe_present=game_exe.exists(),
            game_ini_present=game_ini.exists(),
            data_folder_present=data.is_dir(),
            graphics_folder_present=graphics.is_dir(),
            audio_folder_present=audio.is_dir(),
            scripts_rxdata_present=scripts_rxdata.exists(),
            common_events_present=common_events.exists(),
            system_rxdata_present=system_rxdata.exists(),
            map_infos_present=map_infos.exists(),
            map_count=len(map_files),
            rxdata_count=len(rxdata_files),
            dat_count=len(dat_files),
            encrypted_archives=encrypted,
            message_banks=sorted(set(message_banks)),
            probable_text_sources=sorted(set(probable_sources)),
            excluded_technical_files=sorted(set(excluded)),
            compatibility_level=level,
            compatibility_score=score,
            warnings=warnings,
            notes=notes,
        )

        self.last_diagnostic = diagnostic
        self.progress["value"] = 100
        self.status_var.set(f"Analyse terminée — compatibilité {level.lower()} ({score}/100).")
        self.project_status.set("Analyse terminée")
        self.compatibility_display.set(f"{score} / 100")
        self.maps_display.set(str(len(map_files)))
        self.files_display.set(str(len(rxdata_files)))
        self.progress_percent.set("100%")
        self.engine_display.set(detection.display_name)
        self.essentials_display.set("Détecté" if essentials else "Non détecté")

        self._render_diagnostic(diagnostic)
        self._write_automatic_report(diagnostic)
        self._refresh_action_buttons()
        self._log(self.status_var.get())

    def run_deep_analysis(self):
        """Lance une validation analytique statique, sans exécuter le jeu."""
        if not self.game_dir or not self.detection_result:
            messagebox.showerror(
                "Analyse initiale requise",
                "Choisis le fangame et lance d'abord l'analyse de compatibilité.",
            )
            return
        if not self._adapter_can(GameCapability.DEEP_ANALYZE):
            messagebox.showerror(
                "Analyse indisponible",
                "Ce profil ne permet pas l'analyse approfondie en lecture seule.",
            )
            return

        root = self.game_dir
        self.deep_analyze_btn.set_enabled(False)
        self.progress["value"] = 0
        self.progress_percent.set("0%")
        self.status_var.set("Validation analytique approfondie en cours…")
        self.project_status.set("Analyse approfondie en cours")
        self._log("Début de l'analyse approfondie statique. Aucun script Ruby ne sera exécuté.")

        def progress(current: int, total: int, relative: str) -> None:
            percent = int(current * 100 / max(1, total))
            self.progress["value"] = percent
            self.progress_percent.set(f"{percent}%")
            self.status_var.set(f"Analyse approfondie : {relative}")
            self.update_idletasks()

        try:
            adapter = self.adapter_registry.adapter_for(self.detection_result)
            report = adapter.analyze(
                root,
                self.detection_result,
                mode="complete",
                progress=progress,
            )
            paths = write_analysis_reports(
                report,
                self._reports_dir(),
                original_root=root,
            )
        except Exception as exc:
            self.status_var.set("Analyse approfondie impossible.")
            self.project_status.set("Erreur d'analyse")
            self._log(f"Échec de l'analyse approfondie : {type(exc).__name__}: {exc}")
            messagebox.showerror(
                "Analyse approfondie impossible",
                f"L'analyse n'a pas pu être terminée.\n\n{type(exc).__name__}: {exc}",
            )
            self._refresh_action_buttons()
            return

        self.last_deep_analysis = report
        self.progress["value"] = 100
        self.progress_percent.set("100%")
        self.status_var.set(
            f"Validation analytique terminée — statut {report.status.upper()}."
        )
        self.project_status.set(f"Analyse approfondie : {report.status.upper()}")
        self._set_text(self.summary_text, deep_report_text(report))
        self._set_text(
            self.stats_text,
            "\n".join(
                [
                    "COUVERTURE FRANÇAISE ESTIMÉE",
                    "=" * 72,
                    f"Lignes analysées : {report.coverage.total_lines}",
                    f"Français probable : {report.coverage.line_counts['francais_probable']}",
                    f"Anglais probable : {report.coverage.line_counts['anglais_probable']}",
                    f"Textes mixtes : {report.coverage.line_counts['mixte']}",
                    f"Ambigus : {report.coverage.line_counts['ambigu']}",
                    f"Estimation par lignes : {report.coverage.french_line_percent:.2f}%",
                    f"Estimation par mots : {report.coverage.french_word_percent:.2f}%",
                    f"Estimation par caractères : {report.coverage.french_character_percent:.2f}%",
                    "",
                    "Cette estimation ne prouve pas que l'aventure complète a été jouée.",
                ]
            ),
        )
        self._toggle_technical_details(force=True)
        self.notebook.select(0)
        self._refresh_action_buttons()
        self._log(f"Rapport approfondi : {paths['text']}")
        self._log(f"Résumé Discord : {paths['discord']}")

        message = (
            f"Statut analytique : {report.status.upper()}\n\n"
            f"Cartes relues : {report.maps_analyzed}/{report.map_files_found}\n"
            f"Pages d'événements : {report.map_pages}\n"
            f"Alertes : {len(report.issues)}\n"
            f"Couverture française estimée : {report.coverage.french_line_percent:.2f}%\n\n"
            "L'aventure complète n'a pas été jouée physiquement de bout en bout.\n\n"
            f"Rapport :\n{paths['text']}"
        )
        if report.status == "vert":
            messagebox.showinfo("Validation analytique terminée", message)
        else:
            messagebox.showwarning("Validation analytique terminée avec réserves", message)

    def _clear_views(self):
        self._set_text(self.summary_text, "")
        self._set_text(self.extraction_text, "")
        for item in self.files_tree.get_children():
            self.files_tree.delete(item)

    def _render_diagnostic(self, d: Diagnostic):
        summary = [
            "RÉSULTAT DU DIAGNOSTIC",
            "=" * 72,
            f"Dossier : {d.root}",
            "",
            f"Compatibilité estimée : {d.compatibility_level}",
            f"Score : {d.compatibility_score}/100",
            "",
            f"Adaptateur : {d.adapter_display_name} ({d.adapter_id})",
            f"Confiance de détection : {d.detection_confidence}/100",
            f"Reconstruction et écriture dans le jeu : {'AUTORISÉES' if d.write_actions_allowed else 'BLOQUÉES'}",
            f"Détection ambiguë : {'OUI' if d.adapter_ambiguous else 'NON'}",
            "",
            f"RPG Maker XP détecté : {'OUI' if d.rpg_maker_xp_detected else 'NON'}",
            f"Pokémon Essentials détecté : {'OUI' if d.pokemon_essentials_detected else 'NON'}",
            f"Version détectée : {d.probable_essentials_version}",
            "",
            f"Cartes détectées : {d.map_count}",
            f"Fichiers RXDATA : {d.rxdata_count}",
            f"Fichiers DAT : {d.dat_count}",
            f"Banques de messages probables : {len(d.message_banks)}",
            f"Fichiers techniques exclus : {len(d.excluded_technical_files)}",
            "",
            "AVERTISSEMENTS",
            "-" * 72,
            *(d.warnings or ["Aucun avertissement majeur."]),
            "",
            "NOTES",
            "-" * 72,
            *(d.notes or ["Aucune note supplémentaire."]),
            "",
            "INDICES DE DÉTECTION",
            "-" * 72,
            *(d.detection_evidence or ["Aucun indice structurel suffisant."]),
            "",
            "PROCHAINE ÉTAPE",
            "-" * 72,
            (
                "Le jeu semble compatible avec l'extracteur structuré de la v1.0.2."
                if d.write_actions_allowed
                else "La structure doit être étudiée manuellement ; les actions d'écriture restent bloquées."
            ),
        ]
        self._set_text(self.summary_text, "\n".join(summary))

        def add_rows(kind: str, files: list[str], role: str):
            for file in files:
                self.files_tree.insert("", "end", values=(kind, file, role))

        add_rows("Banque de messages", d.message_banks, "Texte potentiellement traduisible")
        add_rows("Source probable", d.probable_text_sources, "À analyser pour extraction")
        add_rows("Exclu", d.excluded_technical_files, "Script ou fichier technique")
        add_rows("Archive chiffrée", d.encrypted_archives, "Peut limiter l'extraction")

    def extract_texts(self):
        """Extraction structurée en lecture seule des cartes, banques et PBS."""
        if not self.last_diagnostic or not self.game_dir:
            messagebox.showerror("Diagnostic requis", "Lance d'abord l'analyse du fangame.")
            return
        if not self._adapter_can(GameCapability.EXTRACT):
            messagebox.showerror(
                "Extraction bloquée",
                "La structure du jeu n'est pas reconnue avec assez de certitude. "
                "Aucun texte ne sera extrait afin d'éviter un traitement inadapté.",
            )
            return

        root = self.game_dir
        data = root / "Data"
        if not data.is_dir():
            messagebox.showerror("Dossier Data absent", "Le dossier Data est introuvable.")
            return

        out_dir = self._outputs_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "textes_structures.csv"
        compatibility_csv = out_dir / "textes_extraits.csv"
        report_path = out_dir / "RAPPORT_EXTRACTION_STRUCTUREE.txt"

        self.progress["value"] = 0
        self.progress_percent.set("0%")
        self.status_var.set("Lecture structurée des données RPG Maker…")
        self._log("Début de l'extraction structurée v1.0.2.")

        def progress(current, total, relative):
            percent = int(current * 100 / max(1, total))
            self.progress["value"] = percent
            self.progress_percent.set(f"{percent}%")
            self.status_var.set(f"Analyse structurée : {current}/{total} — {relative}")
            self.update_idletasks()

        try:
            adapter = self.adapter_registry.adapter_for(self.detection_result)
            rows, errors = adapter.extract(root, progress=progress, logger=self._log)
        except Exception as exc:
            messagebox.showerror(
                "Extraction impossible",
                f"Le parseur structuré n'a pas pu lire ce jeu.\n\n{type(exc).__name__}: {exc}"
            )
            self._log(f"Échec global : {type(exc).__name__}: {exc}")
            return

        if not rows:
            messagebox.showerror(
                "Aucun texte extractible",
                "L'extraction n'a produit aucune ligne vérifiable. Le projet existant "
                "n'a pas été modifié.",
            )
            self._log("Extraction vide refusée : projet existant conservé.")
            return

        try:
            existing_backup = self._backup_existing_project_csv(csv_path)
            merged_rows, preserved_translations, project_fields = merge_project_rows(rows, csv_path)
            write_project_csv(csv_path, merged_rows, project_fields)
        except (OSError, ProjectMergeError) as exc:
            messagebox.showerror(
                "Projet conservé",
                "La nouvelle extraction n'a pas remplacé le projet existant.\n\n"
                f"{exc}",
            )
            self._log(f"Réextraction annulée sans écrasement : {exc}")
            return
        try:
            atomic_write_bytes(compatibility_csv, csv_path.read_bytes())
        except OSError as exc:
            self._log(
                "Avertissement : copie CSV de compatibilité non mise à jour "
                f"({type(exc).__name__})."
            )
        rows = merged_rows

        by_type = {}
        by_file = {}
        for row in rows:
            by_type[row["type"]] = by_type.get(row["type"], 0) + 1
            by_file[row["fichier"]] = by_file.get(row["fichier"], 0) + 1

        dialogues = sum(1 for row in rows if row["type"] == "Dialogue")
        choices = sum(1 for row in rows if row["type"] == "Choix")
        bank_rows = sum(1 for row in rows if row["type"] == "Banque de messages")
        pbs_rows = sum(1 for row in rows if row["type"].startswith("PBS —"))
        unique_texts = len({row["texte_source"] for row in rows})
        duplicates = len(rows) - unique_texts
        protected = sum(1 for row in rows if row["codes_proteges"])

        report_lines = [
            "POKÉMON FANGAME TRANSLATOR v1.0.2 — RAPPORT D'EXTRACTION STRUCTURÉE",
            "=" * 82,
            f"Jeu : {root}",
            f"Textes structurés : {len(rows)}",
            f"Dialogues de cartes : {dialogues}",
            f"Choix du joueur : {choices}",
            f"Banques de messages : {bank_rows}",
            f"Champs PBS : {pbs_rows}",
            f"Textes uniques : {unique_texts}",
            f"Doublons potentiels : {duplicates}",
            f"Lignes avec commandes protégées : {protected}",
            f"Traductions conservées du projet : {preserved_translations}",
            f"Sauvegarde avant réextraction : {existing_backup or 'Aucune'}",
            "",
            "SÉCURITÉ",
            "-" * 82,
            "Aucun fichier du jeu n'a été modifié.",
            "Scripts.rxdata, PluginScripts.rxdata et les scripts Ruby sont exclus.",
            "L'extraction lit uniquement les fichiers originaux. La reconstruction écrit seulement dans une copie séparée.",
            "",
            "ERREURS DE LECTURE",
            "-" * 82,
            *(errors or ["Aucune erreur."]),
            "",
            "DÉTAIL PAR TYPE",
            "-" * 82,
            *(f"{count:6d}  {kind}" for kind, count in sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
            "",
            "DÉTAIL PAR FICHIER",
            "-" * 82,
            *(f"{count:6d}  {file}" for file, count in sorted(by_file.items(), key=lambda item: (-item[1], item[0]))),
        ]
        atomic_write_text(report_path, "\n".join(report_lines), encoding="utf-8")

        self._set_text(
            self.stats_text,
            "\n".join([
                "STATISTIQUES STRUCTURÉES v1.0.2",
                "=" * 72,
                f"Textes extraits : {len(rows)}",
                f"Dialogues complets : {dialogues}",
                f"Choix du joueur : {choices}",
                f"Banques de messages : {bank_rows}",
                f"Champs PBS : {pbs_rows}",
                f"Textes uniques : {unique_texts}",
                f"Doublons potentiels : {duplicates}",
                f"Commandes RPG protégées : {protected}",
                f"Erreurs de lecture : {len(errors)}",
                f"Traductions conservées : {preserved_translations}",
                "",
                "Chaque dialogue de carte possède maintenant son contexte :",
                "carte, événement, page et numéro de commande.",
            ])
        )

        self._set_text(
            self.extraction_text,
            "\n".join([
                "EXTRACTION STRUCTURÉE TERMINÉE",
                "=" * 72,
                f"Textes structurés : {len(rows)}",
                f"Dialogues : {dialogues}",
                f"Choix : {choices}",
                f"Banques de messages : {bank_rows}",
                f"Champs PBS : {pbs_rows}",
                "",
                f"CSV principal : {csv_path}",
                f"CSV compatible : {compatibility_csv}",
                f"Rapport : {report_path}",
                f"Projet persistant : {out_dir}",
                f"Traductions conservées : {preserved_translations}",
                "",
                "Aucun fichier du jeu n'a été modifié.",
            ])
        )

        self.progress["value"] = 100
        self.progress_percent.set("100%")
        self.status_var.set(f"Extraction structurée terminée — {len(rows)} texte(s).")
        self.project_status.set("Extraction structurée terminée")
        self.extracted_count = len(rows)
        self.texts_display.set(str(len(rows)))
        self.texts_display.set(str(len(rows)))
        self.files_display.set(str(len(by_file)))
        self._log(self.status_var.get())
        self.translation_csv_path = csv_path
        self._refresh_action_buttons()
        self._log(f"CSV structuré créé : {csv_path}")
        self._log("La traduction et la relecture intelligente sont prêtes.")

        messagebox.showinfo(
            "Extraction structurée terminée",
            f"{len(rows)} texte(s) proprement identifié(s).\n\n"
            f"Dialogues : {dialogues}\nChoix : {choices}\n"
            f"Banques : {bank_rows}\nPBS : {pbs_rows}\n\n"
            f"CSV :\n{csv_path}\n\nVous pouvez maintenant ouvrir le Studio de traduction."
        )

    def create_diagnostic_sample(self):
        """Crée, après avertissement, un ZIP privé sans résidu local."""
        if not self.game_dir:
            messagebox.showerror("Aucun fangame", "Choisis d'abord le dossier du fangame.")
            return

        root = self.game_dir
        if not (root / "Data").is_dir():
            messagebox.showerror("Dossier Data absent", "Le dossier Data est introuvable.")
            return

        if not messagebox.askyesno(
            "Échantillon privé",
            "Cette archive peut contenir des cartes, des dialogues et des données PBS "
            "appartenant au créateur du fangame.\n\n"
            "Ne la publie pas sur GitHub, Discord ou un forum public. Elle doit être "
            "transmise uniquement à une personne de confiance pour un diagnostic privé.\n\n"
            "Continuer ?",
        ):
            return

        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", root.name).strip("_") or "Fangame"
        chosen = filedialog.asksaveasfilename(
            title="Enregistrer l'échantillon privé de diagnostic",
            defaultextension=".zip",
            initialfile=f"Echantillon_prive_{safe_name}_v1.0.2.zip",
            filetypes=[("Archive ZIP", "*.zip")],
        )
        if not chosen:
            return

        zip_path = Path(chosen)
        try:
            copied = create_private_diagnostic_sample(
                root,
                zip_path,
                application_dir=self.base_dir,
            )
        except Exception as exc:
            self._log(f"Échec de l'échantillon privé : {type(exc).__name__}: {exc}")
            messagebox.showerror("Échantillon impossible", str(exc))
            return

        self._log(f"Échantillon privé créé : {zip_path} ({len(copied)} groupe(s) de fichiers).")
        messagebox.showinfo(
            "Échantillon privé créé",
            f"L'échantillon privé est prêt :\n\n{zip_path}\n\n"
            "Il peut contenir des données du jeu : ne le publie pas.\n"
            "Aucun script Ruby ni fichier exécutable n'a été inclus."
        )

    def _reports_dir(self) -> Path:
        folder = (self.project_dir / "Rapports") if self.project_dir else (self.base_dir / "Rapports")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _outputs_dir(self) -> Path:
        folder = self.project_dir if self.project_dir else (self.base_dir / "Sortie_Extraction")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _report_text(self, d: Diagnostic) -> str:
        lines = [
            f"{APP_TITLE} — RAPPORT DE DIAGNOSTIC",
            "=" * 74,
            f"Date : {datetime.now().isoformat(timespec='seconds')}",
            f"Dossier : {d.root}",
            "",
            f"Compatibilité : {d.compatibility_level}",
            f"Score : {d.compatibility_score}/100",
            f"Adaptateur : {d.adapter_display_name} ({d.adapter_id})",
            f"Confiance de détection : {d.detection_confidence}/100",
            f"Actions d'écriture : {'AUTORISÉES' if d.write_actions_allowed else 'BLOQUÉES'}",
            f"Détection ambiguë : {'OUI' if d.adapter_ambiguous else 'NON'}",
            f"RPG Maker XP : {'OUI' if d.rpg_maker_xp_detected else 'NON'}",
            f"Pokémon Essentials : {'OUI' if d.pokemon_essentials_detected else 'NON'}",
            f"Version probable : {d.probable_essentials_version}",
            "",
            f"Cartes : {d.map_count}",
            f"RXDATA : {d.rxdata_count}",
            f"DAT : {d.dat_count}",
            "",
            "INDICES DE DÉTECTION",
            "-" * 74,
            *(d.detection_evidence or ["Aucun indice structurel suffisant."]),
            "",
            "BANQUES DE MESSAGES",
            "-" * 74,
            *(d.message_banks or ["Aucune détectée."]),
            "",
            "SOURCES PROBABLES",
            "-" * 74,
            *(d.probable_text_sources or ["Aucune détectée."]),
            "",
            "FICHIERS EXCLUS",
            "-" * 74,
            *(d.excluded_technical_files or ["Aucun détecté."]),
            "",
            "AVERTISSEMENTS",
            "-" * 74,
            *(d.warnings or ["Aucun avertissement majeur."]),
            "",
            "SÉCURITÉ",
            "-" * 74,
            "Aucun fichier du jeu n'a été modifié.",
        ]
        return "\n".join(lines)

    def _write_automatic_report(self, diagnostic: Diagnostic):
        report = self._reports_dir() / "DERNIER_DIAGNOSTIC.txt"
        report.write_text(self._report_text(diagnostic), encoding="utf-8")
        self._log(f"Rapport automatique créé : {report}")

    def export_report(self):
        if not self.last_diagnostic:
            messagebox.showerror("Aucun diagnostic", "Lance d'abord une analyse.")
            return

        chosen = filedialog.asksaveasfilename(
            title="Exporter le rapport",
            defaultextension=".txt",
            filetypes=[("Rapport texte", "*.txt")]
        )
        if not chosen:
            return

        Path(chosen).write_text(self._report_text(self.last_diagnostic), encoding="utf-8")
        messagebox.showinfo("Rapport exporté", f"Rapport enregistré :\n{chosen}")

    @staticmethod
    def _count_project_statuses(csv_path: Path | None) -> dict[str, int]:
        counts = {
            "lignes": 0,
            "traduites": 0,
            "acceptees": 0,
            "pretes": 0,
            "a_verifier": 0,
            "bloquees": 0,
        }
        if not csv_path or not csv_path.exists():
            return counts
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=";")
                for row in reader:
                    counts["lignes"] += 1
                    if (row.get("traduction_fr") or "").strip():
                        counts["traduites"] += 1
                    status = (row.get("statut") or "").strip().casefold()
                    if status == "accepté".casefold():
                        counts["acceptees"] += 1
                    elif status == "prêt".casefold():
                        counts["pretes"] += 1
                    elif status == "à vérifier".casefold():
                        counts["a_verifier"] += 1
                    elif status == "bloqué".casefold():
                        counts["bloquees"] += 1
        except Exception:
            counts["lecture_impossible"] = 1
        return counts

    def _public_diagnostic_text(self) -> str:
        diagnostic = self.last_diagnostic
        project_counts = self._count_project_statuses(self.translation_csv_path)
        frozen = bool(getattr(sys, "frozen", False))
        lines = [
            "POKÉMON FANGAME TRANSLATOR v1.0.2 — DIAGNOSTIC PUBLIC",
            "=" * 78,
            f"Date : {datetime.now().isoformat(timespec='seconds')}",
            f"Application empaquetée : {'OUI' if frozen else 'NON'}",
            f"Système : {platform.system()} {platform.release()} ({platform.machine()})",
            f"Version Python intégrée : {platform.python_version()}",
            "",
            "CONFIDENTIALITÉ",
            "-" * 78,
            "Ce diagnostic ne contient aucun dialogue, aucune traduction et aucun chemin utilisateur complet.",
            "",
            "ÉTAT DU PARCOURS",
            "-" * 78,
            f"Jeu sélectionné : {'OUI' if self.game_dir else 'NON'}",
            f"Nom du dossier du jeu : {self.game_dir.name if self.game_dir else 'Non sélectionné'}",
            f"Analyse terminée : {'OUI' if diagnostic else 'NON'}",
            f"Extraction disponible : {'OUI' if self.translation_csv_path and self.translation_csv_path.exists() else 'NON'}",
            f"Projet persistant : {'OUI' if self.project_dir and self.project_dir.exists() else 'NON'}",
            "",
            "PROJET DE TRADUCTION",
            "-" * 78,
            f"Lignes : {project_counts.get('lignes', 0)}",
            f"Traduites : {project_counts.get('traduites', 0)}",
            f"Acceptées : {project_counts.get('acceptees', 0)}",
            f"Prêtes : {project_counts.get('pretes', 0)}",
            f"À vérifier : {project_counts.get('a_verifier', 0)}",
            f"Bloquées : {project_counts.get('bloquees', 0)}",
        ]
        if diagnostic:
            lines.extend([
                "",
                "COMPATIBILITÉ DÉTECTÉE",
                "-" * 78,
                f"Niveau : {diagnostic.compatibility_level}",
                f"Score : {diagnostic.compatibility_score}/100",
                f"Adaptateur : {diagnostic.adapter_display_name} ({diagnostic.adapter_id})",
                f"Confiance de détection : {diagnostic.detection_confidence}/100",
                f"Actions d'écriture : {'AUTORISÉES' if diagnostic.write_actions_allowed else 'BLOQUÉES'}",
                f"Détection ambiguë : {'OUI' if diagnostic.adapter_ambiguous else 'NON'}",
                f"RPG Maker XP : {'OUI' if diagnostic.rpg_maker_xp_detected else 'NON'}",
                f"Pokémon Essentials : {'OUI' if diagnostic.pokemon_essentials_detected else 'NON'}",
                f"Version probable : {diagnostic.probable_essentials_version}",
                f"Cartes : {diagnostic.map_count}",
                f"Fichiers RXDATA : {diagnostic.rxdata_count}",
                f"Fichiers DAT : {diagnostic.dat_count}",
                f"Banques de messages : {len(diagnostic.message_banks)}",
                f"Archives détectées : {len(diagnostic.encrypted_archives)}",
                "",
                "INDICES STRUCTURELS (chemins relatifs uniquement)",
                "-" * 78,
                *(diagnostic.detection_evidence or ["Aucun indice structurel suffisant."]),
                "",
                "AVERTISSEMENTS",
                "-" * 78,
                *(diagnostic.warnings or ["Aucun avertissement majeur."]),
            ])
        if self.last_deep_analysis:
            deep = self.last_deep_analysis
            lines.extend([
                "",
                "VALIDATION ANALYTIQUE APPROFONDIE",
                "-" * 78,
                f"Statut : {deep.status.upper()}",
                f"Cartes relues : {deep.maps_analyzed}/{deep.map_files_found}",
                f"Pages d'événements : {deep.map_pages}",
                f"Événements communs : {deep.common_events_analyzed}/{deep.common_events_found}",
                f"Références statiques manquantes : {deep.missing_static_references}",
                f"Couverture française estimée : {deep.coverage.french_line_percent:.2f}% des lignes classables",
                f"Sources non vérifiables ou incomplètes : {'OUI' if deep.coverage.incomplete_sources else 'NON'}",
                "L'aventure complète n'a pas été jouée physiquement de bout en bout.",
            ])
        lines.extend([
            "",
            "INFORMATIONS DE SUPPORT",
            "-" * 78,
            "Joindre ce fichier à un rapport de bug. Ne pas joindre le fangame complet.",
            "Projet indépendant et non officiel. Aucun jeu ni fichier de jeu n'est distribué.",
        ])
        return "\n".join(lines)

    def export_public_diagnostic(self):
        initial = f"Diagnostic_PFT_v1.0.2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        chosen = filedialog.asksaveasfilename(
            title="Exporter un diagnostic public",
            defaultextension=".txt",
            initialfile=initial,
            filetypes=[("Rapport texte", "*.txt")],
        )
        if not chosen:
            return
        Path(chosen).write_text(self._public_diagnostic_text(), encoding="utf-8")
        messagebox.showinfo(
            "Diagnostic exporté",
            "Le diagnostic public a été créé. Il ne contient ni dialogues ni traductions.\n\n"
            f"{chosen}",
        )

    def open_outputs_folder(self):
        folder = self._outputs_dir()
        try:
            os.startfile(folder)
        except AttributeError:
            messagebox.showinfo("Dossier des résultats", str(folder))

    def open_tutorial(self):
        win = tk.Toplevel(self)
        win.title("Tutoriel — Pokémon Fangame Translator v1.0.2")
        win.geometry("920x680")
        win.configure(bg=self.colors["bg"])
        win.transient(self)

        text = tk.Text(
            win,
            wrap="word",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            padx=18,
            pady=16,
            font=("Segoe UI", 10),
        )
        text.pack(fill="both", expand=True, padx=14, pady=14)

        tutorial = r"""
POKÉMON FANGAME TRANSLATOR v1.0.2 — GUIDE RAPIDE

INSTALLATION PUBLIQUE
1. Lance Pokemon_Fangame_Translator_Setup_v1.0.2.exe.
2. Termine l'installation puis ouvre l'application depuis le Bureau ou le menu Démarrer.
3. Python et Inno Setup ne sont pas nécessaires pour utiliser l'application.

PARCOURS DÉBUTANT
1. Choisis le dossier principal du fangame.
2. Lance l'analyse puis l'extraction.
3. Ouvre Traduire et commence par un lot de 20 textes.
4. Vérifie les alertes, accepte les textes prêts et enregistre.
5. Ouvre Reconstruction, lance la simulation et crée la VERSION_FR.
6. Joue uniquement dans la VERSION_FR et conserve l'original intact.

COMMANDES DU JEU
Des éléments comme \\n, \\c[1], \\PN ou \\v[1] ne sont pas du texte normal.
Ils doivent rester identiques dans la traduction. Le bouton « Restaurer les commandes »
répare automatiquement les cas simples, notamment un vrai saut de ligne utilisé à la
place de \\n. Les cas complexes restent bloqués par sécurité.

DIAGNOSTIC
Le bouton « Exporter un diagnostic » crée un rapport de support sans dialogues,
sans traductions et sans chemin utilisateur complet.

LIMITES
La traduction est automatique et peut être maladroite. Cette bêta vise certains
fangames RPG Maker XP / Pokémon Essentials avec Data/PBS accessibles. Les formats
personnalisés, archives chiffrées et scripts peuvent ne pas être pris en charge.

PROJET INDÉPENDANT
Ce logiciel n'est affilié ni à Nintendo, Game Freak, The Pokémon Company, ni aux
créateurs des fangames. Aucun jeu ni fichier de jeu n'est fourni.
"""
        text.insert("1.0", tutorial.strip())
        text.configure(state="disabled")

    def open_about(self):
        messagebox.showinfo(
            "À propos",
            f"{APP_TITLE}\n\n"
            "Bêta publique open source sous licence GPL-3.0-or-later.\n\n"
            "Cet outil accélère une traduction automatique et ne remplace pas une équipe humaine. "
            "Projet indépendant et non officiel, sans affiliation avec Nintendo, Game Freak, "
            "The Pokémon Company ou les créateurs des fangames. Aucun jeu n'est fourni."
        )



if __name__ == "__main__":
    FangameTranslatorApp().mainloop()
