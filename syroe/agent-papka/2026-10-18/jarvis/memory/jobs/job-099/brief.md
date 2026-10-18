## Задача
Выполнить задачу t-002 проекта vnutrennii: написать код, который БЕЗ модели и без сети собирает страницу состояния derived/status/index.html, первым блоком — «очередь и загрузка» по проектам. Ты исполнитель sonnet-1 по этой задаче.

## Где искать
Прочитай ЦЕЛИКОМ перед работой, из корня агента /home/ilya/Рабочий стол/Agent/jarvis:
- memory/projects/vnutrennii/tasks/t-002/task.md — цели, критерии c-01..c-07, границы, материалы
- memory/projects/vnutrennii/tasks/t-002/progress.md — ритуал начала работы, туда же пишешь журнал
- memory/projects/README.md и modules/projects/README.md, если есть
- docstring'и modules/projects/tasks.py (status, overdue), modules/projects/summary.py (collect), modules/projects/cost.py (for_project), modules/agents/watchdog.py (last_evidence, watched_roles), core/jobs.py (all_jobs, by_status) — они уже отдают готовые словари, ничего не пересчитывай заново
- memory/rules/v-derived-tolko-vosstanovimoe.md, memory/rules/samotest-ne-pishet-v-zhivuyu-pamyat.md, memory/rules/kommit-tolko-svoi-fayly.md — соблюдать
Команды запускаются из каталога ВЫШЕ корня агента: cd "/home/ilya/Рабочий стол/Agent" && jarvis/.venv/bin/python -m jarvis.modules.projects.cli status vnutrennii (проверено, работает).

## Что можно самому
- Создать modules/status/ (build.py, cli.py, selftest.py, README.md с одной строкой владельца) или выбрать другое место — тогда назвать путь в progress.md.
- Список проектов брать перечислением подкаталогов memory/projects/ (кроме README.md), не хардкодить 'vnutrennii'.
- Писать артефакт только в derived/status/ — он должен полностью пересобираться из кода.
- Заполнить progress.md: пройти ритуал начала, записать план проверки по каждому критерию c-01..c-07 ДО работы, вести журнал, в конце — что сделано.
- Отметить план и сдачу пультом: jarvis/.venv/bin/python -m jarvis.modules.projects.cli plan vnutrennii t-002 --file <файл с планом> и в конце ... submit vnutrennii t-002 --artifact derived/status/index.html --note "..."
- Прогнать свой самотест и общий ./jarvis --selftest, коммитить ТОЛЬКО свои файлы явным списком путей (git add -- <пути>), без git add -A.

## Что только с моего согласия
Правки core/* (особенно core/selftest.py и core/agent.py), CONSTITUTION.md, MEMORY.md, перезапуск демона ВК, установка пакетов, любые сообщения Илье, удаление чужих файлов. Нужна такая правка — не делай, опиши в отчёте как блокер.

## Формат результата
Не больше 25 строк, по-русски: где лежит код, какой командой собирается страница, что показывает блок очереди, по каждому критерию c-01..c-07 — пройден или нет и чем проверен, коммит(ы), что осталось незакрытым и почему.

## Критерии приёмки
- derived/status/index.html существует и не пуст после запуска сборщика
- в html есть элемент id=queue со списком задач проекта vnutrennii и их статусами, сходится с выводом tasks.status
- в html есть элемент id=teamlead-heartbeat с читаемым временем последнего следа тимлида (не epoch-числом)
- сборка идёт одной командой кода, код возврата 0
- в коде сборщика нет обращения к модели или сети: grep по anthropic/api_key/requests/httpx/urlopen пуст
- у модуля есть свой самотест, код возврата 0
- общий ./jarvis --selftest остаётся зелёным
- запрещено: git push, слово sudo в командной строке, запись вне корня агента, удаление чего-либо из memory/log/