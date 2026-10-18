# modules/status — страница состояния

Сборщик `derived/status/index.html` без модели и без сети: читает готовые
словари `modules/projects/tasks` и `modules/agents/watchdog`, ничего не
пересчитывает заново. Первый (пока единственный) блок — «очередь и
загрузка» по каждому проекту (задачи и статусы, кто занят, живость
тимлида). Владелец: Jarvis (задача t-002 проекта vnutrennii).

```bash
cd "…/Agent"
jarvis/.venv/bin/python -m jarvis.modules.status.cli build
jarvis/.venv/bin/python -m jarvis.modules.status.selftest
```
