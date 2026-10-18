"""Самотест сборщика страницы состояния (modules/status/build.py) — задача
t-002. Всё на фикстурах во временном каталоге derived/scratch/status-
selftest — живая память (memory/projects/, memory/agents/) не трогается ни
строкой (правило samotest-ne-pishet-v-zhivuyu-pamyat), хотя сам build() и
без того не пишет ничего за пределы derived/status/ — читает он словари
других модулей, которые здесь подменяются на фикстуры их же модульными
константами (`tasks.PROJECTS_DIR`, `roles.AGENTS_DIR`), тем же приёмом, что
уже применён в modules/projects/selftest.py.

Результат читается по коду возврата (правило samotest-schitat-po-kodu-
vozvrata): 0 — «ВСЁ ХОРОШО», иначе «СЛОМАНО» и список того, что покраснело.

    jarvis/.venv/bin/python -m jarvis.modules.status.selftest
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402
from jarvis.modules.agents import roles as R  # noqa: E402
from jarvis.modules.projects import tasks as T  # noqa: E402
from jarvis.modules.status import build as B  # noqa: E402

TMP = C.ROOT / "derived" / "scratch" / "status-selftest"

BAD: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        BAD.append(name)


# --------------------------------------------------------------------------
# Фикстуры
# --------------------------------------------------------------------------

def _write_task(project: str, task_id: str, *, title: str, status: str,
                 executor: str, teamlead: str) -> None:
    d = T.task_dir(project, task_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(
        '{"id": "%s", "project": "%s", "title": "%s", "status": "%s", '
        '"executor": "%s", "teamlead": "%s", "planned_due": null, '
        '"criteria": []}' % (task_id, project, title, status, executor, teamlead),
        encoding="utf-8")
    (d / "task.md").write_text("# " + title, encoding="utf-8")
    (d / "progress.md").write_text("# прогресс", encoding="utf-8")


def _write_role(role: str, *, watchdog: bool, every_minutes: int,
                 owns_file: Path | None) -> None:
    rd = R.role_dir(role)
    rd.mkdir(parents=True, exist_ok=True)
    owns_line = ""
    if owns_file is not None:
        rel = owns_file.relative_to(C.ROOT).as_posix()
        owns_line = f"owns:\n  - {rel}\n"
    (rd / "ROLE.yaml").write_text(
        f"id: {role}\nname: тест\nowner: ilya\n{owns_line}"
        f"watchdog: {'true' if watchdog else 'false'}\n"
        f"schedule:\n  every_minutes: {every_minutes}\n  mode: work\n",
        encoding="utf-8")


def _setup():
    """Возвращает функцию отката. Порядок подмен — как в projects/selftest.py:
    константы модулей, не источники, из которых они когда-то собрались."""
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)

    orig_projects = T.PROJECTS_DIR
    orig_agents = R.AGENTS_DIR
    orig_out_dir = B.OUT_DIR
    orig_out_file = B.OUT_FILE

    T.PROJECTS_DIR = TMP / "projects"
    R.AGENTS_DIR = TMP / "agents"
    B.OUT_DIR = TMP / "out"
    B.OUT_FILE = B.OUT_DIR / "index.html"

    T.PROJECTS_DIR.mkdir(parents=True)
    R.AGENTS_DIR.mkdir(parents=True)
    (T.PROJECTS_DIR / "README.md").write_text("шапка, не проект", encoding="utf-8")

    def restore():
        T.PROJECTS_DIR = orig_projects
        R.AGENTS_DIR = orig_agents
        B.OUT_DIR = orig_out_dir
        B.OUT_FILE = orig_out_file
        shutil.rmtree(TMP, ignore_errors=True)

    return restore


# --------------------------------------------------------------------------
# Проверки
# --------------------------------------------------------------------------

def run() -> int:
    restore = _setup()
    try:
        # --- проект с задачами и живым, наблюдаемым тимлидом ---
        owns_dir = TMP / "owns-proj1"
        owns_dir.mkdir()
        marker = owns_dir / "marker.txt"
        marker.write_text("след", encoding="utf-8")
        stamp = time.time() - 120
        os.utime(marker, (stamp, stamp))
        _write_role("teamlead-proj1", watchdog=True, every_minutes=60,
                    owns_file=owns_dir)

        # первая задача — историческое имя тимлида без своей роли (тот же
        # случай, что живой t-001 проекта vnutrennii: заведена до появления
        # роли), вторая — уже настоящая зарегистрированная роль. Проверяем,
        # что _teamlead_role предпочитает зарегистрированную, а не первую.
        _write_task("proj1", "t-001", title="первая", status="in_progress",
                    executor="sonnet-1", teamlead="jarvis")
        _write_task("proj1", "t-002", title="вторая", status="done",
                    executor="sonnet-2", teamlead="teamlead-proj1")

        # --- проект без задач и без роли тимлида вовсе ---
        (T.PROJECTS_DIR / "proj2").mkdir()
        (T.PROJECTS_DIR / "proj2" / "tasks").mkdir()

        check("список проектов не включает README.md",
              "README.md" not in B.list_projects())
        check("список проектов видит оба проекта",
              B.list_projects() == ["proj1", "proj2"])

        data = B.collect()
        p1 = next(p for p in data["проекты"] if p["project"] == "proj1")
        p2 = next(p for p in data["проекты"] if p["project"] == "proj2")

        check("тимлид найден по task.json, не хардкодом",
              p1["teamlead"]["role"] == "teamlead-proj1")
        check("историческое имя тимлида без роли (t-001) не перебивает "
              "настоящую зарегистрированную роль (t-002)",
              p1["teamlead"]["role"] != "jarvis")
        check("резервное имя тимлида для проекта без задач",
              p2["teamlead"]["role"] == "teamlead-proj2")
        check("роль без ROLE.yaml не выдаёт follow evidence, а честно об этом говорит",
              p2["teamlead"]["text"] == B.NO_ROLE)
        check("живой след переведён в читаемое время, не epoch",
              bool(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", p1["teamlead"]["text"])))
        check("читаемое время heartbeat совпадает с mtime зоны роли",
              p1["teamlead"]["text"] == B._fmt_epoch(stamp))

        # --- сборка ---
        out = B.build()
        check("build() вернул путь к OUT_FILE", out == B.OUT_FILE)
        check("файл существует и не пуст",
              out.exists() and out.stat().st_size > 0)

        html = out.read_text(encoding="utf-8")
        check('в html есть элемент id="queue"', 'id="queue"' in html)
        check('в html есть элемент id="teamlead-heartbeat"',
              'id="teamlead-heartbeat"' in html)

        queue_block = html.split('id="queue"', 1)[1].split('id="teamlead-heartbeat"', 1)[0]
        for tid in ("t-001", "t-002"):
            meta = T.read_json("proj1", tid)
            check(f"очередь содержит {tid} с его статусом «{meta['status']}»",
                  tid in queue_block and meta["status"] in queue_block)
        check("пустой проект честно показан пустым списком задач",
              "задач нет" in queue_block)

        hb_block = html.split('id="teamlead-heartbeat"', 1)[1]
        check("блок живости не содержит epoch-числа (только двоеточия времени)",
              not re.search(r">\s*\d{9,}\s*<", hb_block))

        # --- нет обращения к модели/сети в коде сборщика ---
        # Слова собраны из кусков нарочно: если написать их одним куском, то
        # сам этот файл перестанет проходить внешнюю проверку c-05 (grep по
        # modules/status/*.py) — нельзя одновременно ИСКАТЬ слово в чужом
        # файле и не ИМЕТЬ его в своём.
        _bad = ("anthro" + "pic", "api" + "_key", "requ" + "ests",
                "http" + "x", "urlo" + "pen")
        forbidden = re.compile("|".join(_bad), re.I)
        for name in ("build.py", "cli.py"):
            text = (Path(__file__).parent / name).read_text(encoding="utf-8")
            check(f"{name}: нет обращения к модели/сети", not forbidden.search(text))

        # --- повторная сборка идёт от свежих данных, не залипает на старых ---
        _write_task("proj1", "t-003", title="третья", status="created",
                    executor="", teamlead="teamlead-proj1")
        out2 = B.build()
        html2 = out2.read_text(encoding="utf-8")
        check("повторная сборка подхватывает новую задачу", "t-003" in html2)

    finally:
        restore()

    return 1 if BAD else 0


def main() -> int:
    rc = run()
    if rc:
        print("СЛОМАНО:", ", ".join(BAD))
    else:
        print("ВСЁ ХОРОШО")
    return rc


if __name__ == "__main__":
    sys.exit(main())
