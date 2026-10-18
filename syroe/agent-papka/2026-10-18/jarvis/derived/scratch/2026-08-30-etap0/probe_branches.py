"""Живая обкатка веток задач (пункт 10 приёмки этапа 0, обязательство ob-074).

Всё на подставном репозитории в derived/scratch — живой репозиторий агента не
трогается ни строкой. Запуск из каталога выше корня агента:

    jarvis/.venv/bin/python jarvis/derived/scratch/2026-08-30-etap0/probe_branches.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from jarvis.modules.projects import branches as B  # noqa: E402

TMP = Path(__file__).resolve().parent / "repo"
WT = Path(__file__).resolve().parent / "wt"
BAD = []


def check(name, ok):
    print(("  ок    " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        BAD.append(name)


def git(*args, cwd=TMP):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True)


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(WT, ignore_errors=True)
    TMP.mkdir(parents=True)
    git("init", "-b", "master")
    git("config", "user.email", "probe@local")
    git("config", "user.name", "probe")
    (TMP / "a.txt").write_text("раз\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-m", "первый")

    info = B.create_task_branch(TMP, "t-777", "проба веток")
    check("ветка названа agent/<id>-<коротко>", info["branch"] == "agent/t-777-proba-vetok")
    check("рабочий каталог заведён", Path(info["path"]).exists())
    check("рабочий каталог лежит ВНУТРИ репозитория (пункт 3 приёмки)",
          Path(info["path"]).resolve().is_relative_to(TMP.resolve()))
    check("ветка видна в списке веток задач",
          info["branch"] in B.list_task_branches(TMP))

    wt = Path(info["path"])
    (wt / "b.txt").write_text("два\n", encoding="utf-8")
    git("add", "b.txt", cwd=wt)
    git("commit", "-m", "t-777: работа исполнителя", cwd=wt)
    check("основная ветка репозитория не сдвинулась",
          not (TMP / "b.txt").exists())

    # Слияние из-под исполнителя — отбой.
    try:
        B.merge_task_branch(TMP, "t-777", info["branch"],
                            message="t-777: сливаю сам", actor="executor")
        check("исполнитель не сливает ветку", False)
    except B.PermissionRole:
        check("исполнитель не сливает ветку", True)

    # Сообщение без id задачи — отбой.
    try:
        B.merge_task_branch(TMP, "t-777", info["branch"],
                            message="мелкие правки", actor="teamlead")
        check("сообщение без id задачи не проходит", False)
    except B.BranchMessageMissingTaskId:
        check("сообщение без id задачи не проходит", True)

    res = B.merge_task_branch(TMP, "t-777", info["branch"],
                              message="t-777: принято тимлидом", actor="teamlead")
    check("тимлид слил ветку", bool(res["commit"]))
    check("работа из ветки приехала в основную", (TMP / "b.txt").exists())
    log = git("log", "--oneline", "-1").stdout
    check("id задачи есть в сообщении коммита слияния", "t-777" in log)
    check("рабочий каталог ветки убран после слияния", not wt.exists())
    check("ветка осталась видимой", info["branch"] in B.list_task_branches(TMP))

    # Невлитая ветка остаётся: провал задачи виден.
    info2 = B.create_task_branch(TMP, "t-778", "провал")
    wt2 = Path(info2["path"])
    (wt2 / "c.txt").write_text("три\n", encoding="utf-8")
    git("add", "c.txt", cwd=wt2)
    git("commit", "-m", "t-778: работа, которую не приняли", cwd=wt2)
    check("занятая рабочим деревом ветка видна в списке",
          info2["branch"] in B.list_task_branches(TMP))
    B.remove_task_worktree(TMP, info2["branch"])
    check("невлитая ветка не пропала", info2["branch"] in B.list_task_branches(TMP))
    check("она видна именно как невлитая", not B.is_merged(TMP, info2["branch"]))

    print("\nСЛОМАНО:" if BAD else "\nВСЁ ХОРОШО")
    for b in BAD:
        print(" -", b)
    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(WT, ignore_errors=True)
    return 1 if BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
