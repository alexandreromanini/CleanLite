"""Programmes installés (registre, lecture seule) et lancement du désinstalleur officiel."""
from dataclasses import dataclass

from . import winapi
from .safety import log

UNINSTALL_KEYS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
]


@dataclass
class Program:
    name: str
    version: str
    publisher: str
    size_kb: int
    uninstall: str


def list_programs():
    found = {}
    for hive, key in UNINSTALL_KEYS:
        for sub in winapi.reg_subkeys(hive, key):
            v = winapi.reg_values(hive, rf"{key}\{sub}")
            name, cmd = v.get("DisplayName"), v.get("UninstallString")
            # Composants système et mises à jour (ParentKeyName) : masqués, comme dans Windows.
            if not name or not cmd or v.get("SystemComponent") == 1 or v.get("ParentKeyName"):
                continue
            size = v.get("EstimatedSize")
            found.setdefault((name, v.get("DisplayVersion", "")), Program(
                name, str(v.get("DisplayVersion", "")), str(v.get("Publisher", "")),
                size if isinstance(size, int) else 0, cmd))
    return sorted(found.values(), key=lambda p: p.name.lower())


def split_command(cmd):
    """Sépare exécutable et arguments, y compris un chemin avec espaces non entre guillemets."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        exe, _, args = cmd[1:].partition('"')
        return exe, args.strip()
    i = cmd.lower().find(".exe")
    if i >= 0:
        return cmd[:i + 4], cmd[i + 4:].strip()
    exe, _, args = cmd.partition(" ")
    return exe, args


def uninstall(program):
    """Lance l'UninstallString officiel ; Windows affiche l'invite UAC si le désinstalleur l'exige."""
    log.info("Désinstallation lancée : %s (%s)", program.name, program.uninstall)
    winapi.shell_execute(*split_command(program.uninstall))
