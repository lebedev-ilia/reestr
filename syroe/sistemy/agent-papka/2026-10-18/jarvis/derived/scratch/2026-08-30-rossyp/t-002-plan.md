Место кода: modules/status/ (build.py — сбор данных и рендер html, cli.py —
пульт с подкомандой build, selftest.py — свой самотест на фикстурах).

- c-01: после `python -m jarvis.modules.status.cli build` проверить, что
  derived/status/index.html существует и его размер больше 0 (os.path).
- c-02: собрать список задач и статусов через `tasks.list_tasks('vnutrennii')`
  + `tasks.read_json`, сверить построчно, что каждый id и статус встречается
  внутри html-фрагмента с id="queue" (текстовый поиск подстрок).
- c-03: прочитать html-фрагмент с id="teamlead-heartbeat", убедиться, что там
  дата вида ГГГГ-ММ-ДД ЧЧ:ММ (регэксп), а не число-эпоха; сверить с
  watchdog.last_evidence('teamlead-vnutrennii'), переведённым в ту же
  читаемую форму тем же кодом форматирования — числа обязаны совпасть.
- c-04: команда `cd Agent && jarvis/.venv/bin/python -m jarvis.modules.status.cli build`,
  проверить код возврата 0 через `echo $?`.
- c-05: `grep -riE "anthropic|api.key|requests|httpx|urlopen" modules/status/*.py`
  — пусто.
- c-06: `python -m jarvis.modules.status.selftest`, код возврата 0.
- c-07: `./jarvis --selftest` из корня агента после добавления кода, код
  возврата 0.
