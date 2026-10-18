"""Живая проверка: законные пути исполнителя проекта остаются зелёными
после починки job-169/ob-114 (обёртки перед _project_exec_danger/
_project_code_danger, ob-114 — submit с --files по докстрингу build.py).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.core.selftest import gate_chain  # noqa: E402

PROJ = dict(project="vnutrennii", zone="memory/projects/vnutrennii", role="worker")

CASES = [
    ("сдача своего кода пультом (ob-114, докстринг build.py)", True,
     ".venv/bin/python -m jarvis.modules.projects.cli submit "
     "vnutrennii t-005 --files modules/status/build.py"),
    ("сдача пультом, обёрнутая env (новая проверка)", True,
     "env .venv/bin/python -m jarvis.modules.projects.cli submit "
     "vnutrennii t-005 --files modules/status/build.py"),
    ("самотест своего кода", True,
     "python3 -m jarvis.modules.status.selftest"),
    ("самотест из венва полным путём", True,
     ".venv/bin/python -m jarvis.modules.status.selftest"),
    ("самотест, обёрнутый nice (новая проверка)", True,
     "nice -n 0 python3 -m jarvis.modules.status.selftest"),
    ("самотест после cd закрыт", False,
     "cd memory/projects/vnutrennii/worktrees/t-005 && "
     "python3 -m modules.status.selftest"),
    ("самотест с подменой PYTHONPATH закрыт", False,
     "PYTHONPATH=memory/projects/vnutrennii python3 -m "
     "jarvis.modules.status.selftest"),
    ("python -c исполнителю по-прежнему закрыт", False,
     'python3 -c "print(1)"'),
    ("простое чтение своей папки (ls) не задето", True,
     "ls memory/projects/vnutrennii/"),
    ("правка своего task.md через Write не Bash — не тестируется здесь", True,
     "echo проверка"),
]

if __name__ == "__main__":
    bad = []
    for name, expect_allow, cmd in CASES:
        verdict, why = gate_chain("Bash", {"command": cmd}, **PROJ)
        ok = (verdict == "allow") == expect_allow
        print(("ок   " if ok else "ПЛОХО"), name, "->", verdict,
              "" if ok else f"(ожидали {'allow' if expect_allow else 'deny'}: {why})")
        if not ok:
            bad.append(name)
    print()
    print("ИТОГ:", "всё ожидаемо" if not bad else f"РАСХОЖДЕНИЯ: {bad}")
