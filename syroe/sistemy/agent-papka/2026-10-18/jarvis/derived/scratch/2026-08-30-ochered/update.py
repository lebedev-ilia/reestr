import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import memory as M

M.backlog_update("bl-147", status="wip", checkpoint=(
    "Код готов и зелёный: шлюз считает строки до записи, Write/Edit/heredoc/"
    "sed отбиты, самотест test_task_md_cap. Коммит a728f89. Осталось только "
    "живое подтверждение после перезапуска демона ВК (ob-086)."))
M.backlog_update("bl-149", status="done", checkpoint=(
    "Живой прогон t-004: ветка agent/t-004-proba-vetok, коммит 8c7bcf9 с id "
    "задачи, слияние тимлидом fa9f44c, отказ исполнителю на merge проверен. "
    "branch-list починен (81c47fc). Осталась дыра: роли с зоной вызвать "
    "branch-create/merge не могут — вопрос Илье, заведён отдельно."))
M.backlog_update("bl-150", status="done", checkpoint=(
    "Готово: park_unfinished в core/agent.py, вызов в turn(), 9 проверок в "
    "самотесте хода, очередь в самотесте подменяется. Коммит eeb2e42."))
M.backlog_update("bl-148", status="wip", checkpoint=(
    "Первый живой заход (job-115) обошёл изоляцию симлинком и достал корневой "
    ".env — дыра закрыта резолвом путей и запретом ln (ffbdabd). Идёт второй "
    "заход проверки job-116."))
for r in M._backlog_rows():
    if r["id"] in ("bl-147", "bl-148", "bl-149", "bl-150"):
        print(r["id"], r["status"])
