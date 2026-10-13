"""Seul module qui parle à Windows (registre, shell32, schtasks…).

Hors Windows chaque fonction renvoie un résultat neutre, pour que tout le reste
s'importe et se teste sous Linux.
"""
import csv
import json
import os
import string
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import winreg
    from ctypes import wintypes

    _HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    _PACK = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8  # shellapi.h : pshpack1 en 32 bits

    class _SHQUERYRBINFO(ctypes.Structure):
        _pack_ = _PACK
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong),
                    ("i64NumItems", ctypes.c_longlong)]

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = _PACK
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]


def _com():
    """Les API shell32 de la corbeille veulent COM initialisé sur le thread appelant (threads de l'UI)."""
    ctypes.windll.ole32.CoInitialize(None)  # S_FALSE si déjà fait : sans effet


def _run(args, encoding="oem", timeout=60):
    """Lance une commande console sans ouvrir de fenêtre ; renvoie (ok, sortie)."""
    try:
        r = subprocess.run(args, capture_output=True, encoding=encoding, errors="replace",
                           timeout=timeout, creationflags=0x08000000)  # CREATE_NO_WINDOW
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode == 0:
        return True, r.stdout or ""  # stderr à part : un avertissement casserait le JSON
    return False, (r.stdout or "") + (r.stderr or "")


def init_process():
    """Icône propre dans la barre des tâches (sinon celle de Python) et rendu net en HiDPI."""
    if not IS_WINDOWS:
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CleanLite")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def is_admin():
    return IS_WINDOWS and bool(ctypes.windll.shell32.IsUserAnAdmin())


def relaunch_as_admin():
    """Relance CleanLite élevé (invite UAC). Faux si refusé ou hors Windows."""
    if not IS_WINDOWS:
        return False
    # Chemin du script plutôt que `-m` : le dossier de travail d'un processus élevé n'est pas garanti.
    params = "" if getattr(sys, "frozen", False) else f'"{Path(__file__).with_name("__main__.py")}"'
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1) > 32


def shell_execute(exe, args=""):
    """Ouvre un exécutable comme l'Explorateur (gère l'invite UAC s'il la demande)."""
    if not IS_WINDOWS:
        raise OSError("Disponible uniquement sous Windows")
    r = ctypes.windll.shell32.ShellExecuteW(None, "open", exe, args or None, None, 1)
    if r <= 32:
        raise OSError(f"Impossible de lancer {exe} (code {r})")


def open_path(path):
    if IS_WINDOWS:
        os.startfile(path)


def recycle_bin_size():
    """(octets, éléments) dans la corbeille, tous lecteurs confondus."""
    if not IS_WINDOWS:
        return 0, 0
    _com()
    info = _SHQUERYRBINFO(cbSize=ctypes.sizeof(_SHQUERYRBINFO))
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return info.i64Size, info.i64NumItems


def empty_recycle_bin():
    if not IS_WINDOWS:
        return False
    _com()
    flags = 0x1 | 0x2 | 0x4  # SHERB_NOCONFIRMATION | NOPROGRESSUI | NOSOUND
    return ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags) == 0


def send_to_trash(path):
    """Envoie un fichier à la corbeille. Lève OSError en cas d'échec."""
    if not IS_WINDOWS:
        raise OSError("Corbeille disponible uniquement sous Windows")
    _com()
    # FOF_SILENT | NOCONFIRMATION | ALLOWUNDO | NOERRORUI | WANTNUKEWARNING :
    # si la corbeille est impossible (lecteur réseau…), Windows demande avant de supprimer pour de bon.
    op = _SHFILEOPSTRUCTW(wFunc=3, pFrom=os.path.abspath(path) + "\0",
                          fFlags=0x4 | 0x10 | 0x40 | 0x400 | 0x4000)
    r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if r or op.fAnyOperationsAborted:
        raise OSError(f"Échec de l'envoi à la corbeille (code {r}) : {path}")


def flush_dns():
    return IS_WINDOWS and _run(["ipconfig", "/flushdns"])[0]


def running_processes():
    """Noms (minuscules) des exécutables en cours, ex. {'chrome.exe'}."""
    if not IS_WINDOWS:
        return set()
    ok, out = _run(["tasklist", "/FO", "CSV", "/NH"])
    return {row[0].lower() for row in csv.reader(out.splitlines()) if row} if ok else set()


def drives():
    if not IS_WINDOWS:
        return [str(Path.home())]
    mask = ctypes.windll.kernel32.GetLogicalDrives()  # pas de « aucun disque » sur lecteur vide
    return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1]


def reg_values(hive, path):
    """{nom: valeur} d'une clé (vue 64 bits) ; {} si absente."""
    if not IS_WINDOWS:
        return {}
    try:
        with winreg.OpenKey(_HIVES[hive], path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            values = (winreg.EnumValue(k, i) for i in range(winreg.QueryInfoKey(k)[1]))
            return {name: value for name, value, _ in values}
    except OSError:
        return {}


def reg_subkeys(hive, path):
    if not IS_WINDOWS:
        return []
    try:
        with winreg.OpenKey(_HIVES[hive], path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return [winreg.EnumKey(k, i) for i in range(winreg.QueryInfoKey(k)[0])]
    except OSError:
        return []


def reg_set_binary(hive, path, name, data):
    """Écrit une valeur REG_BINARY. PermissionError sur HKLM sans droits admin."""
    if not IS_WINDOWS:
        raise OSError("Registre disponible uniquement sous Windows")
    with winreg.CreateKeyEx(_HIVES[hive], path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)


def schtasks(*args):
    """(ok, sortie) de schtasks.exe."""
    if not IS_WINDOWS:
        return False, "schtasks indisponible hors Windows"
    return _run(["schtasks", *args])


def powershell_json(script):
    """Exécute un script PowerShell et renvoie sa sortie (objets) en liste de dicts."""
    if not IS_WINDOWS:
        return []
    ok, out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                    f"ConvertTo-Json -Compress -InputObject @({script})"], encoding="utf-8")
    try:
        data = json.loads(out) if ok and out.strip() else []
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]
