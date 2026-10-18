import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from jarvis.core import memory as M  # noqa: E402

print(M.backlog_update(
    "bl-157", status="done", bump_cycle=True,
    checkpoint=("Готово: continue_maxed в core/jobs.py — оборванный по потолку "
                "заход заводит продолжение (потолок 35, глубина 2, на входе "
                "результат первого захода), вызов в modules/jobs/run.py по "
                "hit_max_turns, обрыв назван в докладе, совет дробить теперь "
                "возвращается из delegate ставящему. 20 проверок в самотесте, "
                "RC=0, коммит f4a1304, строка в core_changes. На живых "
                "заданиях заработает после перезапуска демона (ob-097).")))
