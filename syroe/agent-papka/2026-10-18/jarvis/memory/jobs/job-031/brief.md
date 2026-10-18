Задача: закрыть дыру в шлюзе разрешений — изменяющий сетевой запрос через переименованный модуль (import requests as req; req.post(...)) проходит без ask.

Где искать: core/agent.py, регулярка NET_PY_WRITE (около строки 1045) и место её вызова в _net_change (writes = NET_PY_WRITE.search(text)). Формулировка задачи — строка bl-111 в memory/backlog.md.

ПЕРЕД РАБОТОЙ прочитай целиком:
- memory/procedures/pravka-yadra.md (это правка ядра, порядок обязателен)
- memory/facts/granicy-razdela-8-chem-zashchishcheny.md (пункт 4)
- memory/rules/granicu-proveryaet-kod.md
- memory/rules/samotest-ne-pishet-v-zhivuyu-pamyat.md
- memory/rules/odnorazovye-proverki-v-derived.md

Что сделать:
1. Собрать из текста команды имена, привязанные к сетевым модулям: import requests|httpx|aiohttp|urllib as ALIAS; from requests|httpx|aiohttp import ИМЕНА; X = requests.Session() и X = httpx.Client(). Считать изменяющим вызов через любое из этих имён (.post/.put/.patch/.delete/.request с методом), а для from-import — и голый вызов post(...)/put(...)/delete(...).
2. Не сломать существующее: всё, что ловилось раньше, ловится и теперь; чтение (GET, -G, urlopen без data) не трогать.
3. Ложных ask быть не должно: локальный db.delete(...) без сетевого импорта, слова post/delete в тексте, который просто записывается в файл.

Критерии приёмки:
- случаи обеих сторон добавлены в core/selftest.py рядом с проверками шлюза (test_gate): минимум четыре ловящих (import as, from import, Session(), голый post) и минимум три зелёных, которые не должны давать ask;
- ./jarvis --selftest из корня агента проходит целиком, ноль красного;
- новая логика прогнана по живому логу команд за 24-26.08: новых срабатываний либо ноль, либо каждое названо поимённо и признано верным;
- правка закоммичена (git add + git commit, без push) и в том же ходе записана строкой в memory/core_changes.md: что, зачем, файлы, коммит, как откатить, чем рискуем. Правка без этой записи откатывается;
- чекпоинт в очереди проставлен через core.memory.backlog_update('bl-111', status=..., checkpoint=...).

Формат результата: отчёт по-русски, не длиннее 12 строк, обычным текстом. 1) что изменил в core/agent.py одной-двумя фразами; 2) какие случаи добавил в самотест и сколько их; 3) итог прогона самотеста числом проверок; 4) сколько срабатываний на живом логе и верны ли они; 5) хеш коммита; 6) чем рискуем и как откатить.

Что можно самому: правку core/agent.py и core/selftest.py, прогон самотеста, коммит, запись в core_changes.md, чекпоинт в очереди, одноразовые скрипты в derived/scratch/.

Что только с моего согласия: перезапуск демона ВК, любые сообщения Илье, правка конституции и текста раздела 8, установка пакетов, права root, удаление чужих файлов.

Запрещено: git push, слово из четырёх букв s-u-d-o в командной строке, запись вне папки агента, удаление чего-либо из memory/log/, правка core/memory.py и modules/outbox/** — они принадлежат другим. Файлы твои: core/agent.py, core/selftest.py, memory/core_changes.md, derived/scratch/**.