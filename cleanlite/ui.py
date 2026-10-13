"""Interface tkinter/ttk : un onglet par fonctionnalité ; les travaux longs tournent dans des threads."""
import heapq
import json
import os
import queue
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


def scrolled_tree(parent, cols, **kw):
    """Treeview + barre de défilement ; cols = [(id, titre, largeur)], '#0' = colonne arborescente."""
    frame = ttk.Frame(parent)
    ids = [c for c, _, _ in cols if c != "#0"]
    tree = ttk.Treeview(frame, columns=ids, show="tree headings" if cols[0][0] == "#0" else "headings", **kw)
    for cid, title, width in cols:
        anchor = "e" if cid in ("size", "pct") else "w"
        tree.heading(cid, text=title, anchor=anchor)
        tree.column(cid, width=width, anchor=anchor)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    return frame, tree


def throttled(app, fn, delay=0.2):
    """Rappel de progression appelable depuis un thread, limité à un message UI toutes les `delay` s."""
    last = [0.0]

    def progress(value):
        now = time.monotonic()
        if now - last[0] > delay:
            last[0] = now
            app.post(fn, value)
    return progress


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CleanLite")
        self.geometry("980x640")
        self.minsize(780, 480)
        icon = BASE / "assets" / "icon.ico"
        if winapi.IS_WINDOWS and icon.exists():
            self.iconbitmap(default=str(icon))
        self.admin = winapi.is_admin()
        self.dry_run = tk.BooleanVar(value=load_settings().get("dry_run", True))
        self.dry_run.trace_add("write", self._mode_changed)
        self._q = queue.Queue()
        self.after(100, self._poll)

        self.status = ttk.Label(self, anchor="w", padding=(8, 2))
        self.status.pack(side="bottom", fill="x")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        for title, tab in (("Nettoyeur", CleanerTab), ("Démarrage", StartupTab), ("Programmes", ProgramsTab),
                           ("Analyse disque", DiskTab), ("Doublons", DupTab), ("Options", OptionsTab)):
            nb.add(tab(nb, self), text=title)
        self._mode_changed()

    def _mode_changed(self, *_):
        save_settings({"dry_run": self.dry_run.get()})
        mode = "SIMULATION (rien n'est supprimé)" if self.dry_run.get() else "RÉEL"
        rights = "administrateur" if self.admin else "utilisateur standard"
        self.status["text"] = f"Mode : {mode}   •   Droits : {rights}   •   Journal : {DATA_DIR / 'log.txt'}"

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


class CleanerTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.cats = {c.key: c for c in cleaner.categories()}
        self.checked = {k: c.default and not self._locked(c) for k, c in self.cats.items()}
        self.preview = {}  # clé -> [(chemin, taille)] de la dernière analyse

        side = ttk.Frame(self)
        side.pack(side="right", fill="y", padx=(8, 0))
        self.btn_analyze = ttk.Button(side, text="Analyser", command=self.analyze)
        self.btn_analyze.pack(fill="x")
        ttk.Button(side, text="Aperçu des fichiers", command=self.show_files).pack(fill="x", pady=(4, 0))
        self.btn_clean = ttk.Button(side, text="Nettoyer", command=self.clean)
        self.btn_clean.pack(fill="x", pady=4)
        ttk.Checkbutton(side, text="Mode simulation", variable=app.dry_run).pack(anchor="w", pady=4)
        if not app.admin:
            ttk.Label(side, text="Les catégories grisées demandent les droits administrateur.",
                      wraplength=220, foreground="gray").pack(anchor="w", pady=(12, 2))
            ttk.Button(side, text="Relancer en administrateur", command=app.relaunch_admin).pack(fill="x")
        self.result = ttk.Label(side, wraplength=220, justify="left",
                                text="Cochez des catégories puis cliquez sur « Analyser » : "
                                     "rien n'est supprimé avant « Nettoyer ».")
        self.result.pack(anchor="w", pady=12)

        frame, self.tree = scrolled_tree(self, [("#0", "Catégorie", 360), ("size", "Récupérable", 120)])
        frame.pack(fill="both", expand=True)
        self.tree.tag_configure("locked", foreground="gray")
        groups = {}
        for c in self.cats.values():
            if c.group not in groups:
                groups[c.group] = self.tree.insert("", "end", text=c.group, open=True)
            self.tree.insert(groups[c.group], "end", iid=c.key, text=self._text(c),
                             tags=("locked",) if self._locked(c) else ())
        self.tree.bind("<Button-1>", lambda e: self.toggle(self.tree.identify_row(e.y)))
        self.tree.bind("<space>", lambda e: self.toggle(self.tree.focus()))

    def _locked(self, c):
        return c.admin and not self.app.admin

    def _text(self, c):
        return f"{'☑' if self.checked[c.key] else '☐'}  {c.label}" + ("  (admin)" if c.admin else "")

    def toggle(self, iid):
        c = self.cats.get(iid)
        if c and not self._locked(c):
            self.checked[iid] = not self.checked[iid]
            self.tree.item(iid, text=self._text(c))

    def _busy(self, busy):
        for b in (self.btn_analyze, self.btn_clean):
            b.state(["disabled"] if busy else ["!disabled"])

    def _run(self, cats, dry_run, done):
        self._busy(True)
        self.app.bg(lambda: [(c, cleaner.run(c, dry_run)) for c in cats], done)

    def analyze(self):
        cats = [c for k, c in self.cats.items() if self.checked[k]]

        def work():
            out = []
            for c in cats:
                files = []
                out.append((c, cleaner.run(c, True, files), files))
            return out
        self.result["text"] = "Analyse en cours…"
        self._busy(True)
        self.app.bg(work, self._analyzed)

    def _analyzed(self, res):
        self._busy(False)
        if self.app.failed(res):
            return
        for k in self.cats:
            self.tree.set(k, "size", "")
        self.preview = {c.key: files for c, _, files in res}
        for c, st, _ in res:
            self.tree.set(c.key, "size", "—" if c.key == "dns" else human(st.bytes))
        total = sum(st.bytes for _, st, _ in res)
        self.result["text"] = (f"Récupérable : {human(total)}\n\nSélectionnez une catégorie puis « Aperçu des "
                               "fichiers » pour voir le détail, ou « Nettoyer » pour libérer cet espace.")

    def show_files(self):
        key = self.tree.focus()
        c = self.cats.get(key)
        if c is None or key not in self.preview:
            messagebox.showinfo("Aperçu", "Cochez la catégorie, lancez « Analyser », puis sélectionnez-la.")
            return
        if key in ("recycle", "dns"):
            messagebox.showinfo("Aperçu", "Cette catégorie est vidée en bloc par Windows : pas de liste de fichiers.")
            return
        files = self.preview[key]
        top = tk.Toplevel(self)
        top.title(f"Aperçu : {c.group} – {c.label}")
        top.geometry("820x460")
        more = f" Les {PREVIEW_MAX} plus gros sont affichés." if len(files) > PREVIEW_MAX else ""
        ttk.Label(top, padding=6, text=f"{len(files)} fichier(s), {human(sum(s for _, s in files))}. "
                                       f"Rien n'a été supprimé.{more}").pack(anchor="w")
        frame, tree = scrolled_tree(top, [("path", "Fichier", 680), ("size", "Taille", 100)])
        frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
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
        self.result["text"] = "Simulation en cours…" if dry else "Nettoyage en cours…"
        self._run(cats, dry, lambda res: self._cleaned(res, dry))

    def _cleaned(self, res, dry):
        self._busy(False)
        if self.app.failed(res):
            return
        total = Stats()
        for c, st in res:
            total.add(st)
            self.tree.set(c.key, "size", "—" if c.key == "dns" else human(st.bytes))
        if dry:
            log.info("Simulation : %s seraient libérés (%d fichiers)", human(total.bytes), total.files)
            text = (f"Simulation : {human(total.bytes)} seraient libérés.\nAucun fichier supprimé.\n\n"
                    "Décochez « Mode simulation » pour nettoyer réellement.")
        else:
            text = f"Libéré : {human(total.bytes)} ({total.files} fichiers)."
        if total.skipped:
            text += f"\n{total.skipped} élément(s) ignoré(s) : verrouillés ou en cours d'utilisation."
        self.result["text"] = text


class StartupTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.entries = []
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Actualiser", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Activer", command=lambda: self.set_enabled(True)).pack(side="left", padx=4)
        ttk.Button(bar, text="Désactiver", command=lambda: self.set_enabled(False)).pack(side="left")
        ttk.Label(bar, text="Rien n'est supprimé : une entrée désactivée se réactive à tout moment.",
                  foreground="gray").pack(side="left", padx=12)
        frame, self.tree = scrolled_tree(self, [("name", "Nom", 200), ("state", "État", 90),
                                                ("where", "Emplacement", 180), ("cmd", "Commande", 420)])
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
            self.tree.insert("", "end", iid=str(i), values=(
                e.name, "Activé" if e.enabled else "Désactivé", e.location, e.command))

    def set_enabled(self, enabled):
        for iid in self.tree.selection():
            e = self.entries[int(iid)]
            try:
                startup.set_enabled(e, enabled)
            except OSError as err:
                hint = "" if self.app.admin else "\n\nDroits administrateur probablement requis (onglet Options)."
                messagebox.showerror("Démarrage", f"{e.name} : {err}{hint}")
                return
            self.tree.set(iid, "state", "Activé" if enabled else "Désactivé")


class ProgramsTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.programs = []
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Rechercher :").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", self._fill)
        ttk.Entry(bar, textvariable=self.search, width=30).pack(side="left", padx=4)
        ttk.Button(bar, text="Désinstaller…", command=self.uninstall).pack(side="left", padx=4)
        ttk.Button(bar, text="Actualiser", command=self.refresh).pack(side="left")
        frame, self.tree = scrolled_tree(self, [("name", "Nom", 330), ("pub", "Éditeur", 220),
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
        for i, p in enumerate(self.programs):
            if q in p.name.lower() or q in p.publisher.lower():
                self.tree.insert("", "end", iid=str(i), values=(
                    p.name, p.publisher, p.version, human(p.size_kb * 1024) if p.size_kb else ""))

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


class DiskTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.nodes = {}
        self.cancel = threading.Event()
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Lecteur :").pack(side="left")
        self.target = ttk.Combobox(bar, values=winapi.drives(), width=24)
        self.target.current(0)
        self.target.pack(side="left", padx=4)
        ttk.Button(bar, text="Dossier…", command=self.pick).pack(side="left")
        self.btn = ttk.Button(bar, text="Analyser", command=self.analyze)
        self.btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Annuler", command=lambda: self.cancel.set()).pack(side="left")
        ttk.Button(bar, text="Ouvrir dans l'Explorateur", command=self.open).pack(side="left", padx=4)
        self.progress = ttk.Label(self, text="Choisissez un lecteur puis « Analyser ».")
        self.progress.pack(fill="x", pady=(0, 6))
        frame, self.tree = scrolled_tree(self, [("#0", "Nom", 480), ("size", "Taille", 110),
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
        progress = throttled(self.app, lambda p: self.progress.configure(text=f"Analyse : {p}"))
        self.app.bg(lambda: diskscan.scan(path, cancel, progress), self._done)

    def _done(self, root):
        self.btn.state(["!disabled"])
        if self.app.failed(root):
            return
        partial = "Annulé : résultats partiels. " if self.cancel.is_set() else ""
        self.progress["text"] = f"{partial}Total : {human(root.size)}"
        self.tree.delete(*self.tree.get_children())
        self.nodes = {}
        iid = self.tree.insert("", "end", text=root.path, values=(human(root.size), "100 %"), open=True)
        self.nodes[iid] = root
        self._fill(iid, root)

    def _fill(self, iid, node):
        for child in node.children:
            pct = f"{100 * child.size / node.size:.1f} %".replace(".", ",") if node.size else ""
            cid = self.tree.insert(iid, "end", text=child.name, values=(human(child.size), pct))
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


class DupTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        self.files = {}  # iid -> [chemin, à_supprimer, taille]
        self.cancel = threading.Event()
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Dossier :").pack(side="left")
        self.folder = tk.StringVar(value=str(Path.home()))
        ttk.Entry(bar, textvariable=self.folder, width=40).pack(side="left", padx=4)
        ttk.Button(bar, text="Parcourir…", command=self.pick).pack(side="left")
        ttk.Label(bar, text="Taille min. (Mo) :").pack(side="left", padx=(12, 2))
        self.min_mb = tk.StringVar(value="1")
        ttk.Spinbox(bar, from_=0, to=100000, textvariable=self.min_mb, width=6).pack(side="left")
        self.btn = ttk.Button(bar, text="Rechercher", command=self.search)
        self.btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Annuler", command=lambda: self.cancel.set()).pack(side="left")
        bar2 = ttk.Frame(self)
        bar2.pack(fill="x", pady=(0, 6))
        ttk.Button(bar2, text="Envoyer les fichiers marqués à la corbeille", command=self.delete).pack(side="left")
        self.progress = ttk.Label(bar2, text="Double-clic ou Espace : basculer Garder / Corbeille. "
                                             "Les dossiers système ne sont jamais parcourus.")
        self.progress.pack(side="left", padx=8)
        frame, self.tree = scrolled_tree(self, [("#0", "Fichier", 600), ("size", "Taille", 100),
                                                ("action", "Action", 120)])
        frame.pack(fill="both", expand=True)
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
        progress = throttled(self.app, lambda p: self.progress.configure(text=f"Analyse : {p}"))
        self.app.bg(lambda: duplicates.find(folder, min_size, cancel, progress), self._found)

    def _found(self, groups):
        self.btn.state(["!disabled"])
        if self.app.failed(groups):
            return
        self.tree.delete(*self.tree.get_children())
        self.files = {}
        for size, paths in groups:
            g = self.tree.insert("", "end", text=f"{len(paths)} copies identiques de {human(size)}", open=True)
            keep = duplicates.keeper(paths)
            for p in paths:
                iid = self.tree.insert(g, "end", text=p, values=(human(size), ""))
                self.files[iid] = [p, p != keep, size]
                self._show(iid)
        waste = sum(size * (len(paths) - 1) for size, paths in groups)
        self.progress["text"] = ("Recherche annulée." if self.cancel.is_set() else
                                 f"{len(groups)} groupe(s) de doublons, {human(waste)} récupérables.")

    def _show(self, iid):
        self.tree.set(iid, "action", "→ Corbeille" if self.files[iid][1] else "Garder")

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
        self.progress["text"] = msg
        if errors:
            messagebox.showwarning("Doublons", msg + "\n\nÉchecs :\n" + "\n".join(errors[:10]))


class OptionsTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app = app
        sec = ttk.LabelFrame(self, text="Sécurité", padding=8)
        sec.pack(fill="x")
        ttk.Checkbutton(sec, text="Mode simulation : analyser et journaliser sans rien supprimer",
                        variable=app.dry_run).pack(anchor="w")
        if not app.admin:
            ttk.Button(sec, text="Relancer en administrateur", command=app.relaunch_admin).pack(anchor="w", pady=(6, 0))

        sch = ttk.LabelFrame(self, text="Nettoyage planifié", padding=8)
        sch.pack(fill="x", pady=8)
        safe = ", ".join(f"{c.group} – {c.label}" for c in cleaner.auto_categories())
        ttk.Label(sch, wraplength=900, justify="left",
                  text=f"Chaque dimanche à 12:00, sans fenêtre, catégories sûres uniquement : {safe}. "
                       "Les navigateurs ouverts sont passés.").pack(anchor="w")
        self.sch_state = ttk.Label(sch, text="État : vérification…")
        self.sch_state.pack(anchor="w", pady=4)
        row = ttk.Frame(sch)
        row.pack(anchor="w")
        ttk.Button(row, text="Activer", command=lambda: self._schedule(schedule.enable)).pack(side="left")
        ttk.Button(row, text="Désactiver", command=lambda: self._schedule(schedule.disable)).pack(side="left", padx=4)
        self.app.bg(schedule.is_enabled, self._sch_show)

        logf = ttk.LabelFrame(self, text="Journal des actions", padding=8)
        logf.pack(fill="x")
        ttk.Label(logf, text=str(DATA_DIR / "log.txt")).pack(side="left")
        ttk.Button(logf, text="Ouvrir", command=lambda: winapi.open_path(DATA_DIR / "log.txt")).pack(side="left", padx=8)
        ttk.Label(self, text=EXCLUDED, wraplength=900, justify="left", foreground="gray").pack(anchor="w", pady=12)

    def _schedule(self, action):
        ok, out = action()
        if not ok:
            messagebox.showerror("Planification", out.strip() or "Échec de schtasks.")
        self.app.bg(schedule.is_enabled, self._sch_show)

    def _sch_show(self, enabled):
        self.sch_state["text"] = "État : activé" if enabled is True else "État : désactivé"


def main():
    winapi.init_process()  # avant la création de la fenêtre
    setup_log()
    log.info("Démarrage de CleanLite (admin=%s)", winapi.is_admin())
    App().mainloop()
