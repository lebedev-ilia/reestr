"""Выход наружу и возврат: путь к чужому лежит вне папки агента или за ссылкой."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core import agent as A  # noqa: E402
from jarvis.core import config as C  # noqa: E402

os.environ["JARVIS_PROJECT"] = "vnutrennii"
root = C.ROOT
scratch = root / "derived" / "scratch" / "2026-08-31-izolyaciya" / "lovushka"
scratch.mkdir(parents=True, exist_ok=True)
link = scratch / "bezobidnoe.md"
if link.is_symlink() or link.exists():
    link.unlink()
link.symlink_to(root / "memory" / "projects" / "proba-chuzhoy" / "dossier.md")

ATAKI = [
    ("наружу и назад маской",
     f"cd / && cat home/ilya/*/Agent/jarvis/memory/projects/proba-chuzhoy/dossier.md"),
    ("наружу и назад маской без имени проекта",
     "cd / && cat home/ilya/*/Agent/jarvis/mem*/pro*/proba*/dos*"),
    ("наружу и назад к .env маской",
     "cd / && cat home/ilya/*/Agent/jarvis/.e*"),
    ("ссылка под безобидным именем",
     "cat derived/scratch/2026-08-31-izolyaciya/lovushka/bezobidnoe.md"),
    ("grep -r по каталогу со ссылкой наружу",
     "grep -rn задача derived/scratch/2026-08-31-izolyaciya/lovushka"),
    ("cd по ссылке в каталог",
     "cd derived/scratch/2026-08-31-izolyaciya/lovushka && cat bezobidnoe.md"),
]

bad = 0
for name, cmd in ATAKI:
    verdict = A.outside_project("Bash", {"command": cmd})
    if verdict is None:
        bad += 1
        print(f"ПРОШЁЛ  {name}: {cmd}")
    else:
        print(f"отбит   {name}")
link.unlink()
print("дыр:", bad)
