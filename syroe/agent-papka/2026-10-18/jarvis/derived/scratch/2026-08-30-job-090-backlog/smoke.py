import sys, shutil
from pathlib import Path
sys.path.insert(0, str(Path("/home/ilya/Рабочий стол/Agent")))
from jarvis.core import config as C, memory as M

tmp = Path("/home/ilya/Рабочий стол/Agent/jarvis/derived/scratch/2026-08-30-job-090-backlog/backlog.md")
if tmp.exists():
    tmp.unlink()
C.BACKLOG = tmp

# 1. добавить без проекта — 7 колонок, как раньше
tid1 = M.backlog_add("тестовая задача без проекта", "проверка обратной совместимости", prio=5)
rows = M._backlog_rows()
assert rows[0]["project"] == "", rows
assert M.backlog_lint() == [], M.backlog_lint()
print("1 ok:", tid1, "project=", repr(rows[0]["project"]))

# 2. добавить с проектом — 8 колонок
tid2 = M.backlog_add("тестовая задача nologo", "проверка метки", prio=3, project="nologo")
rows = M._backlog_rows()
row2 = [r for r in rows if r["id"] == tid2][0]
assert row2["project"] == "nologo", row2
assert M.backlog_lint() == [], M.backlog_lint()
print("2 ok:", tid2, "project=", row2["project"])

# 3. фильтр по проекту
only_nologo = M.backlog_by_project("nologo")
assert [r["id"] for r in only_nologo] == [tid2], only_nologo
print("3 ok: backlog_by_project вернул", [r["id"] for r in only_nologo])

# 4. обновление статуса не роняет метку проекта
M.backlog_update(tid2, status="wip", checkpoint="начал")
rows = M._backlog_rows()
row2 = [r for r in rows if r["id"] == tid2][0]
assert row2["project"] == "nologo" and row2["status"] == "wip", row2
assert M.backlog_lint() == [], M.backlog_lint()
print("4 ok: статус поменялся, метка жива:", row2["status"], row2["project"])

# 5. проставить метку на старую (без метки) строку задним числом
M.backlog_set_project(tid1, "vnutrennii")
rows = M._backlog_rows()
row1 = [r for r in rows if r["id"] == tid1][0]
assert row1["project"] == "vnutrennii", row1
assert M.backlog_lint() == [], M.backlog_lint()
print("5 ok: метка проставлена задним числом:", row1["project"])

# 6. снять метку
M.backlog_set_project(tid1, "")
rows = M._backlog_rows()
row1 = [r for r in rows if r["id"] == tid1][0]
assert row1["project"] == "", row1
assert M.backlog_lint() == [], M.backlog_lint()
print("6 ok: метка снята:", repr(row1["project"]))

# 7. плохая метка отбивается
try:
    M.backlog_add("плохая метка", "зачем", project="Bad Project!")
    print("7 FAIL: должно было упасть")
    sys.exit(1)
except ValueError as e:
    print("7 ok: отбито —", e)

# 8. backlog_edit по-прежнему работает на строке с 8 колонками
M.backlog_edit(tid2, what="переименовано")
rows = M._backlog_rows()
row2 = [r for r in rows if r["id"] == tid2][0]
assert row2["what"] == "переименовано" and row2["project"] == "nologo", row2
assert M.backlog_lint() == [], M.backlog_lint()
print("8 ok: backlog_edit сохранил метку:", row2["what"], row2["project"])

print("ВСЁ ХОРОШО")

# 9. мутация: искусственно испортить ширину строки (9 колонок) — лint обязан покраснеть
bad = tmp.read_text(encoding="utf-8").rstrip("\n") + "\n| bl-999 | x | y | 1 | open | 0 | — | proj | лишнее |\n"
tmp.write_text(bad, encoding="utf-8")
problems = M.backlog_lint()
assert problems, "мутация не поймана: лint должен был покраснеть на 9 колонках"
print("9 ok: мутация поймана —", problems[-1])
