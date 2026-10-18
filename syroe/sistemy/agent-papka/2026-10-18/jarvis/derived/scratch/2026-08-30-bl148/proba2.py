import os, sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
os.environ["JARVIS_PROJECT"] = "vnutrennii"
from jarvis.core import agent as A

cases = [
    ("свой проект без слэша", "grep -rn метка memory/projects/vnutrennii"),
    ("свой проект со слэшем", "grep -rn метка memory/projects/vnutrennii/"),
    ("свой проект, файл", "cat memory/projects/vnutrennii/README.md"),
    ("свой проект, ls", "ls memory/projects/vnutrennii/tasks"),
    ("свой tasks грепом", "grep -rn c-01 memory/projects/vnutrennii/tasks"),
    ("скрипт с чужим путём", "python3 derived/scratch/2026-08-30-bl148/x.py"),
    ("perl с разворотом", "perl -e 'print reverse(\"x\")'"),
    ("свой скрипт чистый", "python3 derived/scratch/2026-08-30-bl148/ok.py"),
    ("питон -m пульта", "jarvis/.venv/bin/python -m jarvis.modules.projects.cli status vnutrennii"),
    ("обычный самотест модуля", "python3 -m jarvis.modules.projects.selftest"),
]
root = "/home/ilya/Рабочий стол/Agent/jarvis/derived/scratch/2026-08-30-bl148"
os.makedirs(root, exist_ok=True)
with open(root + "/x.py", "w") as f:
    f.write("print(open('memory/projects/proba-chuzhoy/dossier.md').read())\n")
with open(root + "/ok.py", "w") as f:
    f.write("print('ничего запретного, считаю до трёх')\n")

for name, cmd in cases:
    print(f"{name:28} -> {A.outside_project('Bash', {'command': cmd}) or 'ПРОШЛО'}"[:190])
