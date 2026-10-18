"""Живая проверка заведения задачи длиннее потолка (bl-147, пункт 6 приёмки).

Ждём: TaskTooLong и НИ ОДНОГО нового каталога в memory/projects/vnutrennii/tasks.
"""
import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")

from jarvis.modules.projects import tasks as T   # noqa: E402

TDIR = T.tasks_dir("vnutrennii")
before = sorted(p.name for p in TDIR.iterdir() if p.is_dir())

try:
    tid = T.create(
        "vnutrennii", "проба потолка при заведении",
        teamlead="jarvis", executor="sonnet", planned_due="2026-08-31",
        criteria=["[c-01] проба"],
        materials="\n".join(f"строка {i}" for i in range(1, 201)),
        actor="teamlead")
    print("ПЛОХО: задача заведена:", tid)
except T.TaskTooLong as e:
    print("отказ при заведении:", type(e).__name__, e)
except Exception as e:                      # noqa: BLE001
    print("отказ другой ошибкой:", type(e).__name__, e)

after = sorted(p.name for p in TDIR.iterdir() if p.is_dir())
print("каталоги до :", before)
print("каталоги после:", after)
print("МУСОРА НЕТ" if before == after else "ПЛОХО: остался мусор")
