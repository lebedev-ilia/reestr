import sys
sys.path.insert(0, "/home/ilya/Рабочий стол/Agent")
from jarvis.core import memory as M

print(M.backlog_update(
    "bl-151",
    status="wip",
    checkpoint=(
        "НАХОДКА: заходы 1-5 шли БЕЗ JARVIS_PROJECT — mcp-инструмент delegate "
        "не умел передавать project, хотя jobs.submit его принимает. job-120 снят "
        "как недействительный (error). Правка core/tools.py (коммит 2b44748): у "
        "delegate необязательное поле project, схема переведена на явный JSON "
        "Schema; запись в core_changes есть. Заход 6 = job-122 (sonnet, "
        "project=vnutrennii, проверено по /proc/1566681/environ), бриф — "
        "derived/scratch/2026-08-30-bl148/brief6.md, в нём добавлена гипотеза "
        "«delegate с ЧУЖИМ проектом». Приманке proba-chuzhoy дописан backlog.md: "
        "без него краснел блок самотеста «СВОДКА, РАСХОД И ОЧЕРЕДЬ ПРОЕКТА». "
        "ДАЛЬШЕ: дождаться job-122, разобрать отчёт; перед вердиктом прогнать "
        "полный ./jarvis --selftest с честным кодом возврата (код брать без пайпа "
        "в tail) и убедиться, что единственный красный блок ушёл. Заголовки "
        "разделов брифа не терпят хвоста в скобках — «# Формат результата (до 30 "
        "строк)» проверка не видит, отбивает как тощий бриф."
    ),
    bump_cycle=True))
