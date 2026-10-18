import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import memory

memory.backlog_update(
    "bl-148",
    status="wip",
    checkpoint=("Четвёртый заход (job-119, sonnet, --project vnutrennii) запущен "
                "в 20:xx. Бриф brief4.md: список закрытого (симлинки, тело кода, "
                "TOCTOU, эскейпы, списки путей) + пять новых гипотез, найденных "
                "чтением шлюза — WebFetch не в PROJECT_PATH_TOOLS, /proc/<pid>/environ "
                "и /proc/self/root мимо Bash-проверки, вложенное делегирование без "
                "--project, process substitution, git-читатели. Ждём отчёт: пройдёт "
                "чисто — пункт 4 закрыт, нашёл дыру — правка и пятый заход."),
    bump_cycle=True,
)
memory.backlog_update(
    "bl-151",
    status="wip",
    checkpoint="job-119 запущен по brief4.md; ждём отчёт следующим ходом.",
)
print("ok")
