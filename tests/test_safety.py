"""Tests de sécurité, exécutables sous Linux : python3 tests/test_safety.py (ou pytest)."""
import ntpath
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleanlite import cleaner, safety  # noqa: E402


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


def test_dotdot_et_liens_sortants_refuses():
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
    if os.geteuid() == 0:
        return  # root ignore les permissions
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
        assert cleaner.run(cats["chrome_cache"], dry_run=True).bytes == 500
        assert cleaner.run(cats["chrome_cookies"], dry_run=True).bytes == 70
        assert cleaner.run(cats["chrome_cache"], dry_run=False).bytes == 500
        assert (prof / "Network" / "Cookies").exists() and (prof / "Bookmarks").exists()
        assert not (prof / "Cache").exists()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
