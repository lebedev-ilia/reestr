"""Разовая проверка новых веток изоляции по проекту (bl-148, после job-119)."""
import os
import sys

sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
os.environ["JARVIS_PROJECT"] = "vnutrennii"
from jarvis.core import agent as A  # noqa: E402

CASES = [
    # (инструмент, вход, ожидаем отказ?)
    ("Bash", {"command": "cat /proc/1513801/environ"}, True),
    ("Bash", {"command": "cat /proc/self/environ"}, True),
    ("Bash", {"command": "grep -n 'os.environ' modules/jobs/cli.py"}, False),
    ("Bash", {"command": "cat .env"}, True),
    ("Bash", {"command": "cat memory/projects/vnutrennii/.env"}, False),
    ("Bash", {"command": "git log --all -p -S'SEKRET'"}, True),
    ("Bash", {"command": "git grep SEKRET"}, True),
    ("Bash", {"command": "git show"}, True),
    ("Bash", {"command": "git diff"}, True),
    ("Bash", {"command": "git status"}, False),
    ("Bash", {"command": "git add memory/projects/vnutrennii/tasks && git commit -m 'x'"}, False),
    ("Bash", {"command": "git log --oneline -5"}, False),
    ("Bash", {"command": "git show a728f89:core/agent.py"}, False),
    ("Bash", {"command": "git diff modules/projects/tasks.py"}, False),
    ("Read", {"file_path": "/proc/1513801/environ"}, True),
    ("Read", {"file_path": "memory/projects/vnutrennii/dossier.md"}, False),
    ("Glob", {"pattern": "memory/projects/*/dossier.md"}, True),
    ("Glob", {"pattern": "memory/projects/proba-chuzhoy/**"}, True),
    ("Glob", {"pattern": "**/.env"}, True),
    ("Glob", {"pattern": "memory/projects/vnutrennii/**/*.md"}, False),
    ("Glob", {"pattern": "modules/**/*.py"}, False),
]

bad = 0
for tool, inp, want_deny in CASES:
    got = A.outside_project(tool, inp)
    ok = bool(got) == want_deny
    if not ok:
        bad += 1
    mark = "OK " if ok else "ХУЖЕ"
    short = (str(inp)[:60]).replace("\n", " ")
    print(f"{mark} {tool} {short} -> {'отказ' if got else 'пропуск'}")
print(f"\nрасхождений: {bad} из {len(CASES)}")
sys.exit(1 if bad else 0)
