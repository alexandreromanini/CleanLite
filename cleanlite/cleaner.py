"""Nettoyeur : catégories (liste blanche stricte de racines + motifs) et exécution analyse/nettoyage."""
import glob
import os
from dataclasses import dataclass
from pathlib import Path

from . import winapi
from .safety import Stats, is_safe_root, log, purge

CRITICAL_VARS = ("USERPROFILE", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData",
                 "APPDATA", "LOCALAPPDATA")


@dataclass
class Category:
    key: str
    label: str
    group: str
    targets: list   # [(racine autorisée, motif glob relatif)] : rien n'est supprimé hors de la racine
    default: bool = True
    admin: bool = False
    process: str = ""  # exécutable du navigateur, à fermer avant nettoyage
    min_age_h: int = 0  # protège les fichiers récents (installations en cours dans %TEMP%)


def categories(env=os.environ):
    def p(var, *parts):
        return os.path.join(env[var], *parts) if env.get(var) else ""

    win = env.get("SystemRoot", r"C:\Windows")
    wer_user = p("LOCALAPPDATA", "Microsoft", "Windows", "WER")
    wer_sys = os.path.join(env.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows", "WER")
    cats = [
        Category("temp", "Fichiers temporaires", "Windows", [(p("TEMP"), "*")], min_age_h=24),
        Category("wintemp", "Fichiers temporaires Windows", "Windows",
                 [(os.path.join(win, "Temp"), "*")], admin=True, min_age_h=24),
        Category("recycle", "Corbeille", "Windows", []),
        Category("thumbs", "Cache des miniatures", "Windows",
                 [(p("LOCALAPPDATA", "Microsoft", "Windows", "Explorer"), "thumbcache_*.db")]),
        Category("dns", "Cache DNS", "Windows", []),
        Category("crash", "Rapports d'erreur et dumps", "Windows",
                 [(wer_user, "ReportArchive/*"), (wer_user, "ReportQueue/*"),
                  (p("LOCALAPPDATA", "CrashDumps"), "*")]),
        Category("syslogs", "Journaux et rapports système", "Windows",
                 [(os.path.join(win, "Logs"), "**/*.log"), (os.path.join(win, "Minidump"), "*.dmp"),
                  (wer_sys, "ReportArchive/*"), (wer_sys, "ReportQueue/*")], admin=True),
        Category("wu", "Cache Windows Update", "Windows",
                 [(os.path.join(win, "SoftwareDistribution", "Download"), "*")], admin=True),
        Category("fonts", "Cache des polices", "Windows",
                 [(os.path.join(win, "ServiceProfiles", "LocalService", "AppData", "Local", "FontCache"), "*")],
                 admin=True),
        Category("recent", "Éléments récents", "Windows", [(p("APPDATA", "Microsoft", "Windows", "Recent"), "*.lnk")]),
    ]
    for name, data, exe in (("Chrome", p("LOCALAPPDATA", "Google", "Chrome", "User Data"), "chrome.exe"),
                            ("Edge", p("LOCALAPPDATA", "Microsoft", "Edge", "User Data"), "msedge.exe"),
                            ("Brave", p("LOCALAPPDATA", "BraveSoftware", "Brave-Browser", "User Data"), "brave.exe")):
        k = name.lower()
        cats += [  # « * » = chaque profil (Default, Profile 1…)
            Category(f"{k}_cache", "Cache", name,
                     [(data, f"*/{d}") for d in ("Cache", "Code Cache", "GPUCache")], process=exe),
            Category(f"{k}_cookies", "Cookies", name,
                     [(data, f"*/{f}") for f in ("Network/Cookies", "Network/Cookies-journal",
                                                 "Cookies", "Cookies-journal")], default=False, process=exe),
            Category(f"{k}_history", "Historique", name,
                     [(data, f"*/{f}") for f in ("History", "History-journal")], default=False, process=exe),
        ]
    # Pas d'historique Firefox : places.sqlite contient aussi les favoris.
    cats += [
        Category("firefox_cache", "Cache", "Firefox",
                 [(p("LOCALAPPDATA", "Mozilla", "Firefox", "Profiles"), "*/cache2")], process="firefox.exe"),
        Category("firefox_cookies", "Cookies", "Firefox",
                 [(p("APPDATA", "Mozilla", "Firefox", "Profiles"), f"*/{f}")
                  for f in ("cookies.sqlite", "cookies.sqlite-wal")], default=False, process="firefox.exe"),
    ]
    return cats


def run(cat, dry_run, collect=None):
    """Analyse (dry_run=True : rien n'est touché) ou nettoie une catégorie ; collect : voir purge."""
    st = Stats()
    if cat.key == "recycle":
        st.bytes, st.files = winapi.recycle_bin_size()
        if not dry_run and st.files and not winapi.empty_recycle_bin():
            st = Stats(skipped=st.files)
    elif cat.key == "dns":
        if not dry_run and not winapi.flush_dns():
            st.skipped = 1
    critical = [str(Path.home())] + [os.environ.get(k) for k in CRITICAL_VARS]
    for root, pattern in cat.targets:
        if root and not is_safe_root(root, critical):
            log.warning("Racine refusée (contient un dossier critique) : %s", root)
            st.skipped += 1
        elif root and os.path.isdir(root):
            for match in glob.glob(os.path.join(glob.escape(root), pattern), recursive=True):
                purge(match, [root], dry_run, st, cat.min_age_h * 3600, collect)
    if not dry_run:
        log.info("Nettoyage %s – %s : %d fichiers, %d octets, %d ignorés",
                 cat.group, cat.label, st.files, st.bytes, st.skipped)
    return st


def auto_categories():
    """Catégories sûres du nettoyage planifié : cochées par défaut, sans admin, hors corbeille."""
    return [c for c in categories() if c.default and not c.admin and c.key != "recycle"]


def run_auto():
    """Nettoyage planifié sans interface ; les navigateurs ouverts sont passés."""
    running = winapi.running_processes()
    total = Stats()
    for c in auto_categories():
        if c.process not in running:
            total.add(run(c, dry_run=False))
    log.info("Nettoyage planifié terminé : %.1f Mo libérés, %d fichiers ignorés",
             total.bytes / 1e6, total.skipped)
    return total
