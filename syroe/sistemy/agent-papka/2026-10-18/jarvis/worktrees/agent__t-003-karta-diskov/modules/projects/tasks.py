"""Протокол задачи проекта (раздел 3 «Джарвис — команда и проекты», этап 0).

Папка задачи — `memory/projects/<проект>/tasks/<id>/` — содержит ровно три
файла с разными владельцами и разными правами записи:

  task.md      — пишет тимлид, исполнителю только чтение. Потолок 100 строк,
                 проверяется машинно при КАЖДОЙ правке (создание, круг
                 доработки), а не один раз при заведении.
  task.json    — критерии приёмки. Статус (`passes`) меняет только тимлид
                 при `accept()`; текст пункта не меняется никогда — попытка
                 подмены ловится `check()` сравнением с первой закоммиченной
                 версией файла.
  progress.md  — ведёт исполнитель: план проверки критериев (обязателен до
                 submit), журнал работы, вопросы.

Почему в memory/, а не в derived/: работа команды по проекту ничем не
пересоздаётся, случайный снос derived не должен уносить с собой задачи и их
историю (правило v-derived-tolko-vosstanovimoe).

Путь к каталогу проектов — модульная константа PROJECTS_DIR, а не значение,
захваченное локальной переменной внутри функций: самотест подменяет именно
её (`T.PROJECTS_DIR = tmp`), и это подхватывают все функции модуля, потому
что каждая обращается к ней заново при вызове, а не к копии, посчитанной при
импорте (тот же урок, что bl-118 в core/jobs.py и core/config.py).

Роли передаются явно строкой (`actor="executor"` / `actor="teamlead"`) —
модуль не знает, кто их вызывает, знает только то, что ему передали. Различие
ролей держит код, а не идентификатор процесса: `accept()` тимлидом и
`accept()` исполнителем — разные ветки одной функции, и вторая всегда падает.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.core import config as C

PROJECTS_DIR = C.MEM / "projects"

TASK_MD_MAX_LINES = 100
ID_RE = re.compile(r"^t-(\d+)$")
CRIT_ID_RE = re.compile(r"^c-(\d+)$")

# Статусы задачи. created → (план проверки) → in_progress → submitted →
# done | rework (возврат к in_progress, новый круг) → … → escalated.
OPEN_STATUSES = ("created", "in_progress", "submitted", "rework")
MAX_ROUNDS = 3


class ProjectsError(ValueError):
    """Базовая ошибка протокола — всегда с понятным русским текстом."""


class NoSuchProject(ProjectsError):
    pass


class NoSuchTask(ProjectsError):
    pass


class TaskTooLong(ProjectsError):
    def __init__(self, lines: int):
        self.lines = lines
        super().__init__(
            f"task.md длиннее потолка: {lines} строк при потолке "
            f"{TASK_MD_MAX_LINES}. Не влезло — не влезло: сократи текст или "
            f"убери часть по остаточному принципу (даты, цели, критерии, "
            f"границы, связи, материалы — по убыванию приоритета), но задачу "
            f"не дроби.")


class PermissionRole(ProjectsError):
    pass


class PlanIncomplete(ProjectsError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            "план проверки не покрывает все критерии, не хватает: "
            + ", ".join(missing) + ". Плана «сделаю аккуратно» недостаточно — "
            "по каждому id критерия нужно назвать способ проверки.")


class AcceptanceIncomplete(ProjectsError):
    def __init__(self, unresolved: list[str]):
        self.unresolved = unresolved
        super().__init__(
            "нельзя закрыть задачу «Выполнено»: не пройдены критерии "
            + ", ".join(unresolved))


class RoundsExhausted(ProjectsError):
    def __init__(self):
        super().__init__(
            f"исчерпаны все {MAX_ROUNDS} круга доработки — задача уходит к "
            "Jarvis, а не на новый круг")


# --------------------------------------------------------------------------
# Пути
# --------------------------------------------------------------------------

def project_dir(project: str) -> Path:
    return PROJECTS_DIR / project


def require_project(project: str) -> Path:
    pdir = project_dir(project)
    if not pdir.is_dir():
        known = ", ".join(sorted(p.name for p in PROJECTS_DIR.iterdir()
                                  if p.is_dir())) if PROJECTS_DIR.exists() else "—"
        raise NoSuchProject(f"нет такого проекта: {project}. Есть: {known}")
    return pdir


def tasks_dir(project: str) -> Path:
    return project_dir(project) / "tasks"


def task_dir(project: str, task_id: str) -> Path:
    return tasks_dir(project) / task_id


def task_md_path(project: str, task_id: str) -> Path:
    return task_dir(project, task_id) / "task.md"


def task_json_path(project: str, task_id: str) -> Path:
    return task_dir(project, task_id) / "task.json"


def progress_md_path(project: str, task_id: str) -> Path:
    return task_dir(project, task_id) / "progress.md"


def queue_path(project: str) -> Path:
    """Очередь проекта — раздел 6.1 документа, именно backlog.md.

    Имя то же, что у главной очереди агента, но файл другой: свой формат
    (свободный текст, а не семь колонок), свой владелец (тимлид проекта) и
    своя проверка — queue_lint ниже. До 30.08 файл назывался queue.md, потому
    что шлюз отбивал запись по базовому ИМЕНИ файла; теперь он судит по
    полному пути (bl-142), и имя из документа освободилось.
    """
    return project_dir(project) / "backlog.md"


# Строка главной очереди агента: семь колонок и id вида bl-NNN.
_MAIN_ROW = re.compile(r"^\s*\|\s*bl-\d+\s*\|")


def queue_lint(project: str) -> list[str]:
    """Претензии к очереди проекта. Пустой список — всё в порядке.

    Проверяем ровно то, что может сломаться молча: файл на месте, назван по
    документу, и в него не заползли строки главной очереди агента — две
    очереди живут раздельно, и путать их дороже всего.
    """
    problems: list[str] = []
    path = queue_path(project)
    if not path.exists():
        return [f"нет очереди проекта: {path}"]
    if (project_dir(project) / "queue.md").exists():
        problems.append("рядом лежит старый queue.md — очередь раздвоилась")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        problems.append("очередь проекта пуста целиком — нет даже заголовка")
    for i, line in enumerate(text.splitlines(), 1):
        if _MAIN_ROW.match(line):
            problems.append(f"строка {i}: строка ГЛАВНОЙ очереди (bl-…) "
                            "в очереди проекта")
    return problems


def _now() -> datetime:
    return datetime.now(ZoneInfo(C.TZ))


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _today_iso() -> str:
    return _now().date().isoformat()


# --------------------------------------------------------------------------
# Чтение/запись
# --------------------------------------------------------------------------

def list_tasks(project: str) -> list[str]:
    require_project(project)
    tdir = tasks_dir(project)
    if not tdir.exists():
        return []
    return sorted(p.name for p in tdir.iterdir()
                  if p.is_dir() and ID_RE.match(p.name)
                  and (p / "task.json").exists())


def require_task(project: str, task_id: str) -> Path:
    d = task_dir(project, task_id)
    if not (d / "task.json").exists():
        raise NoSuchTask(f"нет такой задачи: {project}/{task_id}")
    return d


def read_json(project: str, task_id: str) -> dict:
    require_task(project, task_id)
    return json.loads(task_json_path(project, task_id).read_text(encoding="utf-8"))


def _write_json(project: str, task_id: str, meta: dict) -> None:
    """Атомарная запись: временный файл, потом переименование (как в jobs.py) —
    половинчатый json не должен выглядеть как задача с потерянными критериями."""
    path = task_json_path(project, task_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_task_md(project: str, task_id: str) -> str:
    require_task(project, task_id)
    return task_md_path(project, task_id).read_text(encoding="utf-8")


def read_progress(project: str, task_id: str) -> str:
    require_task(project, task_id)
    return progress_md_path(project, task_id).read_text(encoding="utf-8")


def _new_task_id(project: str) -> str:
    """Следующий свободный id. mkdir — тот же замок, что у core/jobs.new_id:
    два процесса не получат один номер."""
    tdir = tasks_dir(project)
    tdir.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for d in tdir.iterdir()
            if (m := ID_RE.match(d.name))]
    n = (max(nums) + 1) if nums else 1
    while True:
        task_id = f"t-{n:03d}"
        try:
            task_dir(project, task_id).mkdir()
            return task_id
        except FileExistsError:
            n += 1


# --------------------------------------------------------------------------
# task.md: сборка текста и потолок строк
# --------------------------------------------------------------------------

def _build_task_md(task_id: str, title: str, *, teamlead: str, executor: str,
                    created: str, planned_due: str, criteria: list[str],
                    boundaries: str, links: str, materials: str,
                    goals: str) -> str:
    crit_lines = "\n".join(
        f"- [c-{i:02d}] {text}" for i, text in enumerate(criteria, 1))
    return f"""# Задача {task_id}: {title}

## Даты
- создана (тимлид): {created}
- плановый срок (тимлид): {planned_due}
- начата (исполнитель): —
- сдана фактически (исполнитель): —
- тимлид: {teamlead}
- исполнитель: {executor}

## Цели
{goals or title}

## Критерии
{crit_lines}

## Границы
{boundaries or "—"}

## Связи
{links or "—"}

## Материалы
{materials or "—"}
"""


def _check_line_cap(text: str) -> None:
    n = len(text.splitlines())
    if n > TASK_MD_MAX_LINES:
        raise TaskTooLong(n)


# --------------------------------------------------------------------------
# Создание задачи (тимлид)
# --------------------------------------------------------------------------

def create(project: str, title: str, *, teamlead: str, executor: str,
           planned_due: str, criteria: list[str], goals: str = "",
           boundaries: str = "", links: str = "", materials: str = "",
           actor: str = "teamlead") -> str:
    """Завести задачу. Только тимлид (раздел 3.3, шаг 1)."""
    if actor != "teamlead":
        raise PermissionRole("задачу заводит только тимлид")
    require_project(project)
    title = (title or "").strip()
    if not title:
        raise ProjectsError("нужен заголовок задачи")
    criteria = [c.strip() for c in (criteria or []) if c.strip()]
    if not criteria:
        raise ProjectsError("нужен хотя бы один критерий приёмки")
    if not planned_due:
        raise ProjectsError("нужна плановая дата окончания — ставится даже "
                             "на пятиминутных задачах")

    created = _today_iso()
    task_id = _new_task_id(project)
    try:
        task_md = _build_task_md(
            task_id, title, teamlead=teamlead, executor=executor,
            created=created, planned_due=planned_due, criteria=criteria,
            boundaries=boundaries, links=links, materials=materials,
            goals=goals)
        _check_line_cap(task_md)

        meta = {
            "id": task_id, "project": project, "title": title,
            "teamlead": teamlead, "executor": executor,
            "created": created, "planned_due": planned_due,
            "started": None, "finished": None,
            "accepted_at": None, "accepted_by": None,
            "status": "created", "rounds": [], "artifacts": [],
            "criteria": [
                {"id": f"c-{i:02d}", "text": text, "passes": False,
                 "checked_at": None, "checked_by": None}
                for i, text in enumerate(criteria, 1)],
        }

        task_md_path(project, task_id).write_text(task_md, encoding="utf-8")
        _write_json(project, task_id, meta)
        progress_md_path(project, task_id).write_text(
            f"""# Прогресс: {task_id} — {title}

## Ритуал начала работы (обязателен, раздел 3.3)
- [ ] где я: прочитал task.md целиком
- [ ] прочитал этот файл (progress.md) целиком, включая чужие пометки
- [ ] посмотрел историю правок задачи (git log -- путь к папке задачи)
- [ ] проверил, что оставленное предыдущим ходом не сломано

## План проверки критериев
(пусто — заполняется ДО начала работы; plan() требует упоминания каждого id
критерия из task.json, иначе задача не считается принятой в работу)

## Журнал работы

## Вопросы тимлиду
(мягкий потолок — три за задачу; четвёртый возвращает задачу на переформулировку)
""", encoding="utf-8")
    except Exception:
        # Откат недописанной задачи: половинчатая папка хуже отсутствующей —
        # её id занят, а по содержимому её не отличить от настоящей.
        import shutil
        shutil.rmtree(task_dir(project, task_id), ignore_errors=True)
        raise
    return task_id


# --------------------------------------------------------------------------
# План проверки (исполнитель, до старта работы)
# --------------------------------------------------------------------------

def _replace_section(text: str, header: str, body: str) -> str:
    """Заменить содержимое markdown-раздела `## header` до следующего `## `."""
    pat = re.compile(
        r"(^## " + re.escape(header) + r"\s*\n)(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL)
    if not pat.search(text):
        return text + f"\n## {header}\n{body}\n"
    return pat.sub(lambda m: m.group(1) + body + "\n", text)


def plan(project: str, task_id: str, text: str, *,
         actor: str = "executor") -> None:
    """Исполнитель до старта работы называет способ проверки каждого критерия.

    Покрытие проверяется машинно и буквально: план обязан упомянуть id
    КАЖДОГО критерия из task.json (`c-01`, `c-02`, …). Формулировка «сделаю
    аккуратно» не покрывает ничего и отбивается со списком непокрытых id —
    ровно то, для чего механизм существует (раздел 5 документа: исполнитель
    сам не знает, чего не умеет, и «всё понятно» не проверка).
    """
    if actor != "executor":
        raise PermissionRole("план проверки пишет исполнитель")
    meta = read_json(project, task_id)
    text = (text or "").strip()
    if not text:
        raise PlanIncomplete([c["id"] for c in meta["criteria"]])
    missing = [c["id"] for c in meta["criteria"] if c["id"] not in text]
    if missing:
        raise PlanIncomplete(missing)

    progress = progress_md_path(project, task_id)
    body = progress.read_text(encoding="utf-8")
    stamped = f"_записано {_now_iso()}_\n\n{text}\n"
    progress.write_text(
        _replace_section(body, "План проверки критериев", stamped),
        encoding="utf-8")

    if not meta.get("started"):
        meta["started"] = _now_iso()
        meta["status"] = "in_progress"
        _write_json(project, task_id, meta)


# --------------------------------------------------------------------------
# Сдача (исполнитель) — закрыть задачу сам он не может
# --------------------------------------------------------------------------

def submit(project: str, task_id: str, *, artifacts: list[str] | None = None,
           note: str = "", actor: str = "executor") -> dict:
    if actor != "executor":
        raise PermissionRole("сдаёт задачу исполнитель")
    meta = read_json(project, task_id)
    if not meta.get("started"):
        raise ProjectsError(
            "нельзя сдать задачу без плана проверки — сначала plan()")

    artifacts = [a.strip() for a in (artifacts or []) if a.strip()]
    if artifacts:
        have = set(meta.get("artifacts") or [])
        meta["artifacts"] = sorted(have | set(artifacts))

    meta["status"] = "submitted"
    meta["finished"] = _now_iso()
    _write_json(project, task_id, meta)

    progress = progress_md_path(project, task_id)
    body = progress.read_text(encoding="utf-8")
    line = f"\n- {_now_iso()} · сдано. {note}".rstrip()
    if "## Журнал работы" in body:
        body = body.replace(
            "## Журнал работы", "## Журнал работы" + "\n" + line.strip(), 1)
    else:
        body += f"\n## Журнал работы\n{line.strip()}\n"
    progress.write_text(body, encoding="utf-8")
    return meta


# --------------------------------------------------------------------------
# Приёмка (только тимлид)
# --------------------------------------------------------------------------

def accept(project: str, task_id: str, *, verdict: str,
           results: dict[str, bool] | None = None, note: str = "",
           checked_by: str = "jarvis", rework_points: list[str] | None = None,
           rework_due: str | None = None, actor: str = "teamlead") -> dict:
    """Тимлид сам прогоняет проверки и выносит вердикт. `accept()` из-под
    исполнителя всегда отбивается — это механика, а не вопрос совести
    (раздел 5 документа: «запрет закрывать задачу самому»)."""
    if actor != "teamlead":
        raise PermissionRole("принимает задачу только тимлид")
    if verdict not in ("done", "rework"):
        raise ProjectsError("вердикт бывает только 'done' или 'rework'")

    meta = read_json(project, task_id)
    now = _now_iso()
    results = results or {}
    for c in meta["criteria"]:
        if c["id"] in results:
            c["passes"] = bool(results[c["id"]])
            c["checked_at"] = now
            c["checked_by"] = checked_by

    if verdict == "done":
        unresolved = [c["id"] for c in meta["criteria"] if not c.get("passes")]
        if unresolved:
            _write_json(project, task_id, meta)   # частичная проверка не теряется
            raise AcceptanceIncomplete(unresolved)
        meta["status"] = "done"
        meta["accepted_at"] = now
        meta["accepted_by"] = checked_by
        _write_json(project, task_id, meta)
        return meta

    # verdict == "rework"
    round_no = len(meta.get("rounds", [])) + 1
    if round_no > MAX_ROUNDS:
        raise RoundsExhausted()
    rnd = {"n": round_no, "date": now, "by": checked_by,
           "points": list(rework_points or []), "due": rework_due, "note": note}
    meta.setdefault("rounds", []).append(rnd)
    meta["status"] = "rework"
    meta["started"] = None            # новый круг — новый план проверки
    _append_rework_round(project, task_id, rnd)
    _write_json(project, task_id, meta)
    return meta


def _append_rework_round(project: str, task_id: str, rnd: dict) -> None:
    """Круг доработки дописывается в task.md (раздел 3.3, п.8) — с проверкой
    того же потолка в 100 строк, что и при заведении: правка тимлида не
    освобождена от предела просто потому, что задача уже существует."""
    path = task_md_path(project, task_id)
    body = path.read_text(encoding="utf-8")
    points = "\n".join(f"- {p}" for p in rnd["points"]) or "- (без пунктов)"
    addition = (f"\n## Круг доработки {rnd['n']} ({rnd['date']})\n"
                f"{points}\n" + (f"срок: {rnd['due']}\n" if rnd["due"] else ""))
    new_body = body.rstrip("\n") + "\n" + addition
    _check_line_cap(new_body)
    path.write_text(new_body, encoding="utf-8")


# --------------------------------------------------------------------------
# Машинная проверка доказательства выполнения (раздел 4 документа)
# --------------------------------------------------------------------------

def _git_root(path: Path) -> Path | None:
    start = path if path.is_dir() else path.parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=str(start),
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return Path(out.stdout.strip())


def _first_committed_json(path: Path) -> dict | None:
    """Первая закоммиченная версия task.json. None, если файл ещё не в гите —
    сравнивать тогда не с чем, и это не считается находкой (задача просто
    молодая), а не поводом молчать: check() называет это отдельно.

    `git rev-parse --show-toplevel` всегда возвращает АБСОЛЮТНЫЙ путь — если
    path пришёл относительным (обычный случай вызова из корня агента),
    `path.relative_to(root)` иначе падает ValueError на несовпадении
    абсолютного и относительного префикса, и проверка молча выключается.
    """
    if not path.exists():
        return None
    path = path.resolve()
    root = _git_root(path)
    if root is None:
        return None
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return None
    try:
        # Без --follow: комбинация --follow с --diff-filter теряет коммиты у
        # некоторых версий git (проверено вручную 30.08 — с --follow лог
        # пустой, без него первый коммит находится). Слежение за переименованием
        # тут и не нужно: файл задачи не переименовывают.
        log = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%H",
             "--", rel], cwd=str(root), capture_output=True, text=True,
            timeout=15)
        commits = [c for c in log.stdout.splitlines() if c.strip()]
        if not commits:
            return None
        first = commits[-1]
        show = subprocess.run(
            ["git", "show", f"{first}:{rel}"], cwd=str(root),
            capture_output=True, text=True, timeout=15)
        if show.returncode != 0:
            return None
        return json.loads(show.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def _criteria_intact(project: str, task_id: str, meta: dict) -> tuple[bool, str]:
    orig = _first_committed_json(task_json_path(project, task_id))
    if orig is None:
        return True, "история гита недоступна — сравнение пропущено (задача ещё не закоммичена)"
    orig_map = {c["id"]: c.get("text") for c in orig.get("criteria", [])}
    cur_map = {c["id"]: c.get("text") for c in meta.get("criteria", [])}
    missing = [cid for cid in orig_map if cid not in cur_map]
    changed = [cid for cid in orig_map
               if cid in cur_map and cur_map[cid] != orig_map[cid]]
    if missing or changed:
        bits = []
        if missing:
            bits.append("удалены: " + ", ".join(missing))
        if changed:
            bits.append("изменён текст: " + ", ".join(changed))
        return False, "критерии подменены задним числом (" + "; ".join(bits) + ")"
    return True, ""


def check(project: str, task_id: str) -> dict:
    """Все машинные проверки разом. Тимлид гоняет их сам при приёмке, не
    доверяя отчёту исполнителя (раздел 3.3, п.7)."""
    meta = read_json(project, task_id)
    errors: list[str] = []
    checks: dict[str, bool] = {}

    lines = len(task_md_path(project, task_id).read_text(encoding="utf-8").splitlines())
    checks["task_md_lines_ok"] = lines <= TASK_MD_MAX_LINES
    if not checks["task_md_lines_ok"]:
        errors.append(f"task.md длиннее потолка: {lines} строк")

    unresolved = [c["id"] for c in meta["criteria"] if not c.get("passes")]
    checks["criteria_ok"] = not unresolved
    if unresolved:
        errors.append("критерии без прохода: " + ", ".join(unresolved))

    started = meta.get("started")
    started_dt = datetime.fromisoformat(started) if started else None
    artifacts = meta.get("artifacts") or []
    art_ok = bool(artifacts)
    if not artifacts:
        errors.append("артефакт не назван — задача не закрывается без "
                       "файла или ссылки, созданных в ходе этой задачи")
    for rel in artifacts:
        p = (C.ROOT / rel) if not Path(rel).is_absolute() else Path(rel)
        if not p.exists():
            art_ok = False
            errors.append(f"артефакт не найден на диске: {rel}")
            continue
        size = p.stat().st_size
        if size == 0:
            art_ok = False
            errors.append(f"артефакт нулевого размера: {rel}")
            continue
        if started_dt is not None:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=started_dt.tzinfo)
            if mtime < started_dt:
                art_ok = False
                errors.append(f"артефакт старше начала задачи: {rel}")
    checks["artifacts_ok"] = art_ok

    intact_ok, intact_note = _criteria_intact(project, task_id, meta)
    checks["criteria_intact_ok"] = intact_ok
    if not intact_ok:
        errors.append(intact_note)
    elif intact_note:
        errors.append(intact_note)     # информационная пометка, ok=True всё же

    ok = (checks["task_md_lines_ok"] and checks["criteria_ok"]
          and checks["artifacts_ok"] and checks["criteria_intact_ok"])
    return {"ok": ok, "checks": checks, "errors": errors}


# --------------------------------------------------------------------------
# Просрочка и сводка (раздел 3.2, 6, 15)
# --------------------------------------------------------------------------

def overdue(project: str) -> list[dict]:
    require_project(project)
    today = date.fromisoformat(_today_iso())
    out = []
    for tid in list_tasks(project):
        meta = read_json(project, tid)
        if meta.get("status") not in OPEN_STATUSES:
            continue
        due = meta.get("planned_due")
        if not due:
            continue
        try:
            d = date.fromisoformat(due)
        except ValueError:
            continue
        if d < today:
            days = (today - d).days
            out.append({
                "id": tid, "title": meta.get("title"),
                "planned_due": due, "status": meta.get("status"),
                "days": days,
                "reason": f"просрочено на {days} дн., статус «{meta.get('status')}»",
            })
    return out


def status(project: str) -> dict:
    require_project(project)
    tasks = [read_json(project, tid) for tid in list_tasks(project)]
    by_status: dict[str, list[str]] = {}
    for m in tasks:
        by_status.setdefault(m.get("status", "?"), []).append(m["id"])
    busy = {m["executor"]: m["id"] for m in tasks
            if m.get("status") in OPEN_STATUSES and m.get("executor")}
    stuck = overdue(project)
    return {
        "project": project,
        "всего_задач": len(tasks),
        "по_статусам": {k: len(v) for k, v in sorted(by_status.items())},
        "исполнители_заняты": busy,
        "просрочено": len(stuck),
        "просроченные": stuck,
    }
