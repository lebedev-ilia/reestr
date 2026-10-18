import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import memory as M   # noqa: E402

print(M.backlog_update(
    "bl-147", status="done", bump_cycle=True,
    checkpoint=("Живая проверка пройдена 30.08 в 21:2x на демоне с этим кодом: "
                "отбиты все 7 способов записи (Write 101, Edit до 102, heredoc, "
                "sed -i, python из шелла, tee, cp поверх), md5 t-004/task.md не "
                "изменился; законная правка в 31 строку прошла; create с "
                "материалами в 200 строк упал TaskTooLong и мусорного t-005 не "
                "оставил; мутационная проба даёт 5 красных строк в "
                "test_task_md_cap. ob-086 закрыто. Пункт 6 готов к приёмке Ильи.")))
