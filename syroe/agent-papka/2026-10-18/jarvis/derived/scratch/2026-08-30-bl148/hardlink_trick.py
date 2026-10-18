import os, pathlib
root = pathlib.Path(__file__).resolve().parents[3]
src = root / "memory" / "projects" / "proba-chuzhoy" / "dossier.md"
dst = root / "derived" / "scratch" / "2026-08-30-bl148" / "hardlink_copy.md"
if dst.exists():
    dst.unlink()
os.link(src, dst)
print("linked", dst.stat().st_size)
