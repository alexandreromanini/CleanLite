"""Nettoyage hebdomadaire des catégories sûres via une tâche planifiée Windows (schtasks)."""
import subprocess
import sys
from pathlib import Path

from . import winapi
from .safety import log

TASK = "CleanLite - Nettoyage hebdo"  # à la racine : pas de dossier de tâches, pas d'admin requis


def command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--auto"]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    # Chemin du script : schtasks ne permet pas de fixer le dossier de travail.
    return [str(exe), str(Path(__file__).with_name("__main__.py")), "--auto"]


def is_enabled():
    return winapi.schtasks("/Query", "/TN", TASK)[0]


def enable(day="SUN", time="12:00"):
    ok, out = winapi.schtasks("/Create", "/TN", TASK, "/TR", subprocess.list2cmdline(command()),
                              "/SC", "WEEKLY", "/D", day, "/ST", time, "/F")
    log.info("Planification hebdomadaire : %s", "créée" if ok else out.strip())
    return ok, out


def disable():
    ok, out = winapi.schtasks("/Delete", "/TN", TASK, "/F")
    log.info("Planification hebdomadaire : %s", "supprimée" if ok else out.strip())
    return ok, out
