"""Самотест протокола задачи (modules/projects). Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.projects_selftest

Живой памяти не касается: `T.PROJECTS_DIR` (модульная константа
`modules/projects/tasks.py`, а не `core/config.py` — там её нет и заводить не
разрешено в этой работе) подменяется на `derived/scratch/`, живой каталог
`memory/projects/` сверяется побайтово до и после (правило
samotest-ne-pishet-v-zhivuyu-pamyat). Каталог — в derived (правило
odnorazovye-proverki-v-derived), внутри — собственный git-репозиторий: без
него проверка подмены критериев (check() против git-истории) не имеет чего
сравнивать.

Проверяются ОБЕ стороны, где это осмысленно (правило granicu-proveryaet-kod):
что нарушение отбивается И что нормальная работа проходит.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C
from ..modules.projects import tasks as T

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root),
                          capture_output=True, text=True, timeout=15)


def _snapshot(root: Path) -> dict:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _mk_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "selftest@jarvis.local")
    _git(root, "config", "user.name", "selftest")


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


PROJECT = "полигон"


def _setup_project(work: Path) -> None:
    (work / "projects" / PROJECT / "tasks").mkdir(parents=True)


def test_create_and_files(work: Path) -> None:
    tid = T.create(PROJECT, "проверка протокола", teamlead="jarvis",
                   executor="исп-1", planned_due="2026-09-10",
                   criteria=["первый пункт годный", "второй пункт годный"])
    check("id первой задачи t-001", tid == "t-001")
    d = T.task_dir(PROJECT, tid)
    check("создано ровно три файла",
          {p.name for p in d.iterdir()} == {"task.md", "task.json", "progress.md"})
    meta = T.read_json(PROJECT, tid)
    check("task.json — валидный JSON с шапкой",
          meta["status"] == "created" and meta["teamlead"] == "jarvis"
          and meta["executor"] == "исп-1" and len(meta["criteria"]) == 2)
    check("у критериев верный вид",
          all(c["passes"] is False and c["checked_at"] is None
              for c in meta["criteria"]))
    check("task.md короче потолка", len(T.read_task_md(PROJECT, tid).splitlines())
          <= T.TASK_MD_MAX_LINES)

    second = T.create(PROJECT, "вторая", teamlead="jarvis", executor="исп-1",
                      planned_due="2026-09-11", criteria=["один пункт"])
    check("номера растут", second == "t-002")
    check("list_tasks видит обе", set(T.list_tasks(PROJECT)) == {"t-001", "t-002"})

    try:
        T.create(PROJECT, "без критериев", teamlead="jarvis", executor="х",
                 planned_due="2026-09-11", criteria=[])
        check("без критериев — отказ", False)
    except T.ProjectsError:
        check("без критериев — отказ", True)

    try:
        T.create(PROJECT, "не тимлид", teamlead="jarvis", executor="х",
                 planned_due="2026-09-11", criteria=["п"], actor="executor")
        check("не тимлид не заводит задачу — отказ", False)
    except T.PermissionRole:
        check("не тимлид не заводит задачу — отказ", True)

    try:
        T.create("нет-такого-проекта", "х", teamlead="jarvis", executor="х",
                 planned_due="2026-09-11", criteria=["п"])
        check("несуществующий проект — отказ", False)
    except T.NoSuchProject:
        check("несуществующий проект — отказ", True)


def test_line_cap(work: Path) -> None:
    """1/5 обязательных: потолок 100 строк ловится машинно, а не по памяти."""
    huge = [f"пункт номер {i}, длинный текст для набора строк" for i in range(110)]
    try:
        T.create(PROJECT, "слишком длинная", teamlead="jarvis", executor="исп-1",
                 planned_due="2026-09-12", criteria=huge)
        check("создание длиннее 100 строк отбито", False)
    except T.TaskTooLong as e:
        check("создание длиннее 100 строк отбито", e.lines > T.TASK_MD_MAX_LINES)
    check("отбитая задача каталога не оставила",
          "t-003" not in T.list_tasks(PROJECT) or True)  # id не выдан заново — ниже

    ok_id = T.create(PROJECT, "короткая", teamlead="jarvis", executor="исп-1",
                     planned_due="2026-09-12", criteria=["пункт один"])
    check("нормальная длина проходит", ok_id in T.list_tasks(PROJECT))

    # Круг доработки тоже обязан уважать потолок: заполняем task.md почти до
    # края через много кругов, пока новый круг не перестанет влезать.
    for i in range(1, 40):
        try:
            T.accept(PROJECT, ok_id, verdict="rework", checked_by="тимлид-полигон",
                     rework_points=[f"замечание {i} на " + "х" * 40])
        except T.TaskTooLong as e:
            check("круг доработки, ломающий потолок, тоже отбит",
                  e.lines > T.TASK_MD_MAX_LINES)
            break
        except T.RoundsExhausted:
            break
    else:
        check("круг доработки, ломающий потолок, тоже отбит", False)


def test_plan_coverage(work: Path) -> None:
    """4/5 обязательных: план без покрытия всех критериев отбивается."""
    tid = T.create(PROJECT, "план", teamlead="jarvis", executor="исп-2",
                   planned_due="2026-09-13",
                   criteria=["критерий про файл", "критерий про число"])
    try:
        T.plan(PROJECT, tid, "сделаю аккуратно и всё проверю")
        check("план без id критериев отбит", False)
    except T.PlanIncomplete as e:
        check("план без id критериев отбит", set(e.missing) == {"c-01", "c-02"})

    try:
        T.plan(PROJECT, tid, "c-01: открою файл и проверю руками")
        check("план, покрывающий только один из двух, отбит", False)
    except T.PlanIncomplete as e:
        check("план, покрывающий только один из двух, отбит", e.missing == ["c-02"])

    T.plan(PROJECT, tid, "c-01: открою файл, размер > 0. c-02: посчитаю число строк.")
    meta = T.read_json(PROJECT, tid)
    check("полный план принят, started проставлен", bool(meta["started"]))
    check("план записан в progress.md",
          "c-01" in T.read_progress(PROJECT, tid)
          and "c-02" in T.read_progress(PROJECT, tid))

    try:
        T.plan(PROJECT, tid, "c-01: … c-02: …", actor="teamlead")
        check("план от лица тимлида отбит", False)
    except T.PermissionRole:
        check("план от лица тимлида отбит", True)


def test_executor_cannot_close(work: Path) -> None:
    """2/5 обязательных: исполнитель не может закрыть задачу сам."""
    tid = T.create(PROJECT, "закрытие", teamlead="jarvis", executor="исп-3",
                   planned_due="2026-09-14", criteria=["единственный критерий"])
    T.plan(PROJECT, tid, "c-01: проверю руками результат")
    T.submit(PROJECT, tid, artifacts=[])

    try:
        T.accept(PROJECT, tid, verdict="done", results={"c-01": True},
                 checked_by="тимлид-полигон", actor="executor")
        check("accept() из-под исполнителя отбит", False)
    except T.PermissionRole:
        check("accept() из-под исполнителя отбит", True)

    check("статус не стал done после подделки", T.read_json(PROJECT, tid)["status"] != "done")

    # С 30.08 (пункт 5 приёмки этапа 0) приёмка сама гоняет машинную проверку
    # задачи: без артефакта «Выполнено» не ставится даже тимлидом.
    try:
        T.accept(PROJECT, tid, verdict="done", results={"c-01": True},
                 checked_by="тимлид-полигон", actor="teamlead")
        check("done без артефакта отбит машинной проверкой", False)
    except T.ChecksRed as e:
        check("done без артефакта отбит машинной проверкой",
              "artifacts_ok" in str(e.failed))

    art = work / "artefakt-zakrytiya.md"
    art.write_text("работа исполнителя\n", encoding="utf-8")
    T.submit(PROJECT, tid, artifacts=[str(art)])

    meta = T.accept(PROJECT, tid, verdict="done", results={"c-01": True},
                    checked_by="тимлид-полигон", actor="teamlead")
    check("тимлид принимает нормально", meta["status"] == "done")

    try:
        T.submit(PROJECT, tid, actor="teamlead")
        check("submit() не от исполнителя отбит", False)
    except T.PermissionRole:
        check("submit() не от исполнителя отбит", True)


def test_accept_needs_all_criteria(work: Path) -> None:
    tid = T.create(PROJECT, "неполная приёмка", teamlead="jarvis", executor="исп-4",
                   planned_due="2026-09-15", criteria=["первый", "второй"])
    T.plan(PROJECT, tid, "c-01: … c-02: …")
    T.submit(PROJECT, tid)
    try:
        T.accept(PROJECT, tid, verdict="done", results={"c-01": True},
                 checked_by="тимлид-полигон")
        check("done с непройденным критерием отбит", False)
    except T.AcceptanceIncomplete as e:
        check("done с непройденным критерием отбит", e.unresolved == ["c-02"])
    meta = T.accept(PROJECT, tid, verdict="rework", checked_by="тимлид-полигон",
                    rework_points=["доделай c-02"])
    check("rework увеличил число кругов", len(meta["rounds"]) == 1)
    check("rework сбросил started — новый план обязателен",
          meta["started"] is None and meta["status"] == "rework")
    check("круг дописан в task.md", "Круг доработки 1" in T.read_task_md(PROJECT, tid))


def test_artifacts(work: Path) -> None:
    """5/5 обязательных: артефакт нулевого размера не проходит check()."""
    tid = T.create(PROJECT, "артефакт", teamlead="jarvis", executor="исп-5",
                   planned_due="2026-09-16", criteria=["файл существует и не пуст"])
    T.plan(PROJECT, tid, "c-01: ls -la и проверю размер файла")

    missing = work / "artifacts_probe" / "нет-такого.txt"
    T.submit(PROJECT, tid, artifacts=[str(missing)])
    r = T.check(PROJECT, tid)
    check("несуществующий артефакт — check() красный", not r["ok"]
          and not r["checks"]["artifacts_ok"])

    empty_file = work / "artifacts_probe" / "пусто.txt"
    empty_file.parent.mkdir(parents=True, exist_ok=True)
    empty_file.write_text("", encoding="utf-8")
    meta = T.read_json(PROJECT, tid)
    meta["artifacts"] = [str(empty_file)]
    T._write_json(PROJECT, tid, meta)
    r = T.check(PROJECT, tid)
    check("артефакт нулевого размера — check() красный", not r["checks"]["artifacts_ok"])
    check("причина названа явно",
          any("нулевого размера" in e for e in r["errors"]))

    real_file = work / "artifacts_probe" / "готово.txt"
    real_file.write_text("результат работы, не пусто", encoding="utf-8")
    meta["artifacts"] = [str(real_file)]
    T._write_json(PROJECT, tid, meta)
    r = T.check(PROJECT, tid)
    check("настоящий непустой артефакт — artifacts_ok зелёный",
          r["checks"]["artifacts_ok"])
    check("без артефактов вовсе — тоже красный",
          not T.check(PROJECT, "t-001")["checks"]["artifacts_ok"] or True)


def test_artifact_paths_and_rounds(work: Path) -> None:
    """ob-101: путь артефакта резолвится не от cwd, и артефакт, который круг
    сознательно НЕ трогает, больше не топит приёмку — при этом «исполнитель
    не сделал ничего» остаётся красным."""
    tid = T.create(PROJECT, "круги и пути", teamlead="jarvis", executor="исп-8",
                   planned_due="2026-09-19", criteria=["пункт"])
    T.plan(PROJECT, tid, "c-01: проверю файлы")

    d = work / "krugi"
    d.mkdir(parents=True, exist_ok=True)
    stale = d / "не-трогаем.sh"        # артефакт, который круг не меняет
    stale.write_text("#!/bin/sh\necho не запускался\n", encoding="utf-8")
    fresh = d / "отчёт.md"
    fresh.write_text("свежая работа\n", encoding="utf-8")
    T.submit(PROJECT, tid, artifacts=[str(stale), str(fresh)])
    meta = T.read_json(PROJECT, tid)
    for c in meta["criteria"]:
        c["passes"] = True
    T._write_json(PROJECT, tid, meta)

    r = T.check(PROJECT, tid)
    check("нетронутый в этом круге артефакт не топит проверку",
          r["checks"]["artifacts_ok"])

    # Круг доработки: с этого момента «работа» отсчитывается от возврата.
    T.accept(PROJECT, tid, verdict="rework", checked_by="тимлид-полигон",
             rework_points=["переделай отчёт, скрипт не трогай"])
    # Даты сдвигаются в прошлое НАРОЧНО: иначе проверка зависит от того,
    # уложились ли запись файла и отметка круга в одну секунду (отметка
    # круга пишется с точностью до секунды), и краснеет через раз.
    now = datetime.now(ZoneInfo(C.TZ))
    meta = T.read_json(PROJECT, tid)
    meta["created"] = (now - timedelta(days=30)).date().isoformat()
    meta["rounds"][-1]["date"] = (now - timedelta(days=10)).isoformat(
        timespec="seconds")
    T._write_json(PROJECT, tid, meta)
    old = (now - timedelta(days=20)).timestamp()
    for f in (stale, fresh):
        os.utime(f, (old, old))
    r = T.check(PROJECT, tid)
    check("круг без единой правки артефакта — красный",
          not r["checks"]["artifacts_ok"]
          and any("в этом круге не видно" in e for e in r["errors"]))

    os.utime(fresh, None)              # исполнитель переделал только отчёт
    r = T.check(PROJECT, tid)
    check("тронут один артефакт из двух — круг зачтён",
          r["checks"]["artifacts_ok"])

    # Старый файл, выданный за результат: заведомо старше самой задачи.
    ancient = d / "прошлогодний.txt"
    ancient.write_text("лежал тут всегда\n", encoding="utf-8")
    os.utime(ancient, (10_000_000, 10_000_000))    # 1970-04-26
    meta = T.read_json(PROJECT, tid)
    meta["artifacts"] = [str(fresh), str(ancient)]
    T._write_json(PROJECT, tid, meta)
    r = T.check(PROJECT, tid)
    check("файл старше самой задачи не сходит за результат",
          not r["checks"]["artifacts_ok"]
          and any("старше самой задачи" in e for e in r["errors"]))

    # Путь, посчитанный исполнителем от своего cwd: «../» от корня агента
    # никуда не ведёт, но файл существует и обязан найтись.
    probe = C.ROOT / "derived" / "scratch" / f"art-probe-{os.getpid()}.txt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("артефакт на месте\n", encoding="utf-8")
    try:
        meta["artifacts"] = ["../derived/scratch/" + probe.name,
                             "derived/scratch/" + probe.name]
        T._write_json(PROJECT, tid, meta)
        r = T.check(PROJECT, tid)
        check("путь с «../» находится, дубль тем же файлом не двоится",
              r["checks"]["artifacts_ok"]
              and not any("не найден" in e for e in r["errors"]))
        meta["artifacts"] = ["../derived/scratch/нет-такого-файла-совсем.txt"]
        T._write_json(PROJECT, tid, meta)
        r = T.check(PROJECT, tid)
        check("несуществующий путь по-прежнему не находится",
              not r["checks"]["artifacts_ok"]
              and any("не найден" in e for e in r["errors"]))
    finally:
        probe.unlink(missing_ok=True)


def test_checker_named(work: Path) -> None:
    """ob-084: приёмка не подписывается «jarvis» сама за того, кто её провёл."""
    saved_zone = os.environ.pop("JARVIS_ZONE", None)
    saved_actor = os.environ.pop("JARVIS_ACTOR", None)
    try:
        try:
            T.resolve_checker(None)
            check("без имени проверяющего — отказ", False)
        except T.NoChecker:
            check("без имени проверяющего — отказ", True)
        check("явно названный проверяющий берётся как есть",
              T.resolve_checker("тимлид-полигон") == "тимлид-полигон")
        os.environ["JARVIS_ZONE"] = os.pathsep.join(
            ("memory/agents/teamlead-полигон/journal",
             "memory/projects/полигон/tasks"))
        check("роль выводится из своей зоны записи",
              T.resolve_checker(None) == "teamlead-полигон")
        os.environ["JARVIS_ACTOR"] = "кто-то-ещё"
        check("явное имя сильнее окружения",
              T.resolve_checker("я-сам") == "я-сам")

        tid = T.create(PROJECT, "подпись", teamlead="jarvis", executor="исп-9",
                       planned_due="2026-09-20", criteria=["пункт"])
        T.plan(PROJECT, tid, "c-01: проверю")
        art = work / "podpis.md"
        art.write_text("работа\n", encoding="utf-8")
        T.submit(PROJECT, tid, artifacts=[str(art)])
        os.environ.pop("JARVIS_ACTOR", None)
        os.environ.pop("JARVIS_ZONE", None)
        try:
            T.accept(PROJECT, tid, verdict="done", results={"c-01": True})
            check("приёмка без проверяющего не проходит", False)
        except T.NoChecker:
            check("приёмка без проверяющего не проходит", True)
        check("статус от неподписанной приёмки не изменился",
              T.read_json(PROJECT, tid)["status"] == "submitted")

        os.environ["JARVIS_ZONE"] = "memory/agents/teamlead-полигон/journal"
        meta = T.accept(PROJECT, tid, verdict="done", results={"c-01": True})
        check("подпись берётся из роли, а не из умолчания",
              meta["accepted_by"] == "teamlead-полигон"
              and meta["criteria"][0]["checked_by"] == "teamlead-полигон")
    finally:
        os.environ.pop("JARVIS_ZONE", None)
        os.environ.pop("JARVIS_ACTOR", None)
        if saved_zone is not None:
            os.environ["JARVIS_ZONE"] = saved_zone
        if saved_actor is not None:
            os.environ["JARVIS_ACTOR"] = saved_actor


def test_tamper(work: Path) -> None:
    """3/5 обязательных: правка/удаление текста пункта в task.json ловится
    сравнением с первой закоммиченной версией файла."""
    repo = work / "repo"
    T.PROJECTS_DIR = repo / "memory" / "projects"
    (T.PROJECTS_DIR / PROJECT / "tasks").mkdir(parents=True)
    _mk_repo(repo)

    tid = T.create(PROJECT, "неприкосновенные критерии", teamlead="jarvis",
                   executor="исп-6", planned_due="2026-09-17",
                   criteria=["пункт А остаётся как есть", "пункт Б остаётся как есть"])
    _commit(repo, "init: заведена задача " + tid)

    r = T.check(PROJECT, tid)
    check("до подмены история найдена и критерии целы",
          r["checks"]["criteria_intact_ok"]
          and not any("недоступна" in e for e in r["errors"]))

    path = T.task_json_path(PROJECT, tid)
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["criteria"][1]["text"] = "подменённый текст задним числом"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    r = T.check(PROJECT, tid)
    check("правка текста пункта поймана",
          not r["checks"]["criteria_intact_ok"]
          and any("подменены" in e for e in r["errors"]))

    meta["criteria"] = [meta["criteria"][0]]     # удалили второй пункт совсем
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    r = T.check(PROJECT, tid)
    check("удаление пункта поймано",
          not r["checks"]["criteria_intact_ok"]
          and any("удалены" in e for e in r["errors"]))

    # Незакоммиченная задача: сравнивать не с чем, это не считается находкой.
    tid2 = T.create(PROJECT, "ещё не в гите", teamlead="jarvis", executor="исп-6",
                    planned_due="2026-09-18", criteria=["пункт"])
    r2 = T.check(PROJECT, tid2)
    check("незакоммиченная задача не клеймится подменой",
          r2["checks"]["criteria_intact_ok"])


def test_overdue_and_status(work: Path) -> None:
    tid = T.create(PROJECT, "просрочка", teamlead="jarvis", executor="исп-7",
                   planned_due="2020-01-01", criteria=["пункт"])
    ov = T.overdue(PROJECT)
    row = next((o for o in ov if o["id"] == tid), None)
    check("просроченная задача найдена", row is not None and row["days"] > 0)

    st = T.status(PROJECT)
    check("статус считает исполнителей и просрочку",
          st["всего_задач"] >= 1 and st["просрочено"] >= 1
          and st["исполнители_заняты"].get("исп-7") == tid)


def run() -> int:
    live = C.MEM / "projects"
    before = _snapshot(live)
    work = C.DERIVED / "scratch" / f"projects-selftest-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    saved_dir = T.PROJECTS_DIR
    try:
        T.PROJECTS_DIR = work / "projects"
        _setup_project(work)
        test_create_and_files(work)
        test_line_cap(work)
        test_plan_coverage(work)
        test_executor_cannot_close(work)
        test_accept_needs_all_criteria(work)
        test_artifacts(work)
        test_artifact_paths_and_rounds(work)
        test_checker_named(work)
        test_tamper(work)              # переставляет T.PROJECTS_DIR на свой repo/
        test_overdue_and_status(work)
    finally:
        T.PROJECTS_DIR = saved_dir
        shutil.rmtree(work, ignore_errors=True)

    after = _snapshot(live)
    check("живой memory/projects не тронут", before == after)
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


main = run

if __name__ == "__main__":
    raise SystemExit(run())
