"""Interface tkinter/ttk : barre latérale, en-tête système, une page par fonctionnalité.

Les travaux longs tournent dans des threads ; seul le thread de l'interface touche aux widgets.
"""
import heapq
import json
import os
import platform
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import cleaner, diskscan, duplicates, programs, schedule, startup, winapi
from .safety import DATA_DIR, Stats, log, setup_log

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # PyInstaller ou dépôt
SETTINGS = DATA_DIR / "settings.json"
PREVIEW_MAX = 1000  # lignes affichées dans l'aperçu d'une catégorie
EXCLUDED = ("Volontairement absents, car risqués pour Windows : nettoyage/réparation du registre, "
            "suppression de pilotes, désactivation de services, effacement de l'espace libre, "
            "« boost » et modification de réglages système. Aucune télémétrie, aucun accès réseau.")

# Palette
SIDEBAR, SIDEBAR_HOVER, SIDEBAR_TEXT = "#1d2433", "#2a3347", "#c3cbda"
ACCENT, ACCENT_DARK = "#2d7ff9", "#1f66d1"
SUCCESS, SUCCESS_DARK = "#1f9d61", "#178050"
WARN = "#f2994a"
BG, CARD, BORDER, HEADING = "#f2f4f8", "#ffffff", "#e1e6ee", "#eef1f6"
TEXT, MUTED = "#1f2937", "#6b7280"
FONT = "Segoe UI"


def human(n):
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} To".replace(".", ",")


def load_settings():
    try:
        return json.loads(SETTINGS.read_text("utf-8"))
    except (OSError, ValueError):
        return {}  # premier lancement : simulation par défaut


def save_settings(data):
    try:
        SETTINGS.write_text(json.dumps(data), "utf-8")
    except OSError:
        log.exception("Réglages non enregistrés")


def system_summary():
    """Ligne d'en-tête : Windows, processeur, mémoire, espace libre du disque système."""
    parts = []
    if winapi.IS_WINDOWS:
        build = sys.getwindowsversion().build
        parts.append(f"Windows {'11' if build >= 22000 else '10'} (build {build})")
        cpu = winapi.reg_values("HKLM", r"HARDWARE\DESCRIPTION\System\CentralProcessor\0").get("ProcessorNameString")
        if cpu:
            parts.append(" ".join(str(cpu).split()))
        if ram := winapi.memory_total():
            parts.append(f"{human(ram)} de RAM")
        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    else:
        parts.append(platform.platform(terse=True))
        system_drive = "/"
    try:
        u = shutil.disk_usage(system_drive)
        parts.append(f"Disque système : {human(u.free)} libres sur {human(u.total)}")
    except OSError:
        pass
    return "   ·   ".join(parts)


def setup_style(root):
    """Thème « clam » : le thème natif de Windows ignore les couleurs personnalisées."""
    scale = float(root.winfo_fpixels("1i")) / 96  # écrans haute résolution
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=CARD, foreground=TEXT, font=(FONT, 10), bordercolor=BORDER,
                focuscolor=ACCENT)
    s.configure("Page.TFrame", background=BG)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Title.TLabel", font=(FONT, 16, "bold"))
    s.configure("Section.TLabel", font=(FONT, 11, "bold"))
    s.configure("Big.TLabel", foreground=ACCENT, font=(FONT, 22, "bold"))
    s.configure("BigDone.TLabel", foreground=SUCCESS, font=(FONT, 22, "bold"))
    s.configure("TButton", background=CARD, lightcolor=CARD, darkcolor=CARD, padding=(12, 6))
    s.map("TButton", background=[("disabled", BG), ("active", HEADING)], foreground=[("disabled", MUTED)])
    # Accent/Success : barres d'outils ; Big* : actions principales.
    for name, color, dark, font, pad in (("Accent", ACCENT, ACCENT_DARK, (FONT, 10, "bold"), (14, 6)),
                                         ("BigAccent", ACCENT, ACCENT_DARK, (FONT, 12, "bold"), (22, 11)),
                                         ("BigSuccess", SUCCESS, SUCCESS_DARK, (FONT, 12, "bold"), (22, 11))):
        s.configure(f"{name}.TButton", background=color, foreground="white", bordercolor=color,
                    lightcolor=color, darkcolor=color, font=font, padding=pad)
        s.map(f"{name}.TButton", foreground=[("disabled", "white")],
              **{k: [("disabled", "#b9c3d4"), ("active", dark)]
                 for k in ("background", "bordercolor", "lightcolor", "darkcolor")})
    s.configure("Treeview", background=CARD, fieldbackground=CARD, rowheight=int(28 * scale), font=(FONT, 10),
                indent=int(14 * scale))  # retrait des sous-éléments : 20 px par défaut, trop large ici
    s.map("Treeview", background=[("selected", "#dce8fd")], foreground=[("selected", TEXT)])
    s.configure("Treeview.Heading", background=HEADING, foreground=MUTED, font=(FONT, 9, "bold"),
                relief="flat", padding=(6, 4))
    s.map("Treeview.Heading", background=[("active", BORDER)])
    s.configure("Horizontal.TProgressbar", troughcolor=HEADING, background=ACCENT, bordercolor=HEADING,
                lightcolor=ACCENT, darkcolor=ACCENT, thickness=int(8 * scale))
    s.map("TCheckbutton", background=[("active", CARD)])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, fieldbackground=CARD, padding=4)
    s.configure("Vertical.TScrollbar", background=BORDER, troughcolor=CARD, arrowcolor=MUTED)


def card(parent):
    """Carte blanche bordée d'un trait fin ; renvoie (cadre extérieur à placer, cadre intérieur à remplir)."""
    outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    inner = ttk.Frame(outer, padding=16)
    inner.pack(fill="both", expand=True)
    return outer, inner


def autowrap(label, pad=0):
    """Retour à la ligne calé sur la largeur réelle du label (à placer avec fill="x") ; pad = marges internes."""
    label.bind("<Configure>", lambda e: label.configure(wraplength=max(e.width - pad - 4, 120)))
    return label


def scrolled_tree(parent, cols, stretch=None, **kw):
    """Treeview + barre de défilement ; cols = [(id, titre, largeur)], '#0' = colonne arborescente.

    Une seule colonne (`stretch`, la première par défaut) prend la place restante : un Treeview ttk
    ne rétrécit pas ses colonnes, des largeurs fixes trop grandes seraient coupées à droite.
    """
    frame = ttk.Frame(parent)
    ids = [c for c, _, _ in cols if c != "#0"]
    stretch = stretch or cols[0][0]
    kw.setdefault("show", "tree headings" if cols[0][0] == "#0" else "headings")
    tree = ttk.Treeview(frame, columns=ids, **kw)
    for cid, title, width in cols:
        anchor = "e" if cid in ("size", "pct", "files") else "w"
        tree.heading(cid, text=title, anchor=anchor)
        tree.column(cid, width=width, minwidth=50, anchor=anchor, stretch=cid == stretch)
    tree.tag_configure("muted", foreground=MUTED)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    return frame, tree


def animate(bar, on):
    """Barre indéterminée animée seulement pendant le travail : au repos elle afficherait un bloc figé."""
    if on:
        bar.configure(mode="indeterminate")
        bar.start(12)
    else:
        bar.stop()
        bar.configure(mode="determinate", value=0)


def throttled(app, fn, delay=0.2):
    """Rappel de progression appelable depuis un thread, limité à un message UI toutes les `delay` s."""
    last = [0.0]

    def progress(value):
        now = time.monotonic()
        if now - last[0] > delay:
            last[0] = now
            app.post(fn, value)
    return progress


class Sidebar(tk.Frame):
    def __init__(self, master, app, items):
        super().__init__(master, bg=SIDEBAR, width=220)
        self.pack_propagate(False)
        self.active = None
        tk.Label(self, text="CleanLite", bg=SIDEBAR, fg="white", font=(FONT, 18, "bold"),
                 anchor="w", padx=22, pady=24).pack(fill="x")
        self.buttons = {}  # clé -> widgets d'une entrée (cadre, icône, texte), recolorés ensemble
        for key, symbol, label in items:
            item = tk.Frame(self, bg=SIDEBAR, cursor="hand2", takefocus=True, highlightthickness=1,
                            highlightbackground=SIDEBAR, highlightcolor=ACCENT)
            item.pack(fill="x")
            # Icône dans une colonne de largeur fixe : les glyphes n'ont pas tous la même largeur.
            icon = tk.Label(item, text=symbol, width=2, bg=SIDEBAR, fg=SIDEBAR_TEXT, font=(FONT, 12))
            icon.pack(side="left", padx=(18, 6), pady=11)
            text = tk.Label(item, text=label, bg=SIDEBAR, fg=SIDEBAR_TEXT, font=(FONT, 11), anchor="w")
            text.pack(side="left", fill="x")
            for w in (item, icon, text):
                w.bind("<Button-1>", lambda e, k=key: app.show(k))
                w.bind("<Enter>", lambda e, k=key: self._hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._hover(k, False))
            for seq in ("<Return>", "<space>"):
                item.bind(seq, lambda e, k=key: app.show(k))
            self.buttons[key] = (item, icon, text)
        self.rights = tk.Label(self, bg=SIDEBAR, fg=SIDEBAR_TEXT, font=(FONT, 9), anchor="w", padx=22, pady=16)
        self.rights.pack(side="bottom", fill="x")
        self.mode = tk.Label(self, bg=SIDEBAR, font=(FONT, 9, "bold"), anchor="w", padx=22)
        self.mode.pack(side="bottom", fill="x")

    def _paint(self, key, bg, fg):
        item, icon, text = self.buttons[key]
        item.configure(bg=bg, highlightbackground=bg)
        icon.configure(bg=bg, fg=fg)
        text.configure(bg=bg, fg=fg)

    def select(self, key):
        self.active = key
        for k in self.buttons:
            self._paint(k, *((ACCENT, "white") if k == key else (SIDEBAR, SIDEBAR_TEXT)))

    def _hover(self, key, on):
        if key != self.active:
            self._paint(key, SIDEBAR_HOVER if on else SIDEBAR, "white" if on else SIDEBAR_TEXT)

    def set_status(self, dry_run, admin):
        self.mode.configure(text="●  MODE SIMULATION" if dry_run else "●  MODE RÉEL", fg=WARN if dry_run else SUCCESS)
        self.rights.configure(text="Administrateur" if admin else "Utilisateur standard")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CleanLite")
        self.geometry("1120x720")
        self.minsize(940, 580)
        self.configure(bg=BG)
        icon = BASE / "assets" / "icon.ico"
        if winapi.IS_WINDOWS and icon.exists():
            self.iconbitmap(default=str(icon))
        setup_style(self)
        self.admin = winapi.is_admin()
        self.dry_run = tk.BooleanVar(value=load_settings().get("dry_run", True))
        self.dry_run.trace_add("write", self._mode_changed)
        self._q = queue.Queue()
        self.after(100, self._poll)

        self.page_defs = {
            "cleaner": ("✦", "Nettoyeur", CleanerPage),
            "startup": ("⚡", "Démarrage", StartupPage),
            "programs": ("▣", "Programmes", ProgramsPage),
            "disk": ("◔", "Analyse disque", DiskPage),
            "dups": ("❐", "Doublons", DupPage),
            "options": ("⚙", "Options", OptionsPage),
        }
        self.sidebar = Sidebar(self, self, [(k, s, label) for k, (s, label, _) in self.page_defs.items()])
        self.sidebar.pack(side="left", fill="y")
        main = ttk.Frame(self, style="Page.TFrame")
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        header.pack(fill="x")
        self.page_title = ttk.Label(header, style="Title.TLabel", padding=(24, 16, 24, 0))
        self.page_title.pack(anchor="w")
        self.sysinfo = autowrap(ttk.Label(header, style="Muted.TLabel", padding=(24, 2, 24, 14), text="…"), pad=48)
        self.sysinfo.pack(anchor="w", fill="x")
        self.content = ttk.Frame(main, style="Page.TFrame", padding=16)
        self.content.pack(fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.pages = {}
        self._mode_changed()
        self.refresh_sysinfo()
        self.show("cleaner")

    def show(self, key):
        symbol, label, cls = self.page_defs[key]
        if key not in self.pages:  # construite à la première visite : démarrage rapide
            self.pages[key] = cls(self.content, self)
            self.pages[key].grid(row=0, column=0, sticky="nsew")
        self.pages[key].tkraise()
        self.page_title["text"] = label
        self.sidebar.select(key)

    def refresh_sysinfo(self):
        self.bg(system_summary, lambda s: None if self.failed(s) else self.sysinfo.configure(text=s))

    def _mode_changed(self, *_):
        save_settings({"dry_run": self.dry_run.get()})
        self.sidebar.set_status(self.dry_run.get(), self.admin)

    # --- threads : le travail tourne à part, le résultat revient dans le thread de l'UI ---
    def bg(self, work, done):
        def target():
            try:
                res = work()
            except Exception as e:  # rendu à l'UI : jamais de plantage silencieux
                log.exception("Erreur en tâche de fond")
                res = e
            self._q.put((done, res))
        threading.Thread(target=target, daemon=True).start()

    def post(self, fn, arg):
        self._q.put((fn, arg))

    def _poll(self):
        try:
            while True:
                fn, arg = self._q.get_nowait()
                fn(arg)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll)

    def failed(self, res):
        if isinstance(res, Exception):
            messagebox.showerror("Erreur", str(res) or type(res).__name__)
            return True
        return False

    def report_callback_exception(self, exc, val, tb):
        log.error("Erreur d'interface", exc_info=(exc, val, tb))
        messagebox.showerror("Erreur", f"{val}\n\nDétails dans le journal.")

    def relaunch_admin(self):
        if winapi.relaunch_as_admin():
            self.destroy()
        else:
            messagebox.showwarning("Administrateur", "Relance annulée ou impossible.")


class CleanerPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="Page.TFrame")
        self.app = app
        self.cats = {c.key: c for c in cleaner.categories()}
        self.checked = {k: c.default and not self._locked(c) for k, c in self.cats.items()}
        self.preview = {}  # clé -> [(chemin, taille)] de la dernière analyse
        self.columnconfigure(0, weight=2, uniform="col")
        self.columnconfigure(1, weight=3, uniform="col")
        self.rowconfigure(0, weight=1)

        # Gauche : éléments à cocher.
        outer, left = card(self)
        outer.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        ttk.Label(left, text="Éléments à nettoyer", style="Section.TLabel").pack(anchor="w")
        ttk.Label(left, text="Cliquez sur une ligne pour la cocher.", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        if not app.admin:  # avant la liste, pour la même raison qu'à droite
            ttk.Button(left, text="Relancer en administrateur", command=app.relaunch_admin).pack(side="bottom", anchor="w")
            ttk.Label(left, text="Grisé : droits administrateur requis.", style="Muted.TLabel").pack(
                side="bottom", anchor="w", pady=(10, 4))
        frame, self.tree = scrolled_tree(left, [("#0", "", 220)], show="tree")
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("group", font=(FONT, 10, "bold"))
        groups = {}
        for c in self.cats.values():
            if c.group not in groups:
                groups[c.group] = self.tree.insert("", "end", text=c.group, open=True, tags=("group",))
            self.tree.insert(groups[c.group], "end", iid=c.key, text=self._text(c),
                             tags=("muted",) if self._locked(c) else ())
        self.tree.bind("<Button-1>", lambda e: self.toggle(self.tree.identify_row(e.y)))
        self.tree.bind("<space>", lambda e: self.toggle(self.tree.focus()))

        # Droite : résultats, progression, actions.
        outer, right = card(self)
        outer.grid(row=0, column=1, sticky="nsew")
        self.headline = ttk.Label(right, text="Prêt à analyser", style="Big.TLabel")
        self.headline.pack(anchor="w")
        self.detail = autowrap(ttk.Label(right, style="Muted.TLabel", justify="left",
                                         text="Cochez les éléments à gauche puis lancez l'analyse : "
                                              "rien n'est supprimé avant « Nettoyer »."))
        self.detail.pack(anchor="w", fill="x", pady=(2, 12))
        # Le bas est placé avant la liste : si la hauteur manque, c'est la liste qui rétrécit, pas les boutons.
        # Deux gros boutons qui se partagent la largeur, quelle que soit la taille de la fenêtre.
        row = ttk.Frame(right)
        row.pack(side="bottom", fill="x")
        self.btn_analyze = ttk.Button(row, text="Analyser", style="BigAccent.TButton", command=self.analyze)
        self.btn_analyze.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_clean = ttk.Button(row, text="Nettoyer", style="BigSuccess.TButton", command=self.clean)
        self.btn_clean.pack(side="left", fill="x", expand=True, padx=(6, 0))
        row = ttk.Frame(right)
        row.pack(side="bottom", fill="x", pady=(0, 10))
        ttk.Button(row, text="Aperçu des fichiers", command=self.show_files).pack(side="right")
        ttk.Checkbutton(row, text="Mode simulation", variable=app.dry_run).pack(side="left")
        self.progress = ttk.Progressbar(right, mode="determinate", maximum=100)
        self.progress.pack(side="bottom", fill="x", pady=(14, 10))
        frame, self.res = scrolled_tree(right, [("cat", "Élément", 220), ("files", "Fichiers", 70),
                                                ("size", "Taille", 90)], selectmode="browse")
        frame.pack(fill="both", expand=True)
        self.res.bind("<Double-1>", lambda e: self.show_files())

    def _locked(self, c):
        return c.admin and not self.app.admin

    def _text(self, c):
        return f"{'☑' if self.checked[c.key] else '☐'}  {c.label}"

    def toggle(self, iid):
        c = self.cats.get(iid)
        if c and not self._locked(c):
            self.checked[iid] = not self.checked[iid]
            self.tree.item(iid, text=self._text(c))

    def _busy(self, busy):
        for b in (self.btn_analyze, self.btn_clean):
            b.state(["disabled"] if busy else ["!disabled"])

    def _run(self, cats, dry_run, done, collect=False):
        """Exécute les catégories dans un thread ; résultat [(catégorie, Stats, fichiers)]."""
        self._busy(True)
        self.progress.configure(value=0)
        n = max(len(cats), 1)

        def work():
            out = []
            for i, c in enumerate(cats):
                self.app.post(self._step, (i, n, c))
                files = [] if collect else None
                out.append((c, cleaner.run(c, dry_run, files), files or []))
            return out
        self.app.bg(work, done)

    def _step(self, arg):
        i, n, c = arg
        self.progress.configure(value=100 * i / n)
        self.detail["text"] = f"{c.group} – {c.label}…"

    def _fill_results(self, res):
        self.progress.configure(value=100)
        self.res.delete(*self.res.get_children())
        for c, st, _ in sorted(res, key=lambda r: r[1].bytes, reverse=True):
            dns = c.key == "dns"
            self.res.insert("", "end", iid=c.key, tags=("muted",) if not st.bytes else (), values=(
                f"{c.group} – {c.label}", "—" if dns else st.files, "—" if dns else human(st.bytes)))

    def analyze(self):
        self.headline.configure(text="Analyse en cours…", style="Big.TLabel")
        self._run([c for k, c in self.cats.items() if self.checked[k]], True, self._analyzed, collect=True)

    def _analyzed(self, res):
        self._busy(False)
        if self.app.failed(res):
            return
        self.preview = {c.key: files for c, _, files in res}
        self._fill_results(res)
        self.headline["text"] = f"{human(sum(st.bytes for _, st, _ in res))} récupérables"
        self.detail["text"] = ("Analyse terminée. Double-cliquez sur un élément pour voir ses fichiers, "
                               "puis cliquez sur « Nettoyer ».")

    def show_files(self):
        key = self.res.focus() or self.tree.focus()
        c = self.cats.get(key)
        if c is None or key not in self.preview:
            messagebox.showinfo("Aperçu", "Lancez « Analyser », puis sélectionnez un élément dans les résultats.")
            return
        if key in ("recycle", "dns"):
            messagebox.showinfo("Aperçu", "Cet élément est vidé en bloc par Windows : pas de liste de fichiers.")
            return
        files = self.preview[key]
        top = tk.Toplevel(self)
        top.title(f"Aperçu : {c.group} – {c.label}")
        top.geometry("860x480")
        top.configure(bg=CARD)
        more = f" Les {PREVIEW_MAX} plus gros sont affichés." if len(files) > PREVIEW_MAX else ""
        ttk.Label(top, padding=(14, 12, 14, 6), style="Section.TLabel",
                  text=f"{len(files)} fichier(s), {human(sum(s for _, s in files))}").pack(anchor="w")
        ttk.Label(top, padding=(14, 0, 14, 8), style="Muted.TLabel",
                  text=f"Rien n'a été supprimé.{more}").pack(anchor="w")
        frame, tree = scrolled_tree(top, [("path", "Fichier", 700), ("size", "Taille", 100)])
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        for path, size in heapq.nlargest(PREVIEW_MAX, files, key=lambda f: f[1]):
            tree.insert("", "end", values=(path, human(size)))

    def clean(self):
        cats = [c for k, c in self.cats.items() if self.checked[k]]
        running = winapi.running_processes()
        for browser in sorted({c.group for c in cats if c.process and c.process in running}):
            if messagebox.askyesno("Navigateur ouvert",
                                   f"{browser} est ouvert : ses fichiers en cours d'utilisation seraient ignorés.\n\n"
                                   f"Passer {browser} pour ce nettoyage ? (Non = nettoyer quand même)"):
                cats = [c for c in cats if c.group != browser]
        if not cats:
            return
        dry = self.app.dry_run.get()
        if not dry and not messagebox.askyesno(
                "Confirmer le nettoyage", "Supprimer définitivement :\n\n"
                + "\n".join(f"• {c.group} – {c.label}" for c in cats) + "\n\nCette action est irréversible.",
                icon="warning"):
            return
        self.headline.configure(text="Simulation en cours…" if dry else "Nettoyage en cours…", style="Big.TLabel")
        self._run(cats, dry, lambda res: self._cleaned(res, dry))

    def _cleaned(self, res, dry):
        self._busy(False)
        if self.app.failed(res):
            return
        self._fill_results(res)
        total = Stats()
        for _, st, _ in res:
            total.add(st)
        if dry:
            log.info("Simulation : %s seraient libérés (%d fichiers)", human(total.bytes), total.files)
            self.headline.configure(text=f"{human(total.bytes)} seraient libérés", style="Big.TLabel")
            detail = "Simulation : aucun fichier supprimé. Décochez « Mode simulation » pour nettoyer réellement."
        else:
            self.headline.configure(text=f"{human(total.bytes)} libérés", style="BigDone.TLabel")
            detail = f"Nettoyage terminé : {total.files} fichier(s) supprimé(s)."
            self.preview = {}  # l'aperçu ne correspond plus au disque
            self.app.refresh_sysinfo()
        if total.skipped:
            detail += f"\n{total.skipped} élément(s) ignoré(s) : verrouillés ou en cours d'utilisation."
        self.detail["text"] = detail


class StartupPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="Page.TFrame")
        self.app = app
        self.entries = []
        outer, c = card(self)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(c)
        top.pack(fill="x")
        ttk.Label(top, text="Programmes lancés au démarrage", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="Actualiser", command=self.refresh).pack(side="right")
        ttk.Button(top, text="Activer", command=lambda: self.set_enabled(True)).pack(side="right", padx=8)
        ttk.Button(top, text="Désactiver", style="Accent.TButton",
                   command=lambda: self.set_enabled(False)).pack(side="right")
        self.info = autowrap(ttk.Label(c, style="Muted.TLabel", text="Chargement…"))
        self.info.pack(anchor="w", fill="x", pady=(4, 10))
        frame, self.tree = scrolled_tree(c, [("name", "Nom", 190), ("state", "État", 90),
                                             ("where", "Emplacement", 170), ("cmd", "Commande", 300)],
                                         stretch="cmd")
        frame.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self):
        self.app.bg(startup.list_entries, self._fill)

    def _fill(self, res):
        if self.app.failed(res):
            return
        self.entries = res
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(res):
            self.tree.insert("", "end", iid=str(i), tags=() if e.enabled else ("muted",), values=(
                e.name, "Activé" if e.enabled else "Désactivé", e.location, e.command))
        self._count()

    def _count(self):
        off = sum(not e.enabled for e in self.entries)
        self.info["text"] = (f"{len(self.entries)} entrée(s), dont {off} désactivée(s). Rien n'est supprimé : "
                             "une entrée désactivée se réactive à tout moment.")

    def set_enabled(self, enabled):
        for iid in self.tree.selection():
            e = self.entries[int(iid)]
            try:
                startup.set_enabled(e, enabled)
            except OSError as err:
                hint = "" if self.app.admin else "\n\nDroits administrateur probablement requis (page Options)."
                messagebox.showerror("Démarrage", f"{e.name} : {err}{hint}")
                return
            self.tree.set(iid, "state", "Activé" if enabled else "Désactivé")
            self.tree.item(iid, tags=() if enabled else ("muted",))
        self._count()


class ProgramsPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="Page.TFrame")
        self.app = app
        self.programs = []
        outer, c = card(self)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(c)
        top.pack(fill="x")
        ttk.Label(top, text="Programmes installés", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="Actualiser", command=self.refresh).pack(side="right")
        ttk.Button(top, text="Désinstaller…", style="Accent.TButton", command=self.uninstall).pack(side="right", padx=8)
        self.search = tk.StringVar()
        self.search.trace_add("write", self._fill)
        ttk.Entry(top, textvariable=self.search, width=30).pack(side="right", padx=(0, 8))
        ttk.Label(top, text="Rechercher :", style="Muted.TLabel").pack(side="right", padx=4)
        self.info = autowrap(ttk.Label(c, style="Muted.TLabel", text="Chargement…"))
        self.info.pack(anchor="w", fill="x", pady=(4, 10))
        frame, self.tree = scrolled_tree(c, [("name", "Nom", 280), ("pub", "Éditeur", 200),
                                             ("ver", "Version", 110), ("size", "Taille", 90)],
                                         selectmode="browse")
        frame.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.uninstall())
        self.refresh()

    def refresh(self):
        self.app.bg(programs.list_programs, self._loaded)

    def _loaded(self, res):
        if not self.app.failed(res):
            self.programs = res
            self._fill()

    def _fill(self, *_):
        q = self.search.get().lower()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for i, p in enumerate(self.programs):
            if q in p.name.lower() or q in p.publisher.lower():
                shown += 1
                self.tree.insert("", "end", iid=str(i), values=(
                    p.name, p.publisher, p.version, human(p.size_kb * 1024) if p.size_kb else ""))
        self.info["text"] = (f"{shown} programme(s) sur {len(self.programs)}. Liste en lecture seule : "
                             "« Désinstaller » lance le désinstalleur officiel du programme.")

    def uninstall(self):
        sel = self.tree.selection()
        if not sel:
            return
        p = self.programs[int(sel[0])]
        if messagebox.askyesno("Désinstaller", f"Lancer le désinstalleur officiel de « {p.name} » ?\n\n{p.uninstall}"):
            try:
                programs.uninstall(p)
            except OSError as e:
                messagebox.showerror("Désinstaller", str(e))


class DiskPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="Page.TFrame")
        self.app = app
        self.nodes = {}
        self.cancel = threading.Event()
        outer, c = card(self)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(c)
        top.pack(fill="x")
        # Boutons à droite placés en premier : si la place manque, pack rogne ce qui vient après.
        self.btn = ttk.Button(top, text="Analyser", style="Accent.TButton", command=self.analyze)
        self.btn.pack(side="right")
        ttk.Button(top, text="Annuler", command=lambda: self.cancel.set()).pack(side="right", padx=8)
        ttk.Label(top, text="Lecteur ou dossier :", style="Section.TLabel").pack(side="left")
        self.target = ttk.Combobox(top, values=winapi.drives(), width=22)
        self.target.current(0)
        self.target.pack(side="left", padx=8)
        ttk.Button(top, text="Dossier…", command=self.pick).pack(side="left")
        self.bar = ttk.Progressbar(c, mode="determinate")
        self.bar.pack(fill="x", pady=(12, 6))
        self.info = autowrap(ttk.Label(c, style="Muted.TLabel", text="Choisissez un lecteur puis « Analyser »."))
        self.info.pack(anchor="w", fill="x", pady=(0, 10))
        bottom = ttk.Frame(c)
        bottom.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(bottom, text="Ouvrir dans l'Explorateur", command=self.open).pack(side="right")
        frame, self.tree = scrolled_tree(c, [("#0", "Nom", 400), ("size", "Taille", 110),
                                             ("pct", "% du parent", 100)], selectmode="browse")
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewOpen>>", self._expand)

    def pick(self):
        folder = filedialog.askdirectory()
        if folder:
            self.target.set(os.path.normpath(folder))

    def analyze(self):
        path, self.cancel = self.target.get(), threading.Event()
        cancel = self.cancel
        if not os.path.isdir(path):
            return
        self.btn.state(["disabled"])
        animate(self.bar, True)
        progress = throttled(self.app, lambda p: self.info.configure(text=f"Analyse : {p}"))
        self.app.bg(lambda: diskscan.scan(path, cancel, progress), self._done)

    def _done(self, root):
        self.btn.state(["!disabled"])
        animate(self.bar, False)
        if self.app.failed(root):
            return
        partial = "Annulé : résultats partiels. " if self.cancel.is_set() else ""
        self.info["text"] = f"{partial}Total : {human(root.size)}. Dépliez un dossier pour voir son contenu."
        self.tree.delete(*self.tree.get_children())
        self.nodes = {}
        iid = self.tree.insert("", "end", text=root.path, values=(human(root.size), "100 %"), open=True)
        self.nodes[iid] = root
        self._fill(iid, root)

    def _fill(self, iid, node):
        for child in node.children:
            pct = f"{100 * child.size / node.size:.1f} %".replace(".", ",") if node.size else ""
            cid = self.tree.insert(iid, "end", text=child.name, values=(human(child.size), pct),
                                   tags=() if child.is_dir else ("muted",))
            self.nodes[cid] = child
            if child.children:
                self.tree.insert(cid, "end", text="…")  # rempli à l'ouverture : l'arbre complet serait trop lourd

    def _expand(self, _):
        iid = self.tree.focus()
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and kids[0] not in self.nodes:
            self.tree.delete(kids[0])
            self._fill(iid, self.nodes[iid])

    def open(self):
        sel = self.tree.selection()
        if sel and sel[0] in self.nodes:
            n = self.nodes[sel[0]]
            winapi.open_path(n.path if n.is_dir else os.path.dirname(n.path))


class DupPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="Page.TFrame")
        self.app = app
        self.files = {}  # iid -> [chemin, à_supprimer, taille]
        self.cancel = threading.Event()
        outer, c = card(self)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(c)
        top.pack(fill="x")
        ttk.Label(top, text="Dossier :", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="Parcourir…", command=self.pick).pack(side="right")
        self.folder = tk.StringVar(value=str(Path.home()))
        ttk.Entry(top, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
        row = ttk.Frame(c)
        row.pack(fill="x", pady=(10, 0))
        self.btn = ttk.Button(row, text="Rechercher", style="Accent.TButton", command=self.search)
        self.btn.pack(side="right")
        ttk.Button(row, text="Annuler", command=lambda: self.cancel.set()).pack(side="right", padx=8)
        ttk.Label(row, text="Taille minimale (Mo) :", style="Muted.TLabel").pack(side="left")
        self.min_mb = tk.StringVar(value="1")
        ttk.Spinbox(row, from_=0, to=100000, textvariable=self.min_mb, width=6).pack(side="left", padx=6)
        self.bar = ttk.Progressbar(c, mode="determinate")
        self.bar.pack(fill="x", pady=(12, 6))
        self.info = autowrap(ttk.Label(c, style="Muted.TLabel",
                                       text="Double-clic ou Espace : basculer Garder / Corbeille. "
                                            "Les dossiers système ne sont jamais parcourus."))
        self.info.pack(anchor="w", fill="x", pady=(0, 10))
        bottom = ttk.Frame(c)
        bottom.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(bottom, text="Envoyer les fichiers marqués à la corbeille", style="BigSuccess.TButton",
                   command=self.delete).pack(side="right")
        frame, self.tree = scrolled_tree(c, [("#0", "Fichier", 480), ("size", "Taille", 100),
                                             ("action", "Action", 120)])
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("group", font=(FONT, 10, "bold"))
        self.tree.tag_configure("trash", foreground="#c0392b")
        self.tree.bind("<Double-1>", lambda e: self.toggle(self.tree.identify_row(e.y)))
        self.tree.bind("<space>", lambda e: self.toggle(self.tree.focus()))

    def pick(self):
        folder = filedialog.askdirectory(initialdir=self.folder.get())
        if folder:
            self.folder.set(os.path.normpath(folder))

    def search(self):
        folder, self.cancel = self.folder.get(), threading.Event()
        cancel = self.cancel
        try:
            min_size = max(1, int(float(self.min_mb.get().replace(",", ".")) * 1024 * 1024))
        except ValueError:
            min_size = 1
        self.btn.state(["disabled"])
        animate(self.bar, True)
        progress = throttled(self.app, lambda p: self.info.configure(text=f"Analyse : {p}"))
        self.app.bg(lambda: duplicates.find(folder, min_size, cancel, progress), self._found)

    def _found(self, groups):
        self.btn.state(["!disabled"])
        animate(self.bar, False)
        if self.app.failed(groups):
            return
        self.tree.delete(*self.tree.get_children())
        self.files = {}
        for size, paths in groups:
            g = self.tree.insert("", "end", text=f"{len(paths)} copies identiques de {human(size)}",
                                 open=True, tags=("group",))
            keep = duplicates.keeper(paths)
            for p in paths:
                iid = self.tree.insert(g, "end", text=p, values=(human(size), ""))
                self.files[iid] = [p, p != keep, size]
                self._show(iid)
        waste = sum(size * (len(paths) - 1) for size, paths in groups)
        self.info["text"] = ("Recherche annulée." if self.cancel.is_set() else
                             f"{len(groups)} groupe(s) de doublons, {human(waste)} récupérables. "
                             "Double-clic ou Espace : basculer Garder / Corbeille.")

    def _show(self, iid):
        trash = self.files[iid][1]
        self.tree.set(iid, "action", "→ Corbeille" if trash else "Garder")
        self.tree.item(iid, tags=("trash",) if trash else ())

    def toggle(self, iid):
        if iid in self.files:
            self.files[iid][1] = not self.files[iid][1]
            self._show(iid)

    def delete(self):
        marked = [iid for iid, (_, flag, _) in self.files.items() if flag]
        if not marked:
            return
        for g in self.tree.get_children():
            kids = self.tree.get_children(g)
            if kids and all(self.files[k][1] for k in kids):
                messagebox.showerror("Doublons", "Chaque groupe doit garder au moins un fichier :\n"
                                     + self.tree.item(g, "text"))
                return
        total = sum(self.files[i][2] for i in marked)
        if self.app.dry_run.get():
            log.info("Simulation doublons : %d fichiers (%s) iraient à la corbeille", len(marked), human(total))
            messagebox.showinfo("Simulation", f"Simulation : {len(marked)} fichier(s) ({human(total)}) "
                                "seraient envoyés à la corbeille.\nDécochez « Mode simulation » pour agir.")
            return
        if not messagebox.askyesno("Corbeille", f"Envoyer {len(marked)} fichier(s) ({human(total)}) à la corbeille ?\n"
                                   "Ils resteront récupérables depuis la corbeille."):
            return
        paths = {iid: self.files[iid][0] for iid in marked}

        def work():
            done, errors = [], []
            for iid, p in paths.items():
                try:
                    duplicates.trash(p)
                    done.append(iid)
                except OSError as e:
                    errors.append(str(e))
            return done, errors
        self.app.bg(work, self._deleted)

    def _deleted(self, res):
        if self.app.failed(res):
            return
        done, errors = res
        for iid in done:
            self.tree.delete(iid)
            del self.files[iid]
        for g in self.tree.get_children():
            if len(self.tree.get_children(g)) < 2:  # plus un doublon
                for k in self.tree.get_children(g):
                    self.files.pop(k, None)
                self.tree.delete(g)
        msg = f"{len(done)} fichier(s) envoyé(s) à la corbeille."
        self.info["text"] = msg
        if errors:
            messagebox.showwarning("Doublons", msg + "\n\nÉchecs :\n" + "\n".join(errors[:10]))
        self.app.refresh_sysinfo()


class OptionsPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="Page.TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)

        outer, sec = card(self)
        outer.grid(row=0, column=0, sticky="ew")
        ttk.Label(sec, text="Sécurité", style="Section.TLabel").pack(anchor="w")
        ttk.Checkbutton(sec, text="Mode simulation : analyser et journaliser sans rien supprimer",
                        variable=app.dry_run).pack(anchor="w", pady=(6, 0))
        rights = "administrateur" if app.admin else "utilisateur standard"
        ttk.Label(sec, text=f"CleanLite tourne en {rights}.", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        if not app.admin:
            ttk.Button(sec, text="Relancer en administrateur", command=app.relaunch_admin).pack(anchor="w", pady=(6, 0))

        outer, sch = card(self)
        outer.grid(row=1, column=0, sticky="ew", pady=16)
        ttk.Label(sch, text="Nettoyage planifié", style="Section.TLabel").pack(anchor="w")
        safe = cleaner.auto_categories()
        windows = ", ".join(c.label[0].lower() + c.label[1:] for c in safe if c.group == "Windows")  # garde « DNS »
        browsers = ", ".join(c.group for c in safe if c.group != "Windows")
        autowrap(ttk.Label(sch, justify="left", style="Muted.TLabel",
                           text=f"Chaque dimanche à 12:00 (rattrapé si le PC était éteint), sans fenêtre, "
                                f"catégories sûres uniquement : {windows} ; cache de {browsers}. "
                                "Les navigateurs ouverts sont passés.")).pack(anchor="w", fill="x", pady=(4, 0))
        row = ttk.Frame(sch)
        row.pack(fill="x", pady=(10, 0))
        self.sch_state = ttk.Label(row, text="État : vérification…")
        self.sch_state.pack(side="left")
        ttk.Button(row, text="Désactiver", command=lambda: self._schedule(schedule.disable)).pack(side="right")
        ttk.Button(row, text="Activer", style="Accent.TButton",
                   command=lambda: self._schedule(schedule.enable)).pack(side="right", padx=8)
        self.app.bg(schedule.is_enabled, self._sch_show)

        outer, logf = card(self)
        outer.grid(row=2, column=0, sticky="ew")
        ttk.Label(logf, text="Journal des actions", style="Section.TLabel").pack(anchor="w")
        row = ttk.Frame(logf)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Ouvrir", command=lambda: winapi.open_path(DATA_DIR / "log.txt")).pack(side="right")
        ttk.Label(row, text=str(DATA_DIR / "log.txt"), style="Muted.TLabel").pack(side="left")

        autowrap(ttk.Label(self, text=EXCLUDED, justify="left", style="Muted.TLabel",
                           background=BG)).grid(row=3, column=0, sticky="ew", pady=16)

    def _schedule(self, action):
        ok, out = action()
        if not ok:
            messagebox.showerror("Planification", out.strip() or "Échec de la planification.")
        self.app.bg(schedule.is_enabled, self._sch_show)

    def _sch_show(self, enabled):
        self.sch_state["text"] = "État : activé" if enabled is True else "État : désactivé"


def main():
    winapi.init_process()  # avant la création de la fenêtre
    setup_log()
    log.info("Démarrage de CleanLite (admin=%s)", winapi.is_admin())
    App().mainloop()
