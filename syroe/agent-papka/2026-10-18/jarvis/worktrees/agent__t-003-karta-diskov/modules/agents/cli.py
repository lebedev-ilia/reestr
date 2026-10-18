#!/usr/bin/env python3
"""Пульт постоянных агентов-специалистов.

  .venv/bin/python -m jarvis.modules.agents.cli list
  .venv/bin/python -m jarvis.modules.agents.cli ask legal "вопрос одной фразой"
  .venv/bin/python -m jarvis.modules.agents.cli work legal --file задание.md
  .venv/bin/python -m jarvis.modules.agents.cli learn legal ["тема"]
  .venv/bin/python -m jarvis.modules.agents.cli status legal
  .venv/bin/python -m jarvis.modules.agents.cli brief legal "задача" --mode work

ask/work/learn возвращают управление сразу: роль поднимается отдельным
процессом через диспетчер заданий (core/jobs.py) и переживает конец хода.
Результат демон подложит в промпт следующего хода.

brief ничего не запускает — печатает готовый бриф. Сухой прогон перед тем,
как тратить токены.
"""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C, jobs as J, memory as M  # noqa: E402
from jarvis.modules.agents import roles as R  # noqa: E402


def _flag(argv: list[str], name: str, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _now() -> str:
    return datetime.now(ZoneInfo(C.TZ)).strftime("%Y-%m-%d %H:%M")


def _journal(role: str, line: str) -> None:
    jdir = R.role_dir(role) / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(ZoneInfo(C.TZ)).strftime("%Y-%m-%d")
    path = jdir / f"{day}.md"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {_now()} · {line}\n")


def _next_topic(role: str) -> str | None:
    """Следующая тема из плана знаний, по которой ещё нет файла.

    Занятой считается и тема, по которой прямо сейчас идёт задание: файл
    появится только к его концу, а до тех пор план бы выдавал одну и ту же
    тему по кругу. Живые задания опознаются по метке [slug] в заголовке.
    """
    cfg = R.load(role)
    kdir = R.role_dir(role) / "knowledge"
    done = {p.stem for p in kdir.glob("*.md")} if kdir.exists() else set()
    for meta in J.by_status(*J.LIVE):
        title = meta.get("title") or ""
        if title.startswith(f"{role}:learn") and "[" in title:
            done.add(title.split("[", 1)[1].split("]", 1)[0])
    for item in cfg.get("knowledge_plan") or []:
        if isinstance(item, dict):
            slug, title = item.get("file", ""), item.get("topic", "")
        else:
            slug, title = "", str(item)
        if slug and slug not in done:
            return (f"[{slug}] {title}\n\n"
                    f"Сложи результат в файл knowledge/{slug}.md")
    return None


def _launch(role: str, task: str, mode: str) -> int:
    brief, params = R.build_brief(role, task, mode=mode)
    title = f"{role}:{mode} {task.strip().splitlines()[0][:50]}"
    try:
        # Реестру занятой работы отдаём только саму задачу: бриф роли на
        # девять десятых одинаков от вызова к вызову и любые два вопроса
        # юристу выглядели бы для него одной работой.
        job_id = J.submit(brief, title=title, model=params["model"],
                          max_turns=params["max_turns"],
                          timeout=params["timeout"], origin="role",
                          claim_text=f"{role} {mode}: {task}",
                          # В режиме ответа зона сведена к /tmp: спросили
                          # мнение — значит ничего на диске меняться не должно,
                          # включая собственную папку роли.
                          zone=(["/tmp"] if mode == "answer" else R.zone(role)))
    except J.DuplicateWork as e:
        print(f"уже делается: {e.ref}")
        return 3
    except J.ThinBrief as e:
        print(str(e))
        return 4
    meta = J.read(job_id) or {}
    _journal(role, f"{mode}: {task.strip().splitlines()[0][:90]} → {job_id}")
    M.log("self", {"role_job": job_id, "role": role, "mode": mode,
                   "model": meta.get("model")})
    print(f"{job_id} · {meta.get('status')} · {meta.get('model')} · "
          f"роль {role} · режим {mode}")
    print("результат придёт сам: демон подложит его в промпт следующего хода")
    return 0


def cmd_list(argv: list[str]) -> int:
    names = R.roles()
    if not names:
        print("ролей нет")
        return 0
    for name in names:
        cfg = R.load(name)
        print(f"— {name}: {cfg.get('name', '')}")
        print(f"  зона: {', '.join(R.zone(name)) or '—'}")
        print(f"  модель: {cfg.get('model', 'sonnet')}")
    return 0


def cmd_status(argv: list[str]) -> int:
    role = argv[0]
    rdir = R.role_dir(role)
    status = rdir / "STATUS.md"
    print(status.read_text(encoding="utf-8") if status.exists() else "STATUS.md пуст")
    print("\nЗнания:")
    print(R.knowledge_index(role))
    jdir = rdir / "journal"
    if jdir.exists():
        last = sorted(jdir.glob("*.md"))[-3:]
        print("\nЖурнал (последние дни):")
        for p in last:
            print(f"\n## {p.stem}")
            print(p.read_text(encoding="utf-8").rstrip())
    return 0


def cmd_brief(argv: list[str]) -> int:
    role = argv[0]
    mode = _flag(argv, "--mode", "answer")
    src = _flag(argv, "--file")
    task = (Path(src).read_text(encoding="utf-8") if src
            else " ".join(a for a in argv[1:] if not a.startswith("--")))
    brief, params = R.build_brief(role, task or "(задача не задана)", mode=mode)
    print(brief)
    print(f"\n--- параметры запуска: {params}", file=sys.stderr)
    return 0


def cmd_ask(argv: list[str]) -> int:
    role = argv[0]
    task = " ".join(a for a in argv[1:] if not a.startswith("--"))
    if not task:
        print("нечего спрашивать")
        return 2
    return _launch(role, task, "answer")


def cmd_work(argv: list[str]) -> int:
    role = argv[0]
    src = _flag(argv, "--file")
    task = (Path(src).read_text(encoding="utf-8") if src
            else " ".join(a for a in argv[1:] if not a.startswith("--")))
    if not task.strip():
        print("нет задания")
        return 2
    return _launch(role, task, "work")


def _plan_topic(role: str, slug: str) -> str | None:
    """Тема плана по имени файла — чтобы взять её вне очереди."""
    for item in R.load(role).get("knowledge_plan") or []:
        if isinstance(item, dict) and item.get("file") == slug:
            return (f"[{slug}] {item.get('topic', '')}\n\n"
                    f"Сложи результат в файл knowledge/{slug}.md")
    return None


def cmd_learn(argv: list[str]) -> int:
    role = argv[0]
    task = " ".join(a for a in argv[1:] if not a.startswith("--"))
    if task:
        # Одно слово без пробелов — это, скорее всего, имя темы из плана.
        task = _plan_topic(role, task.strip()) or task
    else:
        task = _next_topic(role)
        if not task:
            print("план знаний пройден целиком — новых тем нет")
            return 1
    return _launch(role, task, "learn")


COMMANDS = {"list": cmd_list, "ask": cmd_ask, "work": cmd_work,
            "learn": cmd_learn, "status": cmd_status, "brief": cmd_brief}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    try:
        return COMMANDS[argv[0]](argv[1:])
    except R.RoleError as e:
        print(str(e))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
