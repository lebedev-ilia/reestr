"""Сводка по проекту — формат раздела 7 документа «Джарвис — команда и
проекты», семь пунктов:

  1. сколько задач сделано
  2. сколько не сделано, но заведено
  3. что получилось по задачам — по пунктам, кратко
  4. что не получилось
  5. просроченное — отдельным пунктом с причиной
  6. прогноз на следующую сводку
  7. вопросы к Илье — по пунктам

«Цифры собирает код» (раздел 7) — весь этот модуль. Формулирует и группирует
дешёвая модель (haiku) — по документу это необязательный слой ПОВЕРХ, а не
замена: если её нет или она отказала, `render_text()` всё равно строит
плоский русский текст без единого обращения к модели. Вызов haiku в этой
версии не подключён — модуль ограничен кодовым слоем (см. result.md
задания, где это названо прямо).

Источники данных, без единого захардкоженного числа:
  - `modules.projects.tasks` — статусы, критерии, круги доработки, просрочка
    (все данные приходят из `memory/projects/<проект>/tasks/*/task.json`);
  - `memory/projects/<проект>/obligations.md` — вопросы к Илье, тот же
    построчный формат «- [id] текст — исполнитель — срок ГГГГ-ММ-ДД —
    статус, переносов: N», что и в общей памяти (см. `memory/obligations.md`
    в системном промпте); пункт становится «вопросом к Илье», если
    исполнитель — ilya и статус open/overdue.

Пустой проект (задач ещё нет) не роняет сборку — все семь пунктов просто
приходят нулями и пустыми списками, это и есть «честные нули» из критерия
приёмки.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.core import config as C
from jarvis.modules.agents import roles as AR
from jarvis.modules.agents import watchdog as WD

from . import tasks as T

# Тот же построчный формат, что у memory/obligations.md — см. mcp__memory__
# add_obligation. Текст — нежадный кусок между "] " и первым " — ", owner —
# одно слово (без пробелов) перед следующим " — срок ".
OBLIGATION_RE = re.compile(
    r"^- \[(?P<id>[^\]]+)\]\s*(?P<text>.+?)\s*—\s*(?P<owner>\S+)\s*—\s*"
    r"срок\s*(?P<due>\d{4}-\d{2}-\d{2})\s*—\s*(?P<status>[а-яa-z_]+)",
    re.MULTILINE)

FORECAST_STATUSES = ("created", "in_progress", "submitted", "rework")


def _now() -> datetime:
    return datetime.now(ZoneInfo(C.TZ))


def _obligations_path(project: str):
    # Через T.project_dir(), а не напрямую C.MEM/"projects"/... — этот путь
    # обязан следовать за T.PROJECTS_DIR (самотест подменяет именно её),
    # иначе фикстура пишет в один каталог, а модуль читает из другого.
    return T.project_dir(project) / "obligations.md"


def read_obligations(project: str) -> list[dict]:
    path = _obligations_path(project)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [m.groupdict() for m in OBLIGATION_RE.finditer(text)]


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, ZoneInfo(C.TZ)).strftime("%Y-%m-%d %H:%M")


def watchdog_notes(project: str) -> list[dict]:
    """Роли этого проекта, которых сторож (modules/agents/watchdog.py) хоть
    раз поднимал заново. Пусто — сторожу не пришлось никого будить.

    Роли проекта опознаются по явному полю project в их ROLE.yaml
    (roles.roles_for_project) — тому же источнику, что даёт метку job.json
    (пункт 9 приёмки этапа 0). Так подъём тимлида виден в его же сводке, а не
    только в файле derived/agents_watchdog.json (пункт 7)."""
    out = []
    for role in AR.roles_for_project(project):
        row = WD.role_state(role)
        if row:
            out.append(row)
    return out


def collect(project: str) -> dict:
    """Все семь пунктов как данные (словарь/JSON). Ни одно число не взято «на
    глаз» — источник каждого назван в докстринге модуля и в result.md."""
    T.require_project(project)
    ids = T.list_tasks(project)
    all_tasks = [T.read_json(project, tid) for tid in ids]

    done = [t for t in all_tasks if t.get("status") == "done"]
    open_tasks = [t for t in all_tasks if t.get("status") in T.OPEN_STATUSES]
    rework = [t for t in all_tasks if t.get("status") == "rework"]
    overdue = T.overdue(project)

    forecast = sorted(
        (t for t in all_tasks
         if t.get("status") in FORECAST_STATUSES and t.get("planned_due")),
        key=lambda t: t["planned_due"])

    obligations = read_obligations(project)
    questions = [o for o in obligations
                 if o["owner"] == "ilya" and o["status"] in ("open", "overdue")]

    return {
        "project": project,
        "собрано": _now().isoformat(timespec="seconds"),
        # 1. сколько задач сделано
        "сделано": {
            "n": len(done),
            "задачи": [{"id": t["id"], "title": t["title"]} for t in done],
        },
        # 2. сколько не сделано, но заведено
        "не_сделано_заведено": {
            "n": len(open_tasks),
            "задачи": [{"id": t["id"], "title": t["title"], "статус": t["status"]}
                       for t in open_tasks],
        },
        # 3. что получилось по задачам — по пунктам, кратко
        "получилось": [
            {"id": t["id"], "title": t["title"],
             "критерии": f"{sum(1 for c in t['criteria'] if c.get('passes'))}/"
                         f"{len(t['criteria'])}",
             "артефакты": list(t.get("artifacts") or [])}
            for t in done],
        # 4. что не получилось
        "не_получилось": [
            {"id": t["id"], "title": t["title"],
             "круг": len(t.get("rounds") or []),
             "пункты": list((t["rounds"][-1].get("points") or [])
                            if t.get("rounds") else [])}
            for t in rework],
        # 5. просроченное — отдельным пунктом с причиной (T.overdue уже
        # несёт "reason" по каждой задаче)
        "просрочено": overdue,
        # 6. прогноз на следующую сводку — что в работе и к какому сроку
        "прогноз": [
            {"id": t["id"], "title": t["title"], "статус": t["status"],
             "срок": t["planned_due"]} for t in forecast],
        # 7. вопросы к Илье — по пунктам, с временем ожидания в исходном тексте
        "вопросы": [
            {"id": q["id"], "text": q["text"], "срок": q["due"],
             "статус": q["status"]} for q in questions],
        # Вне семи пунктов раздела 7 — не заменяет их, а дополняет (раздел 16
        # документа, пункт 7 приёмки этапа 0): подъём сторожем виден здесь,
        # а не только в derived/agents_watchdog.json.
        "сторож": watchdog_notes(project),
    }


def render_text(data: dict) -> str:
    """Плоский русский текст без markdown и таблиц (правило tekst-v-vk):
    короткие абзацы, списки через «— »."""
    L: list[str] = []
    add = L.append
    p = data["project"]

    add(f"Сводка по проекту {p}.")
    add("")

    add(f"Сделано задач: {data['сделано']['n']}.")
    for t in data["сделано"]["задачи"]:
        add(f"— {t['id']}: {t['title']}")
    add("")

    add(f"Не сделано, но заведено: {data['не_сделано_заведено']['n']}.")
    for t in data["не_сделано_заведено"]["задачи"]:
        add(f"— {t['id']}: {t['title']} ({t['статус']})")
    add("")

    if data["получилось"]:
        add("Что получилось:")
        for t in data["получилось"]:
            add(f"— {t['id']}: {t['title']}, критерии {t['критерии']}")
    else:
        add("Что получилось: пока ничего.")
    add("")

    if data["не_получилось"]:
        add("Что не получилось:")
        for t in data["не_получилось"]:
            pts = "; ".join(t["пункты"]) if t["пункты"] else "без пунктов"
            add(f"— {t['id']}: {t['title']}, круг {t['круг']}, {pts}")
    else:
        add("Что не получилось: ничего на доработке.")
    add("")

    if data["просрочено"]:
        add("Просрочено:")
        for t in data["просрочено"]:
            add(f"— {t['id']}: {t['title']}, {t['reason']}")
    else:
        add("Просрочено: ничего.")
    add("")

    if data["прогноз"]:
        add("Прогноз на следующую сводку:")
        for t in data["прогноз"]:
            add(f"— {t['id']}: {t['title']} ({t['статус']}), срок {t['срок']}")
    else:
        add("Прогноз: в работе ничего не заведено.")
    add("")

    if data["вопросы"]:
        add("Вопросы к Илье:")
        for q in data["вопросы"]:
            add(f"— {q['text']} (срок {q['срок']}, {q['статус']})")
    else:
        add("Вопросы к Илье: нет.")

    if data.get("сторож"):
        add("")
        add("Сторож поднимал заново:")
        for w in data["сторож"]:
            flag = ", второй подъём за час" if w["pending_report"] else ""
            add(f"— {w['role']}: {w['revivals']} раз в текущем часовом окне, "
                f"последний — {_fmt_ts(w['last_ts'])}{flag}")

    return "\n".join(L)
