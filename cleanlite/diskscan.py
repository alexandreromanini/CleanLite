"""Analyseur de disque : arbre des dossiers trié par taille (lancé dans un thread par l'interface)."""
import heapq
import os
import stat
from dataclasses import dataclass, field

from .safety import is_link

MAX_FILES = 30  # seuls les 30 plus gros fichiers par dossier sont gardés, pour limiter la mémoire


@dataclass
class Node:
    name: str
    path: str
    size: int = 0
    is_dir: bool = True
    children: list = field(default_factory=list)


def scan(path, cancel=None, progress=None):
    """Taille logique (st_size) ; liens, jonctions et fichiers OneDrive « à la demande » ignorés."""
    node = Node(os.path.basename(path.rstrip("\\/")) or path, path)
    try:
        entries = list(os.scandir(path))
    except OSError:
        return node
    files = []
    for e in entries:
        if cancel is not None and cancel.is_set():
            break
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if is_link(st):
            continue
        if stat.S_ISDIR(st.st_mode):
            child = scan(e.path, cancel, progress)
            node.children.append(child)
            node.size += child.size
        else:
            node.size += st.st_size
            files.append(Node(e.name, e.path, st.st_size, False))
    node.children += heapq.nlargest(MAX_FILES, files, key=lambda n: n.size)
    node.children.sort(key=lambda n: n.size, reverse=True)
    if progress:
        progress(path)
    return node
