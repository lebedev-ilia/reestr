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
import subprocess
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
    правкой, он не знает. Поэтому чужой проект отбивается здесь, и с 30.08
    целиком: и запись, и чтение (пункт 4 приёмки этапа 0). Зоны нет вовсе
    (это сам Jarvis) — ограничений нет.
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
        check("чужой проект на чтение тоже закрыт",
              CLI._zone_refusal("status", alien) is not None)
        check("check чужого проекта закрыт",
              CLI._zone_refusal("check", alien) is not None)
        check("сводка чужого проекта закрыта",
              CLI._zone_refusal("summary", alien) is not None)
        check("расход чужого проекта закрыт",
              CLI._zone_refusal("cost", alien) is not None)
        check("своё чтение при этом открыто",
              CLI._zone_refusal("status", mine) is None
              and CLI._zone_refusal("summary", mine) is None)
        # Зона на папку роли целиком (memory/agents/<роль>) — своего проекта
        # у такой роли нет вовсе, и чужие проекты ей закрыты все до одного.
        os.environ["JARVIS_ZONE"] = str(T.project_dir("alpha"))
        check("зона на папку проекта тоже считается своей",
              CLI._zone_refusal("status", mine) is None)
        os.environ["JARVIS_ZONE"] = str(T.tasks_dir("alpha"))
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


SEVEN = ("Сделано задач:", "Не сделано, но заведено:", "Что получилось",
         "Что не получилось", "Просрочено", "Прогноз", "Вопросы к Илье")


def check_summary_without_model() -> None:
    """Пункт 8 приёмки этапа 0: сводка собирается кодом.

    «При недоступной модели render_text() всё равно выдаёт семь пунктов
    раздела 7. Проверяется без сети.» Сеть режется на время прогона по-живому
    — подменой socket.socket на бросающий: если внутри сборки притаился поход
    к модели или в интернет, проверка упадёт, а не притворится зелёной. Так же
    ловится и будущая правка, которая «на минуточку» позовёт haiku.
    """
    import socket
    project = "nomodel"
    T.project_dir(project).mkdir(parents=True, exist_ok=True)
    _write_task(project, "t-001", status="done", title="Сделанная",
                artifacts=["derived/scratch/x.txt"])
    _write_task(project, "t-002", status="in_progress", title="В работе",
                planned_due="2020-01-01")

    saved = socket.socket

    class NoNet(socket.socket):
        def __init__(self, *a, **k):
            raise OSError("сети нет — так и задумано в проверке пункта 8")

    socket.socket = NoNet
    try:
        data = S.collect(project)
        text = S.render_text(data)
    finally:
        socket.socket = saved

    for must in SEVEN:
        check(f"без сети сводка содержит пункт «{must}»", must in text)
    check("без сети числа настоящие, а не нули",
          "Сделано задач: 1" in text and "Не сделано, но заведено: 1" in text)
    check("без сети просрочка названа", "t-002" in text)


def check_accept_blocked_by_red() -> None:
    """Пункт 5 приёмки этапа 0: красное блокирует «Выполнено».

    Проверяется поведением, а не чтением кода: самотест подменяется на
    заведомо красный и обратно, задача при этом одна и та же. Подменяется
    именно `T.selftest_state`, потому что живой прогон изнутри самотеста
    означал бы самотест внутри самотеста.
    """
    p = "acceptred"
    art = TMP / "artefakt.md"
    art.write_text("готовая работа\n", encoding="utf-8")
    T.project_dir(p).mkdir(parents=True, exist_ok=True)
    _write_task(p, "t-001", status="submitted", artifacts=[str(art)])

    saved = T.selftest_state
    try:
        # (1) самотест красный — «Выполнено» не ставится, статус не меняется.
        T.selftest_state = lambda: (1, "СЛОМАНО (1): ['нарочно']")
        red = None
        try:
            T.accept(p, "t-001", verdict="done", results={"c-01": True},
                     checked_by="тимлид-полигон", actor="teamlead")
        except T.SelftestRed as e:
            red = e
        check("красный самотест не даёт закрыть задачу", red is not None)
        check("в отказе видно, что именно сломано",
              red is not None and "нарочно" in str(red))
        check("задача осталась несданной, а не тихо закрылась",
              T.read_json(p, "t-001")["status"] != "done")
        check("проверенные критерии при отказе не потеряны",
              T.read_json(p, "t-001")["criteria"][0]["passes"] is True)

        # (2) при красном самотесте вернуть на доработку по-прежнему можно —
        # иначе тимлид заперт: ни принять, ни вернуть.
        T.accept(p, "t-001", verdict="rework", rework_points=["переделать"],
                 checked_by="тимлид-полигон", actor="teamlead")
        check("вернуть на доработку красный самотест не мешает",
              T.read_json(p, "t-001")["status"] == "rework")

        # (3) машинная проверка задачи красная (артефакта нет) — тот же отказ,
        # даже когда самотест зелёный.
        _write_task(p, "t-002", status="submitted", artifacts=[])
        T.selftest_state = lambda: (0, "")
        checks_red = None
        try:
            T.accept(p, "t-002", verdict="done", results={"c-01": True},
                     checked_by="тимлид-полигон", actor="teamlead")
        except T.ChecksRed as e:
            checks_red = e
        check("красная машинная проверка задачи не даёт закрыть её",
              checks_red is not None)

        # (4) всё зелёное — задача закрывается как раньше.
        _write_task(p, "t-003", status="submitted", artifacts=[str(art)])
        T.accept(p, "t-003", verdict="done", results={"c-01": True},
                 checked_by="тимлид-полигон", actor="teamlead")
        check("на зелёном приёмка работает как раньше",
              T.read_json(p, "t-003")["status"] == "done")
    finally:
        T.selftest_state = saved


def check_selftest_state_skips_inside() -> None:
    """Внутри самотеста живой прогон не запускается — иначе рекурсия."""
    check("в режиме самотеста прогон пропускается, а не зовётся",
          T.selftest_state() == (0, "прогон пропущен: мы внутри самотеста")
          if C.SELFTEST else True)


def check_branches() -> None:
    """Пункт 10 приёмки этапа 0: ветки и коммиты по разделу 14.

    Живой прогон на подставном репозитории в derived — в самотест перенесён
    с обкатки 30.08 (ob-074), на которой вскрылось, что раздел 14 не работал
    вовсе: `git branch --list` печатает `+` перед веткой, занятой рабочим
    деревом, старый разбор плюс не снимал, и слияние не находило собственную
    ветку. Проверок на ветки не было ни одной — отсюда и жил баг.
    """
    from . import branches as B

    repo = TMP / "branchrepo"
    repo.mkdir(parents=True, exist_ok=True)

    def git(*args, cwd=repo):
        return subprocess.run(["git", *args], cwd=str(cwd),
                              capture_output=True, text=True, timeout=30)

    git("init", "-q", "-b", "master")
    git("config", "user.email", "selftest@jarvis.local")
    git("config", "user.name", "selftest")
    (repo / "a.txt").write_text("раз\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "первый")

    info = B.create_task_branch(repo, "t-777", "проба веток")
    check("ветка задачи названа agent/<id>-<коротко>",
          info["branch"] == "agent/t-777-proba-vetok")
    check("рабочий каталог ветки лежит внутри репозитория",
          Path(info["path"]).resolve().is_relative_to(repo.resolve()))
    check("ветка, занятая рабочим деревом, видна в списке",
          info["branch"] in B.list_task_branches(repo))

    wt = Path(info["path"])
    (wt / "b.txt").write_text("два\n", encoding="utf-8")
    git("add", "b.txt", cwd=wt)
    git("commit", "-q", "-m", "t-777: работа исполнителя", cwd=wt)
    check("основная ветка от работы в ветке не сдвинулась",
          not (repo / "b.txt").exists())

    try:
        B.merge_task_branch(repo, "t-777", info["branch"],
                            message="t-777: сливаю сам", actor="executor")
        check("исполнитель не сливает ветку", False)
    except B.PermissionRole:
        check("исполнитель не сливает ветку", True)
    try:
        B.merge_task_branch(repo, "t-777", info["branch"],
                            message="мелкие правки", actor="teamlead")
        check("сообщение слияния без id задачи отбито", False)
    except B.BranchMessageMissingTaskId:
        check("сообщение слияния без id задачи отбито", True)

    B.merge_task_branch(repo, "t-777", info["branch"],
                        message="t-777: принято тимлидом", actor="teamlead")
    check("тимлид слил ветку, работа приехала в основную",
          (repo / "b.txt").exists())
    check("id задачи есть в сообщении коммита слияния",
          "t-777" in git("log", "--oneline", "-1").stdout)
    check("рабочий каталог ветки убран после слияния", not wt.exists())

    info2 = B.create_task_branch(repo, "t-778", "провал")
    wt2 = Path(info2["path"])
    (wt2 / "c.txt").write_text("три\n", encoding="utf-8")
    git("add", "c.txt", cwd=wt2)
    git("commit", "-q", "-m", "t-778: работа, которую не приняли", cwd=wt2)
    B.remove_task_worktree(repo, info2["branch"])
    check("невлитая ветка осталась видимой",
          info2["branch"] in B.list_task_branches(repo))
    check("невлитая ветка так и числится невлитой",
          not B.is_merged(repo, info2["branch"]))


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
        print("== ветки задач, раздел 14 ==")
        check_branches()
        print("== сводка без модели и без сети ==")
        check_summary_without_model()
        print("== приёмка при красном ==")
        check_accept_blocked_by_red()
        check_selftest_state_skips_inside()
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
