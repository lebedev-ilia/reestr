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

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402
from jarvis.modules.agents import roles as R  # noqa: E402
from jarvis.modules.projects import cost as CO  # noqa: E402
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
                 executor: str, teamlead: str, rounds: list | None = None) -> None:
    d = T.task_dir(project, task_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": task_id, "project": project, "title": title,
            "status": status, "executor": executor, "teamlead": teamlead,
            "planned_due": None, "criteria": [], "rounds": rounds or []}
    (d / "task.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (d / "task.md").write_text("# " + title, encoding="utf-8")
    (d / "progress.md").write_text("# прогресс", encoding="utf-8")


def _write_job(job_id: str, *, project: str | None, cost_usd: float | None,
               finished: str | None, turns: int = 1, sec: float = 1.0,
               status: str = "done") -> None:
    """Фикстура job.json для проверки блока cost (t-005). `project=None`
    значит «поле совсем отсутствует» (старый формат) — не путать с
    project="" («поле есть, но пусто»), см. докстринг modules/projects/cost.py."""
    d = CO.JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": job_id, "cost_usd": cost_usd, "finished": finished,
            "turns": turns, "sec": sec, "status": status}
    if project is not None:
        meta["project"] = project
    (d / "job.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


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
    orig_jobs_dir = CO.JOBS_DIR

    T.PROJECTS_DIR = TMP / "projects"
    R.AGENTS_DIR = TMP / "agents"
    B.OUT_DIR = TMP / "out"
    B.OUT_FILE = B.OUT_DIR / "index.html"
    CO.JOBS_DIR = TMP / "jobs"

    T.PROJECTS_DIR.mkdir(parents=True)
    R.AGENTS_DIR.mkdir(parents=True)
    CO.JOBS_DIR.mkdir(parents=True)
    (T.PROJECTS_DIR / "README.md").write_text("шапка, не проект", encoding="utf-8")

    def restore():
        T.PROJECTS_DIR = orig_projects
        R.AGENTS_DIR = orig_agents
        B.OUT_DIR = orig_out_dir
        B.OUT_FILE = orig_out_file
        CO.JOBS_DIR = orig_jobs_dir
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
        # t-002 с одним кругом доработки — проверка, что номер круга
        # (len(rounds)) в tasks-detail считается буквально, не нулём.
        _write_task("proj1", "t-002", title="вторая", status="done",
                    executor="sonnet-2", teamlead="teamlead-proj1",
                    rounds=[{"n": 1, "date": "2026-08-30T10:00:00+10:00",
                             "by": "teamlead-proj1", "points": ["чинить"],
                             "due": None, "note": ""}])

        # --- проект без задач и без роли тимлида вовсе ---
        (T.PROJECTS_DIR / "proj2").mkdir()
        (T.PROJECTS_DIR / "proj2" / "tasks").mkdir()

        # --- задания для блока cost: proj1 размечен и с сутками, и с
        # заданием вне недельного окна, и с незавершённым; proj2 —
        # ни одного размеченного (честное «без метки», не 0) ---
        now = datetime.now(ZoneInfo(C.TZ))
        _write_job("j-001", project="proj1", cost_usd=0.5,
                    finished=now.isoformat())
        _write_job("j-002", project="proj1", cost_usd=1.25,
                    finished=(now - timedelta(days=10)).isoformat())
        _write_job("j-003", project="proj1", cost_usd=None, finished=None,
                    status="running")

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
        if 'id="tasks-detail"' in hb_block:
            hb_block = hb_block.split('id="tasks-detail"', 1)[0]
        check("блок живости не содержит epoch-числа (только двоеточия времени)",
              not re.search(r">\s*\d{9,}\s*<", hb_block))

        # --- t-005: задачи подробно (номер круга доработки) ---
        detail_direct = B.project_tasks_detail("proj1")
        t002_detail = next(t for t in detail_direct if t["id"] == "t-002")
        t001_detail = next(t for t in detail_direct if t["id"] == "t-001")
        check("project_tasks_detail: круг доработки — буквально len(rounds), не текст",
              t002_detail["round"] == 1)
        check("project_tasks_detail: задача без кругов — круг 0, не «не начат»",
              t001_detail["round"] == 0)

        check('в html есть элемент id="tasks-detail"', 'id="tasks-detail"' in html)
        check('в html есть элемент id="cost"', 'id="cost"' in html)

        detail_block = html.split('id="tasks-detail"', 1)[1].split('id="cost"', 1)[0]
        check("tasks-detail содержит t-002 с кругом доработки 1",
              'data-task-id="t-002"' in detail_block and 'data-round="1"' in detail_block)
        check("tasks-detail содержит t-001 с кругом доработки 0",
              'data-task-id="t-001"' in detail_block and 'data-round="0"' in detail_block)
        check("tasks-detail показывает title/executor задачи",
              "вторая" in detail_block and "sonnet-2" in detail_block)
        check("tasks-detail: пустой проект (proj2) честно показан «задач нет»",
              "задач нет" in detail_block)

        # --- t-005: расход по проекту (сутки/неделя, честное «без метки») ---
        cost_block = html.split('id="cost"', 1)[1]
        cost_p1 = cost_block.split('data-project="proj1"', 1)[1].split("</article>", 1)[0]
        cost_p2 = cost_block.split('data-project="proj2"', 1)[1].split("</article>", 1)[0]
        check("cost: proj1 (размечен) показывает сумму суток $0.50, не голый 0",
              "$0.50" in cost_p1)
        check("cost: проект вне недели (10 дней назад) не попал в сумму суток",
              "$0.50" in cost_p1 and "$1.75" not in cost_p1)
        check("cost: proj1 честно показывает незавершённое задание, не молчит",
              "не завершено" in cost_p1)
        check("cost: proj2 (ни одного размеченного задания) — «без метки», не 0",
              "без метки" in cost_p2 and "$0.00" not in cost_p2)

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
