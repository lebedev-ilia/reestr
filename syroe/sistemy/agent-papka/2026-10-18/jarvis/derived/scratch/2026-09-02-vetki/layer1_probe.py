"""Проба слоя 1 (core/agent.py: outside_zone/outside_project) — как решил бы
шлюз для роли teamlead-vnutrennii (JARVIS_ZONE=memory/projects/vnutrennii/tasks,
JARVIS_PROJECT=vnutrennii), если бы она сама прислала эти Bash-команды.
Ничего не исполняет — только зовёт функции шлюза, как это делают selftest'ы.
"""
import os
import sys

sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import agent as A  # noqa: E402

os.environ["JARVIS_ZONE"] = "memory/projects/vnutrennii/tasks"
os.environ["JARVIS_PROJECT"] = "vnutrennii"

cli = ('cd "/home/ilya/Рабочий стол/Agent/jarvis" && '
       'jarvis/.venv/bin/python -m jarvis.modules.projects.cli')

cases = [
    ("своя ветка, свой проект — branch-create",
     f'{cli} branch-create vnutrennii t-050 proba'),
    ("своя ветка, свой проект — branch-merge",
     f'{cli} branch-merge vnutrennii t-050 agent/t-050-proba --message "t-050: x"'),
    ("чужой проект в аргументе — branch-create",
     f'{cli} branch-create proba-chuzhoy t-900 vzlom'),
    ("чужой проект в аргументе — branch-merge",
     f'{cli} branch-merge proba-chuzhoy t-900 agent/t-900-vzlom --message "t-900: x"'),
    ("обход путём ../ в имени проекта",
     f'{cli} branch-merge ../proba-chuzhoy t-900 agent/t-900-vzlom --message m'),
    ("обход подменой JARVIS_PROJECT переменной в самой команде",
     f'JARVIS_PROJECT=proba-chuzhoy {cli} branch-merge proba-chuzhoy t-900 b --message m'),
    ("обход через промежуточный sh -c",
     f'sh -c \'{cli} branch-merge proba-chuzhoy t-900 b --message m\''),
    ("обычное чтение статуса своего проекта — контроль, должно быть живо",
     f'{cli} status vnutrennii'),
]

for name, cmd in cases:
    why = A.outside_zone("Bash", {"command": cmd})
    print(f"[{name}] outside_zone -> {'ОТБИТО: ' + why if why else 'пропущено дальше'}")

os.environ.pop("JARVIS_ZONE", None)
os.environ.pop("JARVIS_PROJECT", None)
print("done")
