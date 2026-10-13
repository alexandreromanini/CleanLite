"""Doublons : regroupement par taille puis blake2b ; suppression vers la corbeille, hors dossiers système."""
import hashlib
import os
import os.path as osp
import re
import stat
from collections import defaultdict
from pathlib import Path

from . import winapi
from .safety import is_allowed, is_link, log

SKIP_NAMES = {"$recycle.bin", "system volume information", "windows", "program files",
              "program files (x86)", "programdata", "appdata"}
# « Copie de x », « Copy of x », « x - Copie », « x - Copie (2) », « x (1) »
COPY_NAME = re.compile(r"^(copie de|copy of) | - (copie|copy)( \(\d+\))?$| \(\d+\)$", re.IGNORECASE)
DOWNLOAD_DIRS = {"downloads", "téléchargements"}


def system_dirs(env=os.environ):
    keys = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
            "APPDATA", "LOCALAPPDATA")
    return [env[k] for k in keys if env.get(k)]


def is_system_path(path, dirs=None):
    dirs = system_dirs() if dirs is None else dirs
    real = osp.normcase(osp.realpath(path))
    return any(real == osp.normcase(osp.realpath(d)) or is_allowed(path, [d]) for d in dirs)


def hash_file(path, cancel=None):
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            h.update(chunk)
    return h.hexdigest()


def find(folder, min_size=1, cancel=None, progress=None, dirs=None):
    """Groupes de fichiers identiques [(taille, [chemins])], plus gros gain d'abord."""
    dirs = system_dirs() if dirs is None else dirs
    if is_system_path(folder, dirs):
        raise PermissionError(f"Dossier système refusé : {folder}")
    blocked = {osp.normcase(osp.realpath(d)) for d in dirs}
    by_size = defaultdict(list)
    seen = set()
    # On ne traverse jamais de lien : un sous-dossier système n'est atteignable que par lui-même.
    for dirpath, dirnames, filenames in os.walk(osp.realpath(folder)):
        if cancel is not None and cancel.is_set():
            return []
        if progress:
            progress(dirpath)
        keep = []
        for d in dirnames:
            p = osp.join(dirpath, d)
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            if not is_link(st) and d.lower() not in SKIP_NAMES and osp.normcase(p) not in blocked:
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            p = osp.join(dirpath, f)
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            # Les fichiers OneDrive « à la demande » sont des points d'analyse : les lire les téléchargerait.
            if not stat.S_ISREG(st.st_mode) or is_link(st) or st.st_size < min_size:
                continue
            # Liens physiques : un même fichier sous deux noms, en supprimer un ne libère rien.
            inode = (st.st_dev, st.st_ino)
            if st.st_ino and inode in seen:
                continue
            seen.add(inode)
            by_size[st.st_size].append(p)

    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash = defaultdict(list)
        for p in paths:
            if progress:
                progress(p)
            try:
                by_hash[hash_file(p, cancel)].append(p)
            except InterruptedError:
                return []
            except OSError:
                continue  # verrouillé ou illisible
        groups += [(size, sorted(ps)) for ps in by_hash.values() if len(ps) > 1]
    return sorted(groups, key=lambda g: g[0] * (len(g[1]) - 1), reverse=True)


def keeper(paths):
    """Fichier conservé par défaut dans un groupe ; les autres sont pré-cochés pour la corbeille.

    Par ordre de priorité : nom qui n'a pas l'air d'une copie, hors Téléchargements, chemin le moins
    profond, plus ancien, puis ordre alphabétique (résultat stable pour un même groupe).
    """
    def score(p):
        parts = Path(p).parts
        try:
            mtime = os.stat(p).st_mtime
        except OSError:  # disparu depuis l'analyse
            mtime = float("inf")
        return (bool(COPY_NAME.search(Path(p).stem)),
                any(s.lower() in DOWNLOAD_DIRS for s in parts),
                len(parts), mtime, p)
    return min(paths, key=score)


def trash(path):
    if is_system_path(path):
        raise PermissionError(f"Dossier système refusé : {path}")
    winapi.send_to_trash(path)
    log.info("Doublon envoyé à la corbeille : %s", path)
