"""Gestionnaire de démarrage : liste, active et désactive. Ne supprime jamais rien.

Clés Run et dossier Démarrage : (dés)activation comme le Gestionnaire des tâches, via la valeur
binaire StartupApproved (1er octet pair = activé, impair = désactivé) ; l'entrée d'origine est intacte.
Tâches planifiées : schtasks /ENABLE|/DISABLE.
"""
import os
from dataclasses import dataclass

from . import winapi
from .safety import log

APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN_KEYS = [  # (ruche, clé, sous-clé StartupApproved correspondante)
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run", "Run"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "Run"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Run32"),
]
# Tâches lancées à l'ouverture de session ou au démarrage, hors tâches internes de Windows.
TASKS_PS = (r"Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' -and "
            r"($_.Triggers | Where-Object { $_.CimClass.CimClassName -in "
            r"'MSFT_TaskLogonTrigger','MSFT_TaskBootTrigger' }) } | "
            r"Select-Object TaskName, TaskPath, @{n='State';e={[string]$_.State}}, "
            r"@{n='Execute';e={($_.Actions | Select-Object -First 1).Execute}}")


@dataclass
class Entry:
    name: str
    command: str
    location: str
    enabled: bool
    hive: str = ""      # Run / dossier : où écrire StartupApproved
    approved: str = ""  # sous-clé StartupApproved (Run, Run32, StartupFolder)
    task: str = ""      # tâche planifiée : chemin complet


def is_enabled(data):
    """Décode une valeur StartupApproved ; absente = activé."""
    return not isinstance(data, bytes) or not data or not data[0] & 1


def list_entries(env=os.environ):
    out = []
    for hive, key, sub in RUN_KEYS:
        approved = winapi.reg_values(hive, rf"{APPROVED}\{sub}")
        for name, cmd in winapi.reg_values(hive, key).items():
            if name:
                out.append(Entry(name, str(cmd), f"Registre {hive} {sub}",
                                 is_enabled(approved.get(name)), hive, sub))
    folders = (("HKCU", env.get("APPDATA", ""), "Dossier Démarrage"),
               ("HKLM", env.get("ProgramData", ""), "Dossier Démarrage (tous)"))
    for hive, base, label in folders:
        folder = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        if not base or not os.path.isdir(folder):
            continue
        approved = winapi.reg_values(hive, rf"{APPROVED}\StartupFolder")
        for f in sorted(os.listdir(folder)):
            if f.lower() != "desktop.ini":
                out.append(Entry(f, os.path.join(folder, f), label,
                                 is_enabled(approved.get(f)), hive, "StartupFolder"))
    for t in winapi.powershell_json(TASKS_PS):
        out.append(Entry(t["TaskName"], t.get("Execute") or "", "Tâche planifiée",
                         t.get("State") != "Disabled", task=t["TaskPath"] + t["TaskName"]))
    return out


def set_enabled(entry, enabled):
    """Lève OSError (souvent PermissionError : droits admin requis) en cas d'échec."""
    if entry.task:
        ok, out = winapi.schtasks("/Change", "/TN", entry.task, "/ENABLE" if enabled else "/DISABLE")
        if not ok:
            raise PermissionError(out.strip())
    else:
        winapi.reg_set_binary(entry.hive, rf"{APPROVED}\{entry.approved}", entry.name,
                              bytes([2 if enabled else 3]) + bytes(11))
    entry.enabled = enabled
    log.info("Démarrage %s : %s (%s)", "activé" if enabled else "désactivé", entry.name, entry.location)
