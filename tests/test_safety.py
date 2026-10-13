"""Tests de sécurité, exécutables sous Linux : python3 tests/test_safety.py (ou pytest)."""
import ntpath
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleanlite import cleaner, diskscan, duplicates, programs, safety, schedule, startup  # noqa: E402


def test_whitelist_refuse_les_chemins_windows_dangereux():
    real_osp, safety.osp = safety.osp, ntpath  # sémantique de chemins Windows, même sous Linux
    try:
        roots = [r"C:\Windows\Temp", r"C:\Users\a\AppData\Local\Temp"]
        assert not safety.is_allowed(r"C:\Windows\System32", roots)
        assert not safety.is_allowed(r"C:\Windows\System32\drivers\etc\hosts", roots)
        assert not safety.is_allowed(r"C:\Windows\Temp\..\System32\kernel32.dll", roots)
        assert not safety.is_allowed(r"C:\Windows\Temp", roots)  # la racine elle-même
        assert not safety.is_allowed(r"C:\Windows\TempEvil\x.tmp", roots)  # préfixe trompeur
        assert not safety.is_allowed(r"D:\Windows\Temp\x.tmp", roots)
        assert safety.is_allowed(r"c:\WINDOWS\temp\x.tmp", roots)  # insensible à la casse
    finally:
        safety.osp = real_osp


def test_racine_corrompue_refusee():
    real_osp, safety.osp = safety.osp, ntpath
    try:
        crit = [r"C:\Users\a", r"C:\Windows", r"C:\Program Files"]
        assert not safety.is_safe_root("C:\\", crit)                      # TEMP=C:\
        assert not safety.is_safe_root("D:\\", crit)                      # racine d'un autre lecteur
        assert not safety.is_safe_root(r"C:\Users\a", crit)               # TEMP=%USERPROFILE%
        assert not safety.is_safe_root(r"C:\Users", crit)                 # contient le profil
        assert safety.is_safe_root(r"C:\Users\a\AppData\Local\Temp", crit)
        assert safety.is_safe_root(r"C:\Windows\Temp", crit)
    finally:
        safety.osp = real_osp
    cat = cleaner.Category("t", "t", "t", [(str(Path.home()), "*")])
    assert cleaner.run(cat, dry_run=True) == safety.Stats(skipped=1)  # le dossier personnel n'est jamais vidé


def test_dotdot_et_liens_sortants_refuses():
    if sys.platform == "win32":
        return  # créer des symlinks y demande des privilèges ; la logique testée est la même
    with tempfile.TemporaryDirectory() as d:
        root, out = Path(d, "autorise"), Path(d, "dehors")
        root.mkdir()
        out.mkdir()
        secret = out / "secret.txt"
        secret.write_text("ne pas supprimer")
        (root / "lien_dossier").symlink_to(out, target_is_directory=True)
        (root / "lien_fichier").symlink_to(secret)

        assert not safety.is_allowed(root / ".." / "dehors" / "secret.txt", [root])
        assert not safety.is_allowed(root / "lien_dossier" / "secret.txt", [root])
        st = safety.Stats()
        for target in (root / "lien_dossier", root / "lien_fichier", root / ".." / "dehors"):
            safety.purge(target, [root], dry_run=False, stats=st)
        assert secret.exists() and st.files == 0 and st.skipped >= 3


def _arbo(root):
    (root / "a" / "b").mkdir(parents=True)
    (root / "f1.tmp").write_bytes(b"x" * 1000)
    (root / "a" / "f2.tmp").write_bytes(b"x" * 2345)
    (root / "a" / "b" / "f3.tmp").write_bytes(b"")
    return 3345


def test_dry_run_ne_supprime_rien_et_taille_exacte():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        expected = _arbo(root)
        st = safety.Stats()
        for child in root.iterdir():
            safety.purge(child, [root], dry_run=True, stats=st)
        assert (st.bytes, st.files) == (expected, 3), st
        assert (root / "a" / "b" / "f3.tmp").exists() and (root / "f1.tmp").exists()

        st = safety.Stats()
        for child in root.iterdir():
            safety.purge(child, [root], dry_run=False, stats=st)
        assert (st.bytes, st.files, st.skipped) == (expected, 3, 0), st
        assert root.exists() and not any(root.iterdir())  # vidé, racine conservée


def test_fichier_protege_ignore_et_compte():
    if sys.platform == "win32" or os.geteuid() == 0:
        return  # chmod ne verrouille rien sous Windows ; root ignore les permissions
    with tempfile.TemporaryDirectory() as d:
        ro = Path(d, "ro")
        ro.mkdir()
        (ro / "x").write_bytes(b"12")
        ro.chmod(0o500)  # comme un fichier verrouillé : la suppression échoue
        try:
            st = safety.Stats()
            safety.purge(ro, [d], dry_run=False, stats=st)
            assert st.skipped == 1 and (ro / "x").exists()
        finally:
            ro.chmod(0o700)


def test_age_minimum():
    with tempfile.TemporaryDirectory() as d:
        new, old = Path(d, "recent.tmp"), Path(d, "vieux.tmp")
        new.write_bytes(b"1")
        old.write_bytes(b"22")
        os.utime(old, (time.time() - 2 * 86400,) * 2)
        st = safety.Stats()
        for f in (new, old):
            safety.purge(f, [d], dry_run=False, stats=st, min_age_s=86400)
        assert new.exists() and not old.exists() and st.bytes == 2


def test_nettoyeur_navigateur_et_defauts():
    with tempfile.TemporaryDirectory() as d:
        prof = Path(d, "Google", "Chrome", "User Data", "Default")
        (prof / "Cache" / "Cache_Data").mkdir(parents=True)
        (prof / "Network").mkdir()
        (prof / "Cache" / "Cache_Data" / "f_000001").write_bytes(b"x" * 500)
        (prof / "Network" / "Cookies").write_bytes(b"c" * 70)
        (prof / "Bookmarks").write_bytes(b"favoris")
        cats = {c.key: c for c in cleaner.categories({"LOCALAPPDATA": d})}
        assert cats["chrome_cache"].default and cats["temp"].default
        assert not cats["chrome_cookies"].default and not cats["chrome_history"].default
        files = []
        assert cleaner.run(cats["chrome_cache"], dry_run=True, collect=files).bytes == 500
        assert files == [(str(prof / "Cache" / "Cache_Data" / "f_000001"), 500)]  # aperçu fichier par fichier
        assert cleaner.run(cats["chrome_cookies"], dry_run=True).bytes == 70
        assert cleaner.run(cats["chrome_cache"], dry_run=False).bytes == 500
        assert (prof / "Network" / "Cookies").exists() and (prof / "Bookmarks").exists()
        assert not (prof / "Cache").exists()


def test_decodage_registre():
    assert startup.is_enabled(None) and startup.is_enabled(b"\x02" + bytes(11))
    assert startup.is_enabled(b"\x06" + bytes(11)) and not startup.is_enabled(b"\x03" + bytes(11))
    split = programs.split_command
    assert split(r'"C:\Program Files\App\unins000.exe" /SILENT') == (r"C:\Program Files\App\unins000.exe", "/SILENT")
    assert split(r"C:\Program Files\App\uninstall.exe /x") == (r"C:\Program Files\App\uninstall.exe", "/x")
    assert split("MsiExec.exe /X{1234}") == ("MsiExec.exe", "/X{1234}")
    if sys.platform != "win32":
        assert startup.list_entries({}) == [] and programs.list_programs() == []  # stubs hors Windows


def test_planification():
    assert schedule.ps_quote("C:\\Users\\O'Brien\\x") == "'C:\\Users\\O''Brien\\x'"  # apostrophe échappée
    script = schedule.enable_script()
    assert "-StartWhenAvailable" in script and "-AllowStartIfOnBatteries" in script
    assert "--auto" in script and schedule.TASK in script


def test_analyseur_et_doublons():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        expected = _arbo(root)
        tree = diskscan.scan(str(root))
        assert tree.size == expected and tree.children[0].name == "a"  # trié par taille
        (root / "a" / "copie.tmp").write_bytes(b"x" * 1000)       # = f1.tmp
        (root / "a" / "b" / "autre.tmp").write_bytes(b"y" * 1000)  # même taille, contenu différent
        os.link(root / "a" / "f2.tmp", root / "lien_physique.tmp")  # même fichier, pas un doublon
        groups = duplicates.find(str(root), dirs=[str(root / "a" / "b")])
        assert groups == [(1000, sorted([str(root / "a" / "copie.tmp"), str(root / "f1.tmp")]))], groups
        assert duplicates.is_system_path(root / "a" / "b" / "autre.tmp", [str(root / "a" / "b")])
        k = duplicates.keeper
        assert k(["/p/Copie de photo.jpg", "/p/photo.jpg"]) == "/p/photo.jpg"
        assert k(["/p/photo (1).jpg", "/p/photo - Copie.jpg", "/p/x/photo.jpg"]) == "/p/x/photo.jpg"
        assert k(["/h/Downloads/a.pdf", "/h/Documents/2020/a.pdf"]) == "/h/Documents/2020/a.pdf"
        assert k(["/h/Images/2020/a.jpg", "/h/Images/a.jpg"]) == "/h/Images/a.jpg"
        assert k(["/p/zz/a.jpg", "/p/Album (2020)/a.jpg"]) == "/p/Album (2020)/a.jpg"  # seul le nom compte
        (root / "x").mkdir()
        (root / "y").mkdir()
        new, old = root / "x" / "a.bin", root / "y" / "a.bin"
        new.write_bytes(b"1")
        old.write_bytes(b"1")
        os.utime(old, (time.time() - 86400,) * 2)
        assert k([str(new), str(old)]) == str(old)  # même profondeur : le plus ancien gagne
        try:
            duplicates.find(str(root / "a" / "b"), dirs=[str(root / "a" / "b")])
            raise AssertionError("dossier système accepté")
        except PermissionError:
            pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
