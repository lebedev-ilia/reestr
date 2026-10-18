"""Живая проверка пункта 4 приёмки этапа 0: изоляция по проекту.

Прогон с подставной зоной и подставным проектом — решения шлюза читаются
напрямую. Запуск из каталога выше корня агента:

    jarvis/.venv/bin/python jarvis/derived/scratch/2026-08-30-etap0/probe_project.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

os.environ["JARVIS_PROJECT"] = "alpha"

from jarvis.core import agent as A  # noqa: E402

BAD = []


def must_stop(tool, inp, name):
    why = A.outside_project(tool, inp)
    ok = why is not None
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}: {(why or 'пропущено')[:70]}")
    if not ok:
        BAD.append(name)


def must_pass(tool, inp, name):
    why = A.outside_project(tool, inp)
    ok = why is None
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}: {(why or 'пропущено')[:70]}")
    if not ok:
        BAD.append(name)


print("== чужой проект закрыт ==")
must_stop("Read", {"file_path": "memory/projects/beta/dossier.md"}, "Read чужого досье")
must_stop("Write", {"file_path": "memory/projects/beta/backlog.md"}, "Write в чужую очередь")
must_stop("Edit", {"file_path": "memory/projects/beta/tasks/t-001/task.md"}, "Edit чужой задачи")
must_stop("Grep", {"path": "memory/projects/beta"}, "Grep по чужому проекту")
must_stop("Glob", {"path": "memory/projects/beta"}, "Glob по чужому проекту")
must_stop("Bash", {"command": "cat memory/projects/beta/decisions.md"}, "cat чужого решения")
must_stop("Read", {"file_path": "memory/projects/alpha/../beta/.env"}, "обход через ..")

print("== чужие и общие секреты закрыты ==")
must_stop("Read", {"file_path": "memory/projects/beta/.env"}, "чужой .env")
must_stop("Read", {"file_path": ".env"}, "корневой .env")
must_stop("Bash", {"command": "cat .env"}, ".env через шелл")

print("== своё открыто ==")
must_pass("Read", {"file_path": "memory/projects/alpha/dossier.md"}, "своё досье")
must_pass("Write", {"file_path": "memory/projects/alpha/backlog.md"}, "своя очередь")
must_pass("Read", {"file_path": "memory/projects/alpha/.env"}, "свой .env")
must_pass("Bash", {"command": "ls memory/projects/alpha/tasks"}, "свои задачи через шелл")
must_pass("Read", {"file_path": "memory/now.md"}, "общая память вне проектов")

print("== без метки проекта ограничений нет ==")
os.environ.pop("JARVIS_PROJECT", None)
must_pass("Read", {"file_path": "memory/projects/beta/.env"}, "сам Jarvis читает всё")

print("\nСЛОМАНО:" if BAD else "\nВСЁ ХОРОШО")
for b in BAD:
    print(" -", b)
sys.exit(1 if BAD else 0)
