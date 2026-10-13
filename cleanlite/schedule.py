"""Nettoyage hebdomadaire des catégories sûres via une tâche planifiée Windows."""
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
    # Chemin du script : la tâche ne fixe pas de dossier de travail.
    return [str(exe), str(Path(__file__).with_name("__main__.py")), "--auto"]


def ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def enable_script(day="Sunday", time="12:00"):
    """Register-ScheduledTask plutôt que schtasks : seul moyen d'exprimer ces réglages.

    StartWhenAvailable : rattrape le passage manqué (PC éteint le dimanche).
    AllowStartIfOnBatteries : sinon Windows ne lance jamais la tâche sur un portable débranché.
    """
    exe, *args = command()
    return (f"Register-ScheduledTask -Force -TaskName {ps_quote(TASK)} "
            f"-Action (New-ScheduledTaskAction -Execute {ps_quote(exe)} "
            f"-Argument {ps_quote(subprocess.list2cmdline(args))}) "
            f"-Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek {day} -At {time}) "
            "-Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries "
            "-DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)) | Out-Null")


def is_enabled():
    return winapi.schtasks("/Query", "/TN", TASK)[0]


def enable():
    ok, out = winapi.powershell(enable_script())
    log.info("Planification hebdomadaire : %s", "créée" if ok else out.strip())
    return ok, out


def disable():
    ok, out = winapi.schtasks("/Delete", "/TN", TASK, "/F")
    log.info("Planification hebdomadaire : %s", "supprimée" if ok else out.strip())
    return ok, out
