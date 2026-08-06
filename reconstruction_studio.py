# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from reconstruction_engine import (
    ReconstructionError,
    ReconstructionPlan,
    build_plan,
    reconstruct_copy,
    save_plan,
    simulate_plan,
)



def friendly_reason(reason: str) -> str:
    value = reason or "Raison inconnue"
    lowered = value.casefold()
    if "retours de ligne incompatibles" in lowered or "nombre de lignes incompatible" in lowered:
        return "Retours à la ligne différents : les commandes \\n ne correspondent pas"
    if "commande" in lowered and "modifi" in lowered:
        return "Commande du jeu modifiée ou manquante"
    if "non traduit" in lowered:
        return "Texte non traduit"
    if "statut" in lowered or "accept" in lowered or "prêt" in lowered:
        return "Texte non validé pour ce mode"
    if "format" in lowered and "pris en charge" in lowered:
        return "Format non pris en charge ; texte laissé en anglais"
    return value


class ReconstructionStudio(tk.Toplevel):
    def __init__(
        self,
        master,
        game_root: Path,
        csv_path: Path,
        project_dir: Path,
        colors: dict,
        logger=None,
    ):
        super().__init__(master)
        self.title("Reconstruction sécurisée — Pokémon Fangame Translator v1.0.2")
        self.geometry("1180x790")
        self.minsize(980, 700)
        self.configure(bg=colors["bg"])
        self.transient(master)

        self.game_root = Path(game_root).resolve()
        self.csv_path = Path(csv_path).resolve()
        self.project_dir = Path(project_dir).resolve()
        self.reports_dir = self.project_dir / "Rapports"
        self.colors = colors
        self.logger = logger or (lambda _message: None)
        self.plan: ReconstructionPlan | None = None
        self.running = False

        target_name = re.sub(r"[^A-Za-z0-9À-ÿ._ -]+", "_", self.game_root.name).strip() or "Fangame"
        self.default_target = self.game_root.parent / f"{target_name}_VERSION_FR"

        self._configure_styles()
        self._build_ui()
        self.after(150, self.run_simulation)

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Rebuild.Horizontal.TProgressbar",
            troughcolor=self.colors["panel3"],
            background=self.colors["orange"],
            lightcolor=self.colors["orange"],
            darkcolor=self.colors["orange"],
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

    def _button(self, parent, text, command, accent=None, large=False):
        accent = accent or self.colors["accent"]
        return tk.Button(
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
            pady=12 if large else 8,
            cursor="hand2",
            font=("Segoe UI Semibold", 10 if large else 9),
        )

    def _build_ui(self):
        header = tk.Frame(self, bg=self.colors["bg"])
        header.pack(fill="x", padx=20, pady=(16, 10))
        tk.Label(
            header,
            text="◇  RECONSTRUCTION SÉCURISÉE",
            bg=self.colors["bg"],
            fg=self.colors["orange"],
            font=("Segoe UI Black", 20),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Le logiciel crée une version française séparée et jouable. Le jeu original reste intact.",
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        steps = tk.Frame(self, bg=self.colors["bg"])
        steps.pack(fill="x", padx=20, pady=(0, 10))
        for number, title, detail, accent in [
            ("1", "VÉRIFIER", "Simulation sans écriture", self.colors["accent"]),
            ("2", "COPIER", "Duplication complète du jeu", self.colors["purple"]),
            ("3", "RECONSTRUIRE", "Textes sûrs dans la copie", self.colors["orange"]),
            ("4", "TESTER", "Lancer la copie française", self.colors["success"]),
        ]:
            card = self._card(steps, accent, padx=11, pady=8)
            card.pack(side="left", fill="x", expand=True, padx=(0, 7))
            tk.Label(card, text=number, bg=self.colors["panel"], fg=accent, font=("Segoe UI Black", 15)).pack(side="left", padx=(0, 8))
            box = tk.Frame(card, bg=self.colors["panel"])
            box.pack(side="left")
            tk.Label(box, text=title, bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI Semibold", 9)).pack(anchor="w")
            tk.Label(box, text=detail, bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")

        safety = self._card(self, self.colors["success"], padx=14, pady=10)
        safety.pack(fill="x", padx=20)
        tk.Label(
            safety,
            text="✓ SÉCURITÉ : COPIE UNIQUEMENT",
            bg=self.colors["panel"],
            fg=self.colors["success"],
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        tk.Label(
            safety,
            text="Aucune écriture dans le dossier original. La version FR est un second jeu indépendant.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8),
        ).pack(side="right")

        settings = self._card(self, self.colors["orange"], padx=14, pady=12)
        settings.pack(fill="x", padx=20, pady=(10, 0))

        row1 = tk.Frame(settings, bg=self.colors["panel"])
        row1.pack(fill="x")
        tk.Label(row1, text="Textes inclus", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI Semibold", 9)).pack(side="left")
        self.mode_var = tk.StringVar(value="recommended")
        for label, value in [
            ("RECOMMANDÉ — prêts + acceptés", "recommended"),
            ("STRICT — acceptés uniquement", "accepted"),
        ]:
            tk.Radiobutton(
                row1,
                text=label,
                value=value,
                variable=self.mode_var,
                command=self.invalidate_plan,
                indicatoron=False,
                bg=self.colors["panel2"],
                fg=self.colors["text"],
                selectcolor=self.colors["accent_dark"],
                activebackground=self.colors["accent_dark"],
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                padx=12,
                pady=7,
                font=("Segoe UI Semibold", 8),
            ).pack(side="left", padx=(10, 0))

        row2 = tk.Frame(settings, bg=self.colors["panel"])
        row2.pack(fill="x", pady=(12, 0))
        tk.Label(row2, text="Copie française", bg=self.colors["panel"], fg=self.colors["text"], font=("Segoe UI Semibold", 9)).pack(side="left")
        self.target_var = tk.StringVar(value=str(self.default_target))
        entry = tk.Entry(
            row2,
            textvariable=self.target_var,
            bg=self.colors["panel2"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            font=("Segoe UI", 9),
        )
        entry.pack(side="left", fill="x", expand=True, padx=(10, 8), ipady=7)
        self._button(row2, "CHOISIR LE DOSSIER PARENT", self.choose_target_parent, self.colors["accent"]).pack(side="right")

        tk.Label(
            settings,
            text=("Cette version FR est une copie autonome : garde l’original en sécurité et joue uniquement "
                  "avec le Game.exe du dossier VERSION_FR."),
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            justify="left",
            anchor="w",
            font=("Segoe UI", 8),
        ).pack(fill="x", pady=(9, 0))

        summary = self._card(self, self.colors["accent"], padx=14, pady=12)
        summary.pack(fill="both", expand=True, padx=20, pady=(10, 0))

        self.summary_var = tk.StringVar(value="Simulation en attente…")
        tk.Label(
            summary,
            textvariable=self.summary_var,
            bg=self.colors["panel"],
            fg=self.colors["accent2"],
            justify="left",
            anchor="w",
            font=("Segoe UI Semibold", 10),
        ).pack(fill="x")

        self.details = tk.Text(
            summary,
            wrap="word",
            bg="#070a10",
            fg="#c9d3df",
            insertbackground="#ffffff",
            relief="flat",
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            height=13,
            padx=11,
            pady=9,
            font=("Cascadia Mono", 9),
            state="disabled",
        )
        self.details.pack(fill="both", expand=True, pady=(9, 0))

        footer = self._card(self, self.colors["border"], padx=14, pady=10)
        footer.pack(fill="x", padx=20, pady=(10, 16))

        buttons = tk.Frame(footer, bg=self.colors["panel"])
        buttons.pack(fill="x")
        self.simulate_btn = self._button(buttons, "RELANCER LA SIMULATION", self.run_simulation, self.colors["accent"])
        self.simulate_btn.pack(side="left")
        self.rebuild_btn = self._button(buttons, "CRÉER LA COPIE FRANÇAISE", self.create_copy, self.colors["orange"], large=True)
        self.rebuild_btn.pack(side="left", padx=(8, 0))
        self.rebuild_btn.configure(state="disabled")
        self.shortcut_btn = self._button(buttons, "RACCOURCI BUREAU", self.create_desktop_shortcut, self.colors["purple"])
        self.shortcut_btn.pack(side="right", padx=(0, 8))
        self.shortcut_btn.configure(state="disabled")
        self.open_folder_btn = self._button(buttons, "OUVRIR LA VERSION FR", self.open_target, self.colors["accent"])
        self.open_folder_btn.pack(side="right")
        self.open_folder_btn.configure(state="disabled")
        self.open_btn = self._button(buttons, "JOUER À LA VERSION FR", self.launch_target_game, self.colors["success"])
        self.open_btn.pack(side="right", padx=(0, 8))
        self.open_btn.configure(state="disabled")

        self.progress = ttk.Progressbar(footer, maximum=100, style="Rebuild.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(10, 3))
        self.status_var = tk.StringVar(value="Prêt.")
        tk.Label(footer, textvariable=self.status_var, bg=self.colors["panel"], fg=self.colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")

    def _set_details(self, text: str):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def log(self, message: str):
        self.logger(f"Reconstruction v1.0.2 : {message}")

    def invalidate_plan(self):
        self.plan = None
        self.rebuild_btn.configure(state="disabled")
        self.summary_var.set("Le mode a changé. Relance la simulation.")

    def choose_target_parent(self):
        parent = filedialog.askdirectory(parent=self, title="Choisir le dossier qui recevra la copie française")
        if not parent:
            return
        self.target_var.set(str(Path(parent) / self.default_target.name))

    def _toggle_running(self, running: bool):
        self.running = running
        state = "disabled" if running else "normal"
        self.simulate_btn.configure(state=state)
        if running:
            self.rebuild_btn.configure(state="disabled")
        elif self.plan and self.plan.counts().get("applicable", 0) > 0:
            self.rebuild_btn.configure(state="normal")

    def run_simulation(self):
        if self.running:
            return
        self._toggle_running(True)
        self.progress["value"] = 5
        self.status_var.set("Simulation de reconstruction…")
        self.summary_var.set("Vérification des textes et des fichiers originaux…")

        mode = self.mode_var.get()

        def worker():
            try:
                plan = build_plan(self.game_root, self.csv_path, mode)
                plan = simulate_plan(plan)
                self.after(0, lambda: self._simulation_finished(plan))
            except Exception as exc:
                self.after(0, lambda error=exc: self._operation_failed("Simulation impossible", error))

        threading.Thread(target=worker, daemon=True).start()

    def _simulation_finished(self, plan: ReconstructionPlan):
        self.plan = plan
        self._toggle_running(False)
        self.progress["value"] = 100
        counts = plan.counts()
        applicable = counts.get("applicable", 0)
        skipped = counts.get("skipped", 0)
        blocked = counts.get("blocked", 0)
        files = counts.get("files", 0)
        self.summary_var.set(
            f"SIMULATION TERMINÉE  •  {applicable} texte(s) seront intégrés  •  "
            f"{blocked} resteront en anglais par sécurité  •  {files} fichier(s) concernés"
        )

        reasons = {}
        for item in plan.items:
            if item.decision != "applicable":
                label = friendly_reason(item.reason)
                reasons[label] = reasons.get(label, 0) + 1
        lines = [
            "AUCUN FICHIER N'A ÉTÉ MODIFIÉ PENDANT CETTE SIMULATION.",
            "",
            f"Jeu original : {self.game_root}",
            f"Projet : {self.csv_path}",
            f"Mode : {'Recommandé' if plan.mode == 'recommended' else 'Strict'}",
            "",
            f"Lignes du projet : {counts.get('project_rows', 0)}",
            f"Traductions présentes : {counts.get('translated_rows', 0)}",
            f"Textes encore non traduits : {counts.get('untranslated_rows', 0)}",
            f"Traductions applicables : {applicable}",
            f"Traductions ignorées : {skipped}",
            f"Textes laissés en anglais par sécurité : {blocked}",
            f"Fichiers concernés : {files}",
            "",
            "POURQUOI CERTAINS TEXTES RESTENT EN ANGLAIS",
            "-" * 70,
        ]
        lines.extend(f"{count:>6}  {reason}" for reason, count in sorted(reasons.items(), key=lambda pair: (-pair[1], pair[0])))
        if not reasons:
            lines.append("Aucune.")
        lines.extend([
            "",
            "La copie française inclura uniquement les cartes, banques de messages et champs PBS reconnus.",
            "Les scripts et les fichiers inconnus resteront strictement identiques à l'original.",
            "Un texte ignoré n'empêche pas la copie de fonctionner : il reste simplement en anglais.",
        ])
        self._set_details("\n".join(lines))
        self.status_var.set("Simulation sûre terminée. Tu peux créer la copie française.")

        plan_path = self.reports_dir / "PLAN_RECONSTRUCTION_V1.0.json"
        save_plan(plan, plan_path)
        self.log(f"Simulation : {applicable} applicable(s), {blocked} bloqué(s).")
        if applicable:
            self.rebuild_btn.configure(state="normal")
        else:
            messagebox.showwarning(
                "Aucun texte applicable",
                "Aucune traduction prête ou acceptée ne peut être reconstruite.",
                parent=self,
            )

    def create_copy(self):
        if self.running:
            return
        if not self.plan:
            self.run_simulation()
            return
        target = Path(self.target_var.get()).expanduser()
        if target.exists():
            messagebox.showerror(
                "Dossier déjà présent",
                "Le dossier de sortie existe déjà. Supprime-le ou choisis un autre nom.",
                parent=self,
            )
            return
        counts = self.plan.counts()
        applicable = counts.get("applicable", 0)
        if not messagebox.askyesno(
            "Créer la copie française",
            f"Créer une copie complète du fangame puis appliquer {applicable} traduction(s) sûre(s) ?\n\n"
            f"Sortie :\n{target}\n\n"
            "Le jeu original ne sera pas modifié.",
            parent=self,
        ):
            return

        self._toggle_running(True)
        self.progress["value"] = 2
        self.status_var.set("Création de la copie française…")

        def progress(current: int, total: int, message: str):
            percent = 5 if total <= 1 else min(99, int(current * 100 / max(1, total)))
            self.after(0, lambda: self._update_progress(percent, message))

        def worker():
            try:
                result = reconstruct_copy(
                    self.plan,
                    target,
                    self.reports_dir,
                    progress=progress,
                )
                self.after(0, lambda: self._reconstruction_finished(result))
            except Exception as exc:
                self.after(0, lambda error=exc: self._operation_failed("Reconstruction impossible", error))

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, percent: int, message: str):
        self.progress["value"] = percent
        self.status_var.set(message)

    def _reconstruction_finished(self, result):
        self._toggle_running(False)
        self.progress["value"] = 100
        self.open_btn.configure(state="normal")
        self.open_folder_btn.configure(state="normal")
        self.shortcut_btn.configure(state="normal")
        self.status_var.set("Version française créée et validée.")
        blocked = self.plan.counts().get("blocked", 0) if self.plan else 0
        critical = len(result.validation_errors)
        self.summary_var.set(
            f"✓ VERSION FR CRÉÉE  •  {result.applied} texte(s) intégré(s)  •  "
            f"{blocked} laissé(s) en anglais  •  {critical} erreur(s) critique(s)"
        )
        current = self.details.get("1.0", "end-1c")
        self._set_details(current + "\n\n" + "\n".join([
            "RÉSULTAT DE LA RECONSTRUCTION",
            "-" * 70,
            f"Copie : {result.target_root}",
            f"Traductions appliquées : {result.applied}",
            f"Fichiers modifiés : {len(result.modified_files)}",
            f"Original inchangé : {'OUI' if result.original_unchanged else 'NON'}",
            f"Erreurs de validation : {len(result.validation_errors)}",
            f"Rapport : {result.report_path}",
        ]))
        self.log(f"Copie française créée : {result.target_root}")
        messagebox.showinfo(
            "Version française prête",
            f"{result.applied} texte(s) intégré(s).\n"
            f"{blocked} texte(s) laissé(s) en anglais par sécurité.\n"
            f"{critical} erreur(s) critique(s).\n\n"
            "Tu peux jouer avec le bouton ci-dessous ou lancer Game.exe dans VERSION_FR.\n"
            "Conserve toujours le jeu original comme sauvegarde propre.",
            parent=self,
        )

    def _operation_failed(self, title: str, exc: Exception):
        self._toggle_running(False)
        self.progress["value"] = 0
        self.status_var.set(str(exc))
        self.log(f"{title} : {type(exc).__name__}: {exc}")
        messagebox.showerror(title, f"{type(exc).__name__}: {exc}", parent=self)


    def launch_target_game(self):
        target = Path(self.target_var.get()).expanduser()
        executable = target / "Game.exe"
        if not executable.exists():
            messagebox.showinfo(
                "Game.exe introuvable",
                "La copie est créée, mais Game.exe est introuvable. Le dossier va être ouvert.",
                parent=self,
            )
            self.open_target()
            return
        try:
            if os.name == "nt":
                os.startfile(executable)  # type: ignore[attr-defined]
            else:
                subprocess.Popen([str(executable)], cwd=str(target))
        except Exception as exc:
            messagebox.showerror("Lancement impossible", str(exc), parent=self)

    def create_desktop_shortcut(self):
        target = Path(self.target_var.get()).expanduser()
        executable = target / "Game.exe"
        if not executable.exists():
            messagebox.showinfo(
                "Version FR absente",
                "Crée d'abord la version française.",
                parent=self,
            )
            return
        if os.name != "nt":
            messagebox.showinfo(
                "Raccourci Windows",
                "Cette fonction est disponible sous Windows. Ouvre le dossier et lance Game.exe.",
                parent=self,
            )
            return
        try:
            shortcut_name = re.sub(r"[^A-Za-z0-9À-ÿ._ -]+", "_", self.game_root.name).strip()
            shortcut_name = f"{shortcut_name} - Version FR"
            exe_ps = str(executable).replace("'", "''")
            target_ps = str(target).replace("'", "''")
            name_ps = shortcut_name.replace("'", "''")
            script = (
                "$desktop=[Environment]::GetFolderPath('Desktop');"
                "$ws=New-Object -ComObject WScript.Shell;"
                f"$s=$ws.CreateShortcut((Join-Path $desktop '{name_ps}.lnk'));"
                f"$s.TargetPath='{exe_ps}';"
                f"$s.WorkingDirectory='{target_ps}';"
                f"$s.IconLocation='{exe_ps},0';"
                "$s.Description='Version française créée avec Pokémon Fangame Translator';"
                "$s.Save();"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                check=True,
                capture_output=True,
                text=True,
            )
            messagebox.showinfo(
                "Raccourci créé",
                "Un raccourci vers la version française a été ajouté au Bureau.",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror("Raccourci impossible", str(exc), parent=self)

    def open_target(self):
        target = Path(self.target_var.get()).expanduser()
        if not target.exists():
            messagebox.showinfo("Copie absente", "La copie française n'existe pas encore.", parent=self)
            return
        try:
            if os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:
            messagebox.showerror("Ouverture impossible", str(exc), parent=self)
