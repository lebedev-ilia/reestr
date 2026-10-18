# Папка Agent из Загрузок → реестр (2026-10-18)

- файлов скопировано: **1986** (уникальных по содержимому), байт: **62237924**
- дубликатов схлопнуто: **1811** (в основном `worktrees/`)
- файлов с редакцией секретов: **9**
- в карантине: **13**
- `.exp` и двоичные файлы не переносятся — в описи.

## Что внутри

`jarvis/` — код и память прошлой системы (memory/log, facts, rules, procedures, jobs),
верхний уровень — планы и приёмка этапа 0.

## Редакция секретов

- `jarvis/core/selftest.py` — пароль: 1
- `jarvis/derived/scratch/2026-08-30-bl148/out_bash.txt` — пароль: 1, ключ API: 1
- `jarvis/derived/scratch/2026-08-30-bl148/read_trick_result.txt` — ключ API: 1
- `jarvis/derived/scratch/2026-08-30-etap0/probe_gate-out.txt` — пароль: 1
- `jarvis/memory/log/2026-08-24.jsonl` — пароль: 7
- `jarvis/memory/log/2026-09-01.jsonl` — пароль: 2
- `jarvis/modules/export/selftest.py` — пароль: 1
- `jarvis/worktrees/agent__t-003-karta-diskov/core/selftest.py` — пароль: 1
- `jarvis/worktrees/agent__t-005-status-tasks-cost/core/selftest.py` — пароль: 1

## Ограничения

- Окружения, кэши, `.git`, `site-packages`, `__pycache__`, `.so/.pyc` не переносятся (опись).
- Огромные каталоги неперенесённого описаны сводкой по каталогам и крупным файлам.
