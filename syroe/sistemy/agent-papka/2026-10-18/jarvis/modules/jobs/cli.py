#!/usr/bin/env python3
"""Пульт диспетчера подагентов — то, чем главный агент пользуется из хода.

  .venv/bin/python -m jarvis.modules.jobs.cli submit "короткое имя" [ключи] < бриф
  .venv/bin/python -m jarvis.modules.jobs.cli list
  .venv/bin/python -m jarvis.modules.jobs.cli show job-001
  .venv/bin/python -m jarvis.modules.jobs.cli result job-001
  .venv/bin/python -m jarvis.modules.jobs.cli wait job-001 [--timeout 600]
  .venv/bin/python -m jarvis.modules.jobs.cli tick
  .venv/bin/python -m jarvis.modules.jobs.cli capacity

Ключи submit: --model haiku|sonnet, --max-turns N, --timeout СЕК,
--file путь (бриф из файла вместо stdin), --no-start (поставить в очередь,
не запуская), --project метка (необязательно; едет в job.json полем
"project" и в процесс задания переменной JARVIS_PROJECT — core/jobs.py уже
это умеет, здесь только доступ из пульта). Не задан — задание как раньше,
без проекта.

  .venv/bin/python -m jarvis.modules.jobs.cli list --project метка
                    — только задания этого проекта.

Бриф читается со стандартного ввода целиком — так его можно передать heredoc'ом
и не воевать с кавычками. Обязательные разделы (задача, где искать, что можно
самому, что только с моего согласия, формат результата, критерии приёмки)
проверяются мягко: отсутствие уходит строкой в лог, а не отказом.

submit возвращает управление сразу: процесс подагента уже запущен и переживёт
конец хода. Ждать его в том же ходе не нужно и вредно — ради этого всё и делалось.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C, jobs as J  # noqa: E402


def _flag(argv: list[str], name: str, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def cmd_submit(argv: list[str]) -> int:
    title = argv[0] if argv and not argv[0].startswith("--") else ""
    src = _flag(argv, "--file")
    brief = (Path(src).read_text(encoding="utf-8") if src else sys.stdin.read())
    job_id = J.submit(
        brief, title=title,
        model=_flag(argv, "--model"),
        max_turns=_flag(argv, "--max-turns"),
        timeout=_flag(argv, "--timeout"),
        origin=_flag(argv, "--origin", "vk"),
        project=_flag(argv, "--project"),
        autostart="--no-start" not in argv)
    meta = J.read(job_id) or {}
    print(f"{job_id} · {meta.get('status')} · {meta.get('model')} · "
          f"мест на машине: {J.capacity()}")
    print(f"бриф: {J.brief_path(job_id).relative_to(C.ROOT)}")
    print("результат придёт сам: демон подложит его в промпт следующего хода")
    return 0


def cmd_list(argv: list[str]) -> int:
    rows = J.all_jobs()
    if "--live" in argv:
        rows = [m for m in rows if m.get("status") in J.LIVE]
    project = _flag(argv, "--project")
    if project:
        rows = [m for m in rows if (m.get("project") or "") == project]
    if not rows:
        print("заданий нет")
        return 0
    for m in rows:
        mark = " " if m.get("delivered") or m["status"] in J.LIVE else "!"
        proj = f" [{m['project']}]" if m.get("project") else ""
        print(f"{mark}{m['id']}  {m['status']:<8} {str(m.get('sec') or '—'):>7}с  "
              f"{m.get('model') or '—':<8} {m['title'][:60]}{proj}")
    print(f"\nмест на машине сейчас: {J.capacity()}, "
          f"в работе: {len(J.by_status('running'))}, "
          f"ждут: {len(J.by_status('queued'))}")
    return 0


def cmd_show(argv: list[str]) -> int:
    meta = J.read(argv[0]) if argv else None
    if not meta:
        print("нет такого задания")
        return 1
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    print("\n--- бриф ---")
    print(J.brief_path(meta["id"]).read_text(encoding="utf-8"))
    return 0


def cmd_result(argv: list[str]) -> int:
    if not argv:
        return 2
    path = J.result_path(argv[0])
    if not path.exists():
        print("результата ещё нет")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_wait(argv: list[str]) -> int:
    """Дождаться задания. Нужно редко: в ходе из ВК ждать НЕ надо."""
    if not argv:
        return 2
    job_id = argv[0]
    deadline = time.monotonic() + int(_flag(argv, "--timeout", "600"))
    while time.monotonic() < deadline:
        J.tick()
        meta = J.read(job_id)
        if meta is None:
            print("нет такого задания")
            return 1
        if meta["status"] in J.DONE:
            print(f"{job_id}: {meta['status']} за {meta.get('sec')} с")
            return 0 if meta["status"] == "done" else 1
        time.sleep(5)
    print(f"{job_id}: всё ещё в работе")
    return 2


def cmd_tick(argv: list[str]) -> int:
    print(json.dumps(J.tick(), ensure_ascii=False))
    return 0


def cmd_capacity(argv: list[str]) -> int:
    print(json.dumps({"capacity": J.capacity(), "cpu_slots": J.cpu_slots(),
                      "mem_available_mb": J.mem_available_mb(),
                      "mem_per_job_mb": C.JOB_MEM_MB,
                      "running": len(J.by_status("running")),
                      "queued": len(J.by_status("queued"))},
                     ensure_ascii=False))
    return 0


COMMANDS = {"submit": cmd_submit, "list": cmd_list, "show": cmd_show,
            "result": cmd_result, "wait": cmd_wait, "tick": cmd_tick,
            "capacity": cmd_capacity}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
