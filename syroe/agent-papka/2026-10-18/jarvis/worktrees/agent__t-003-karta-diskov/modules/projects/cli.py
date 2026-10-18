"""Пульт протокола задачи. Запуск из каталога ВЫШЕ корня агента:

    cd "…/Agent"
    jarvis/.venv/bin/python -m jarvis.modules.projects.cli status vnutrennii

Подкоманды: create, plan, submit, accept, check, status, overdue, list, а
также branch-create/branch-merge/branch-list (ветки задач, раздел 14 —
modules/projects/branches.py). Сообщения об ошибках — по-русски, код
возврата 1 на любой отказ протокола (core/agent.py такие отказы не глотает —
их видно в выводе Bash).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from jarvis.core import config as C

from . import branches as BR
from . import cost as CO
from . import summary as S
from . import tasks as T

# --------------------------------------------------------------------------
# Зона роли (bl-145). Шлюз (core/agent.py, ZONE_PY_CLI_CMDS) пускает роль с
# зоной в этот модуль и только в него — тело `python -c` шлюзу невидимо, а
# этот код виден и под гитом. Вторая половина границы стоит здесь: шлюз судит
# ПОДКОМАНДУ, а какая папка окажется под правкой, знает только модуль.
#
# JARVIS_ZONE читается тем же способом, что в core.agent.role_zone(); импорт
# core.agent сюда не тянется намеренно — пульт обязан подниматься без SDK и
# без хука, иначе роль потеряет протокол при любой поломке ядра.
WRITE_CMDS = frozenset(("create", "plan", "submit", "accept"))
BRANCH_CMDS = frozenset(("branch-create", "branch-merge"))


def _zone_paths() -> list[Path]:
    out = []
    for item in os.environ.get("JARVIS_ZONE", "").split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        p = Path(item).expanduser()
        out.append((p if p.is_absolute() else C.ROOT / p).resolve())
    return out


def _zone_refusal(cmd: str, args) -> str | None:
    """Причина отказа, если роль правит задачи вне своей зоны. Иначе None."""
    zone = _zone_paths()
    if not zone:                       # зоны нет — это сам Jarvis, ограничений нет
        return None
    if cmd in BRANCH_CMDS:
        return ("ветки задач роли с зоной закрыты: они запускают git в "
                "репозитории проекта, а это изменение вне твоей папки. "
                "Нужна ветка или слияние — опиши это в результате задания")
    if cmd not in WRITE_CMDS:
        return None
    target = T.tasks_dir(args.project).resolve()
    for base in zone:
        if target == base or base in target.parents:
            return None
    return (f"папка задач {target} вне твоей зоны "
            f"({', '.join(str(p) for p in zone)}): чужой проект ведёт его "
            f"тимлид. Читать чужое можно (status, list, check), менять — нет")


def _read_text_arg(args) -> str:
    if args.text:
        return args.text
    if args.file:
        return open(args.file, encoding="utf-8").read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def cmd_create(args) -> int:
    task_id = T.create(
        args.project, args.title, teamlead=args.teamlead,
        executor=args.executor, planned_due=args.planned_due,
        criteria=args.criteria or [], goals=args.goals or "",
        boundaries=args.boundaries or "", links=args.links or "",
        materials=args.materials or "")
    print(json.dumps({"id": task_id,
                      "task_md": str(T.task_md_path(args.project, task_id)),
                      "task_json": str(T.task_json_path(args.project, task_id)),
                      "progress_md": str(T.progress_md_path(args.project, task_id))},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_plan(args) -> int:
    text = _read_text_arg(args)
    T.plan(args.project, args.task, text, actor=args.role)
    print("план записан")
    return 0


def cmd_submit(args) -> int:
    meta = T.submit(args.project, args.task, artifacts=args.artifact or [],
                    note=args.note or "", actor=args.role)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


def cmd_accept(args) -> int:
    results = {}
    for cid in (args.passed or []):
        results[cid] = True
    for cid in (args.failed or []):
        results[cid] = False
    meta = T.accept(
        args.project, args.task, verdict=args.verdict, results=results,
        note=args.note or "", checked_by=args.checked_by,
        rework_points=args.rework_point or [], rework_due=args.rework_due,
        actor=args.role)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0


def cmd_check(args) -> int:
    result = T.check(args.project, args.task)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


def cmd_status(args) -> int:
    print(json.dumps(T.status(args.project), ensure_ascii=False, indent=1))
    return 0


def cmd_overdue(args) -> int:
    print(json.dumps(T.overdue(args.project), ensure_ascii=False, indent=1))
    return 0


def cmd_list(args) -> int:
    ids = T.list_tasks(args.project)
    rows = [T.read_json(args.project, tid) for tid in ids]
    print(json.dumps([{"id": r["id"], "title": r["title"],
                       "status": r["status"], "executor": r["executor"],
                       "planned_due": r["planned_due"]} for r in rows],
                     ensure_ascii=False, indent=1))
    return 0


def cmd_summary(args) -> int:
    data = S.collect(args.project)
    if args.text:
        print(S.render_text(data))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


def cmd_cost(args) -> int:
    row = CO.for_project(args.project)
    if args.text:
        print(CO.render_text(row))
    else:
        print(json.dumps(row, ensure_ascii=False, indent=1))
    return 0


def cmd_branch_create(args) -> int:
    info = BR.create_task_branch(args.repo, args.task, args.short, base=args.base)
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


def cmd_branch_merge(args) -> int:
    info = BR.merge_task_branch(args.repo, args.task, args.branch,
                                message=args.message, actor=args.role)
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


def cmd_branch_list(args) -> int:
    print(json.dumps(BR.list_task_branches(args.repo), ensure_ascii=False, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="projects")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="завести задачу (тимлид)")
    c.add_argument("project")
    c.add_argument("--title", required=True)
    c.add_argument("--teamlead", required=True)
    c.add_argument("--executor", required=True)
    c.add_argument("--planned-due", required=True, dest="planned_due")
    c.add_argument("--criteria", action="append",
                   help="можно повторять, один пункт за раз")
    c.add_argument("--goals", default="")
    c.add_argument("--boundaries", default="")
    c.add_argument("--links", default="")
    c.add_argument("--materials", default="")
    c.set_defaults(func=cmd_create)

    pl = sub.add_parser("plan", help="план проверки критериев (исполнитель)")
    pl.add_argument("project")
    pl.add_argument("task")
    pl.add_argument("--text")
    pl.add_argument("--file")
    pl.add_argument("--role", default="executor")
    pl.set_defaults(func=cmd_plan)

    s = sub.add_parser("submit", help="сдать задачу (исполнитель)")
    s.add_argument("project")
    s.add_argument("task")
    s.add_argument("--artifact", action="append",
                   help="путь к файлу-результату, относительно корня агента; можно повторять")
    s.add_argument("--note", default="")
    s.add_argument("--role", default="executor")
    s.set_defaults(func=cmd_submit)

    a = sub.add_parser("accept", help="принять или вернуть на доработку (тимлид)")
    a.add_argument("project")
    a.add_argument("task")
    a.add_argument("--verdict", choices=("done", "rework"), required=True)
    a.add_argument("--pass", dest="passed", action="append",
                   help="id критерия, прошедшего проверку; можно повторять")
    a.add_argument("--fail", dest="failed", action="append",
                   help="id критерия, не прошедшего проверку; можно повторять")
    a.add_argument("--note", default="")
    a.add_argument("--checked-by", dest="checked_by", default="jarvis")
    a.add_argument("--rework-point", dest="rework_point", action="append")
    a.add_argument("--rework-due", dest="rework_due")
    a.add_argument("--role", default="teamlead")
    a.set_defaults(func=cmd_accept)

    ch = sub.add_parser("check", help="машинные проверки доказательства выполнения")
    ch.add_argument("project")
    ch.add_argument("task")
    ch.set_defaults(func=cmd_check)

    st = sub.add_parser("status", help="сводка по проекту")
    st.add_argument("project")
    st.set_defaults(func=cmd_status)

    ov = sub.add_parser("overdue", help="просроченные задачи")
    ov.add_argument("project")
    ov.set_defaults(func=cmd_overdue)

    ls = sub.add_parser("list", help="все задачи проекта")
    ls.add_argument("project")
    ls.set_defaults(func=cmd_list)

    sm = sub.add_parser("summary", help="сводка по проекту, раздел 7 документа")
    sm.add_argument("project")
    sm.add_argument("--text", action="store_true",
                    help="плоский русский текст вместо JSON")
    sm.set_defaults(func=cmd_summary)

    co = sub.add_parser("cost", help="расход по проекту: сутки и неделя")
    co.add_argument("project")
    co.add_argument("--text", action="store_true",
                    help="плоский русский текст вместо JSON")
    co.set_defaults(func=cmd_cost)

    bc = sub.add_parser("branch-create",
                        help="завести ветку задачи worktree'ом (раздел 14)")
    bc.add_argument("repo", help="путь к git-репозиторию проекта")
    bc.add_argument("task")
    bc.add_argument("short", help="короткое описание, транслитерируется само")
    bc.add_argument("--base")
    bc.set_defaults(func=cmd_branch_create)

    bm = sub.add_parser("branch-merge",
                        help="слить ветку задачи в основную (только тимлид)")
    bm.add_argument("repo")
    bm.add_argument("task")
    bm.add_argument("branch")
    bm.add_argument("--message", required=True,
                    help="обязан содержать id задачи")
    bm.add_argument("--role", default="teamlead")
    bm.set_defaults(func=cmd_branch_merge)

    bl = sub.add_parser("branch-list", help="ветки задач в репозитории")
    bl.add_argument("repo")
    bl.set_defaults(func=cmd_branch_list)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if why := _zone_refusal(args.cmd, args):
        print(f"отказ: {why}", file=sys.stderr)
        return 1
    try:
        return args.func(args)
    except T.ProjectsError as e:
        print(f"отказ: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
