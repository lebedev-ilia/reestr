"""Самотест сводки и расхода по проекту (modules/projects/summary.py,
modules/projects/cost.py) — этап 0, задание job-089.

Всё на фикстурах во временном каталоге derived/scratch/projects-selftest —
живая память (memory/projects/, memory/jobs/, memory/log/) не трогается ни
строкой (правило samotest-ne-pishet-v-zhivuyu-pamyat). Подменяются модульные
константы, читаемые заново при каждом вызове: `tasks.PROJECTS_DIR` и
`cost.JOBS_DIR` — тот же приём, что уже применён в самом tasks.py, поэтому
summary.py специально переписан читать путь к obligations.md через
`tasks.project_dir()`, а не напрямую через core.config, иначе подмена одной
константы не докатилась бы до другой.

Результат читается по коду возврата (правило samotest-schitat-po-kodu-
vozvrata): 0 — всё зелёное, печатает «ВСЁ ХОРОШО»; иначе печатает «СЛОМАНО»
и список того, что покраснело.

    jarvis/.venv/bin/python -m jarvis.modules.projects.selftest
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

from jarvis.core import config as C
from jarvis.modules.projects import cli as CLI
from jarvis.modules.projects import cost as CO
from jarvis.modules.projects import summary as S
from jarvis.modules.projects import tasks as T

TMP = C.ROOT / "derived" / "scratch" / "projects-selftest"

BAD: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        BAD.append(name)


# --------------------------------------------------------------------------
# Фикстуры
# --------------------------------------------------------------------------

def _write_task(project: str, task_id: str, *, status: str,
                 title: str = "задача", planned_due: str = "2026-09-01",
                 criteria: list[dict] | None = None,
                 rounds: list[dict] | None = None,
                 artifacts: list[str] | None = None) -> None:
    d = T.task_dir(project, task_id)
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": task_id, "project": project, "title": title,
        "teamlead": "jarvis", "executor": "sonnet-1",
        "created": "2026-08-30", "planned_due": planned_due,
        "started": "2026-08-30T09:00:00+10:00",
        "finished": None, "accepted_at": None, "accepted_by": None,
        "status": status, "rounds": rounds or [], "artifacts": artifacts or [],
        "criteria": criteria if criteria is not None else [
            {"id": "c-01", "text": "критерий", "passes": status == "done",
             "checked_at": None, "checked_by": None}],
    }
    (d / "task.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    (d / "task.md").write_text(f"# Задача {task_id}: {title}\n", encoding="utf-8")
    (d / "progress.md").write_text("# прогресс\n", encoding="utf-8")


def _write_job(jobs_dir: Path, job_id: str, *, project: object,
               cost_usd: float | None, finished: str | None,
               turns: int = 5, sec: float = 60.0,
               status: str = "done") -> None:
    """project=None → поля "project" в job.json нет вовсе (старый формат);
    project="" или строка → поле есть."""
    d = jobs_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": job_id, "title": job_id, "status": status,
           "created": finished or "2026-08-30T09:00:00+10:00",
           "started": finished or "2026-08-30T09:00:00+10:00",
           "finished": finished, "sec": sec if finished else None,
           "cost_usd": cost_usd, "turns": turns if finished else None}
    if project is not None:
        meta["project"] = project
    (d / "job.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                encoding="utf-8")


# --------------------------------------------------------------------------
# summary.py
# --------------------------------------------------------------------------

def check_summary_empty_project() -> None:
    """Проект существует, но задач нет вовсе — семь пунктов честными нулями,
    без падения (критерий приёмки №1)."""
    project = "empty"
    T.project_dir(project).mkdir(parents=True, exist_ok=True)
    data = S.collect(project)
    check("пустой проект: сделано=0", data["сделано"]["n"] == 0)
    check("пустой проект: не сделано=0", data["не_сделано_заведено"]["n"] == 0)
    check("пустой проект: получилось пусто", data["получилось"] == [])
    check("пустой проект: не получилось пусто", data["не_получилось"] == [])
    check("пустой проект: просрочено пусто", data["просрочено"] == [])
    check("пустой проект: прогноз пуст", data["прогноз"] == [])
    check("пустой проект: вопросы пусты", data["вопросы"] == [])
    text = S.render_text(data)
    check("пустой проект: текст без markdown-таблиц", "|" not in text and "```" not in text)
    check("пустой проект: текст не пуст", len(text) > 0)


def check_summary_mixed() -> None:
    """Проект с полным набором статусов — каждый из семи пунктов проверяется
    отдельно, числа сверяются с фикстурой напрямую (мерило по всему полю)."""
    project = "mixed"
    T.project_dir(project).mkdir(parents=True, exist_ok=True)

    _write_task(project, "t-001", status="done", title="Сделанная",
               criteria=[{"id": "c-01", "text": "x", "passes": True,
                         "checked_at": "2026-08-30T10:00:00+10:00", "checked_by": "jarvis"}],
               artifacts=["derived/scratch/x.txt"])
    _write_task(project, "t-002", status="in_progress", title="В работе",
               planned_due="2026-09-10")
    _write_task(project, "t-003", status="rework", title="На доработке",
               rounds=[{"n": 1, "date": "2026-08-30T11:00:00+10:00", "by": "jarvis",
                        "points": ["переделать X"], "due": None, "note": ""}])
    _write_task(project, "t-004", status="in_progress", title="Просроченная",
               planned_due="2020-01-01")

    obl = T.project_dir(project) / "obligations.md"
    obl.write_text(
        "# Обязательства проекта — mixed\n\n"
        "- [ob-101] Нужен вердикт по X — ilya — срок 2026-09-01 — open, переносов: 0\n"
        "- [ob-102] Внутренняя задача — agent — срок 2026-09-01 — open, переносов: 0\n"
        "- [ob-103] Старый вопрос — ilya — срок 2026-08-01 — closed, переносов: 2\n",
        encoding="utf-8")

    data = S.collect(project)
    check("сделано=1 (t-001)", data["сделано"]["n"] == 1
         and data["сделано"]["задачи"][0]["id"] == "t-001")
    check("не сделано=3 (t-002,t-003,t-004)", data["не_сделано_заведено"]["n"] == 3)
    check("получилось: критерии 1/1", data["получилось"][0]["критерии"] == "1/1")
    check("не получилось: t-003, круг 1, пункт назван",
         len(data["не_получилось"]) == 1 and data["не_получилось"][0]["id"] == "t-003"
         and data["не_получилось"][0]["круг"] == 1
         and "переделать X" in data["не_получилось"][0]["пункты"])
    check("просрочено: только t-004", [x["id"] for x in data["просрочено"]] == ["t-004"])
    check("просрочено: причина заполнена", "дн." in data["просрочено"][0]["reason"])
    # сроки: t-004=2020-01-01, t-003=2026-09-01 (по умолчанию), t-002=2026-09-10
    check("прогноз: три открытые задачи, отсортированы по сроку",
         [x["id"] for x in data["прогноз"]] == ["t-004", "t-003", "t-002"])
    check("вопросы: только ilya+open, не agent и не closed",
         [q["id"] for q in data["вопросы"]] == ["ob-101"])

    text = S.render_text(data)
    for must in ("t-001", "t-002", "t-003", "t-004", "Нужен вердикт по X"):
        check(f"текст сводки содержит {must}", must in text)
    check("текст без markdown-разметки", not any(m in text for m in ("##", "**", "| ")))


def check_summary_unknown_project() -> None:
    try:
        S.collect("нет-такого-проекта-zzz")
        check("неизвестный проект: падает предсказуемо", False)
    except T.NoSuchProject:
        check("неизвестный проект: падает предсказуемо", True)


# --------------------------------------------------------------------------
# cost.py
# --------------------------------------------------------------------------

def check_cost() -> None:
    jobs_dir = TMP / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    today = CO._today_iso()

    # два задания сегодня с меткой vnutrennii
    _write_job(jobs_dir, "job-a", project="vnutrennii", cost_usd=1.5,
              finished=f"{today}T09:00:00+10:00", turns=10, sec=120.0)
    _write_job(jobs_dir, "job-b", project="vnutrennii", cost_usd=0.5,
              finished=f"{today}T10:00:00+10:00", turns=4, sec=30.0)
    # задание вчера, тоже в счёт недели
    _write_job(jobs_dir, "job-c", project="vnutrennii", cost_usd=2.0,
              finished="2020-01-01T09:00:00+10:00", turns=7, sec=90.0)
    # чужой проект — не должен попасть в счёт vnutrennii
    _write_job(jobs_dir, "job-d", project="nologo", cost_usd=9.0,
              finished=f"{today}T09:00:00+10:00")
    # без метки (поле есть, но пусто)
    _write_job(jobs_dir, "job-e", project="", cost_usd=3.0,
              finished=f"{today}T09:00:00+10:00")
    # старый формат — поля "project" нет вовсе
    _write_job(jobs_dir, "job-f", project=None, cost_usd=4.0,
              finished=f"{today}T09:00:00+10:00")
    # незавершённое задание своего проекта — цена ещё не известна
    _write_job(jobs_dir, "job-g", project="vnutrennii", cost_usd=None,
              finished=None, status="running")

    row = CO.for_project("vnutrennii", jobs_dir=jobs_dir)
    check("сутки: два задания сегодня", row["сутки"]["n"] == 2)
    check("сутки: сумма 1.5+0.5=2.0", abs(row["сутки"]["cost_usd"] - 2.0) < 1e-9)
    check("неделя: 2020 год вне недельного окна — не считается",
         row["неделя"]["n"] == 2)
    check("неделя: сумма та же (job-c вне окна)", abs(row["неделя"]["cost_usd"] - 2.0) < 1e-9)
    check("чужой проект не подмешался", row["сутки"]["cost_usd"] < 9.0)
    check("незавершённое задание своего проекта не искажает сумму",
         row["unfinished_total"] >= 1)
    check("has_labeled_jobs=True при реальных данных", row["has_labeled_jobs"] is True)

    empty_row = CO.for_project("совсем-другой-проект-без-заданий", jobs_dir=jobs_dir)
    check("проект без размеченных заданий: has_labeled_jobs=False, не 0-как-данные",
         empty_row["has_labeled_jobs"] is False)
    text = CO.render_text(empty_row)
    check("текст «без меток» не выдаёт себя за настоящий ноль",
         "0.00" not in text and "меток" not in text and "нет" in text)

    filled_text = CO.render_text(row)
    check("текст с данными печатает сутки и неделю",
         "сутки" in filled_text and "неделя" in filled_text)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def check_cli_zone() -> None:
    """Вторая половина границы bl-145: пульт сам сверяет проект с зоной роли.

    Шлюз (core/agent.py) судит только ПОДКОМАНДУ — какой проект окажется под
    правкой, он не знает. Поэтому чужой проект отбивается здесь: читать чужое
    роли можно, менять — нет. Зоны нет вовсе (это сам Jarvis) — ограничений нет.
    """
    saved = os.environ.get("JARVIS_ZONE")
    mine, alien = SimpleNamespace(project="alpha"), SimpleNamespace(project="beta")
    try:
        os.environ.pop("JARVIS_ZONE", None)
        check("без зоны пульт не ограничен",
              CLI._zone_refusal("create", mine) is None)
        check("без зоны ветки открыты",
              CLI._zone_refusal("branch-merge", SimpleNamespace()) is None)

        os.environ["JARVIS_ZONE"] = str(T.tasks_dir("alpha"))
        check("своя папка задач на запись открыта",
              CLI._zone_refusal("create", mine) is None)
        check("свой accept открыт", CLI._zone_refusal("accept", mine) is None)
        check("чужой проект на запись закрыт",
              CLI._zone_refusal("create", alien) is not None)
        check("чужой проект на чтение открыт",
              CLI._zone_refusal("status", alien) is None)
        check("check чужого проекта открыт",
              CLI._zone_refusal("check", alien) is None)
        check("ветки задач роли закрыты",
              CLI._zone_refusal("branch-merge", SimpleNamespace()) is not None)
        check("branch-list роли открыт",
              CLI._zone_refusal("branch-list", SimpleNamespace()) is None)

        # Зона на папку роли, а не на задачи проекта: писать в задачи нельзя.
        os.environ["JARVIS_ZONE"] = str(C.MEM / "agents" / "legal" / "knowledge")
        check("зона мимо задач закрывает запись",
              CLI._zone_refusal("submit", mine) is not None)
    finally:
        if saved is None:
            os.environ.pop("JARVIS_ZONE", None)
        else:
            os.environ["JARVIS_ZONE"] = saved


def check_queue_lint() -> None:
    """Очередь проекта: своё имя из документа и свой линтер (bl-142)."""
    p = "queueproj"
    T.project_dir(p).mkdir(parents=True, exist_ok=True)
    check("очередь проекта названа backlog.md",
          T.queue_path(p).name == "backlog.md")
    check("отсутствие очереди — претензия",
          any("нет очереди" in s for s in T.queue_lint(p)))
    T.queue_path(p).write_text(f"# Очередь задач — {p}\n\nпусто\n",
                               encoding="utf-8")
    check("нормальная очередь чиста", T.queue_lint(p) == [])
    T.queue_path(p).write_text(
        f"# Очередь задач — {p}\n\n| bl-142 | что-то | зачем | 3 | wip | 1 | — |\n",
        encoding="utf-8")
    check("строка главной очереди в проекте — претензия",
          any("ГЛАВНОЙ" in s for s in T.queue_lint(p)))
    T.queue_path(p).write_text(f"# Очередь задач — {p}\n", encoding="utf-8")
    (T.project_dir(p) / "queue.md").write_text("старая\n", encoding="utf-8")
    check("старый queue.md рядом — претензия",
          any("queue.md" in s for s in T.queue_lint(p)))


def check_live_queues() -> None:
    """Живые очереди на диске: главная и проектные не путаются местами."""
    check("главная очередь на месте", C.BACKLOG.exists())
    for pdir in sorted(x for x in T.PROJECTS_DIR.iterdir() if x.is_dir()):
        problems = T.queue_lint(pdir.name)
        check(f"очередь проекта {pdir.name}: {problems or 'чисто'}",
              problems == [])


def main() -> int:
    saved_projects_dir = T.PROJECTS_DIR
    saved_jobs_dir = CO.JOBS_DIR
    # До подмены путей: живые очереди читаются только на чтение.
    print("== живые очереди ==")
    check_live_queues()
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True, exist_ok=True)
    T.PROJECTS_DIR = TMP / "projects"
    CO.JOBS_DIR = TMP / "jobs"
    try:
        print("== summary: пустой проект ==")
        check_summary_empty_project()
        print("== summary: смешанные статусы ==")
        check_summary_mixed()
        print("== summary: неизвестный проект ==")
        check_summary_unknown_project()
        print("== cost ==")
        check_cost()
        print("== пульт: зона роли ==")
        check_cli_zone()
        print("== очередь проекта ==")
        check_queue_lint()
    finally:
        T.PROJECTS_DIR = saved_projects_dir
        CO.JOBS_DIR = saved_jobs_dir
        shutil.rmtree(TMP, ignore_errors=True)

    if BAD:
        print(f"\nСЛОМАНО ({len(BAD)}): {BAD}")
        return 1
    print("\nВСЁ ХОРОШО")
    return 0


if __name__ == "__main__":
    sys.exit(main())
