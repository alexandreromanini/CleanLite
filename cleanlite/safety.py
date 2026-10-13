"""Garde-fous : liste blanche par chemin résolu, purge sûre (dry-run, fichiers verrouillés), journal."""
import logging
import os
import os.path as osp
import stat
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "CleanLite"
log = logging.getLogger("cleanlite")


def setup_log():
    """Journal des actions dans %LOCALAPPDATA%\\CleanLite\\log.txt."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(DATA_DIR / "log.txt", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


@dataclass
class Stats:
    files: int = 0
    bytes: int = 0
    skipped: int = 0  # verrouillés, en cours d'utilisation ou refusés

    def add(self, other):
        self.files += other.files
        self.bytes += other.bytes
        self.skipped += other.skipped


def _norm(path):
    return osp.normcase(osp.realpath(path))


def is_allowed(path, roots):
    """Vrai si le chemin *résolu* (liens, jonctions et '..' suivis) est strictement sous une racine.

    La racine elle-même n'est jamais autorisée : on vide un dossier, on ne le supprime pas.
    """
    real = _norm(path)
    for root in roots:
        r = _norm(root)
        try:
            if real != r and osp.commonpath([real, r]) == r:
                return True
        except ValueError:  # lecteurs différents
            pass
    return False


def is_link(st):
    """Lien symbolique ou point d'analyse Windows (jonction, fichier OneDrive à la demande…)."""
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0)
                                            & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def walk(top):
    """Parcours post-ordre sans jamais traverser un lien : (chemin, taille, mtime, est_dossier)."""
    try:
        st = os.stat(top, follow_symlinks=False)
    except OSError:
        return
    if is_link(st) or not stat.S_ISDIR(st.st_mode):
        yield top, 0 if is_link(st) else st.st_size, st.st_mtime, False
        return
    try:
        children = [e.path for e in os.scandir(top)]
    except OSError:
        children = []
    for child in children:
        yield from walk(child)
    yield top, 0, st.st_mtime, True


def purge(target, roots, dry_run, stats, min_age_s=0):
    """Supprime target (fichier ou arborescence) élément par élément, chacun revérifié contre roots.

    dry_run : compte sans rien toucher. Fichier verrouillé/protégé : ignoré et compté, jamais forcé.
    """
    cutoff = time.time() - min_age_s
    for path, size, mtime, is_dir in walk(str(target)):
        if not is_allowed(path, roots):
            if not is_dir:
                log.warning("Refusé (hors liste blanche) : %s", path)
                stats.skipped += 1
            continue
        if is_dir:
            if not dry_run:
                try:
                    os.rmdir(path)
                except OSError:
                    pass  # pas vide : il contient un fichier ignoré
            continue
        if min_age_s and mtime > cutoff:
            continue
        if not dry_run:
            try:
                os.remove(path)
            except OSError:
                stats.skipped += 1
                continue
        stats.files += 1
        stats.bytes += size
