"""Point d'entrée unique : `pythonw -m cleanlite` (interface) ou `--auto` (nettoyage planifié)."""
import sys
from pathlib import Path

if not __package__:  # lancé par chemin (tâche planifiée, élévation admin, PyInstaller)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    from cleanlite import safety
    if "--auto" in sys.argv:
        from cleanlite import cleaner  # pas de tkinter en mode planifié
        safety.setup_log()
        cleaner.run_auto()
    else:
        from cleanlite import ui
        ui.main()


if __name__ == "__main__":
    main()
