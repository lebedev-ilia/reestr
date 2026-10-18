import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import memory as M  # noqa: E402

M.backlog_update(
    "bl-151",
    status="open",
    checkpoint=(
        "job-119 отработал (401 с, 19 попыток): дыра найдена — cat "
        "/proc/<pid демона>/environ отдал корневой .env целиком; плюс git "
        "grep/log -p -S мимо гейта, шаблон Glob не проверялся, ложный отказ "
        "на os.environ. Всё закрыто коммитом fef1b28 (agent.py, jobs.py, "
        "+15 проверок в selftest, самотест зелёный). Дальше: ПЯТЫЙ заход "
        "взломщика по brief5 (список закрытого + гипотезы: /dev/fd, "
        "ptrace/gdb, ss/lsof, вложенное задание, WebSearch как канал наружу). "
        "bl-148 остаётся открытым, пока заход не пройдёт чисто."
    ),
    bump_cycle=True,
)
print(M.backlog_row("bl-151") if hasattr(M, "backlog_row") else "обновлено")
