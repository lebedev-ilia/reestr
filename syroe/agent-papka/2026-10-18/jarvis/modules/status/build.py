"""Сборщик страницы состояния derived/status/index.html — этап 0, раздел 15
документа «Джарвис — команда и проекты», задача t-002.

Кодом, без модели и без обращения наружу: источники данных — уже готовые
словари других модулей, здесь ничего не пересчитывается заново:

  - modules.projects.tasks   — статусы задач, занятость исполнителей,
    просрочка (`status()`, `overdue()`, `list_tasks()`, `read_json()`);
  - modules.agents.watchdog  — живость постоянных ролей: время последнего
    следа работы (`last_evidence()`), список отслеживаемых (`watched_roles()`);
  - modules.agents.roles     — существует ли роль вообще (`exists()`).

Первый блок страницы (задача t-002 — «каркас») — «очередь и загрузка»: по
каждому проекту список задач со статусами, кто из исполнителей занят, что
просрочено, и живость тимлида проекта в читаемом виде времени, а не epoch-
числом. Второй и третий блоки (задача t-005) — id="tasks-detail" (задачи
подробно: id/title/status/executor и номер круга доработки, len(rounds)
буквально) и id="cost" (расход за сутки и неделю из
modules.projects.cost.for_project, честные "без метки"/"не завершено" вместо
нуля там, где данных нет).

Список проектов — перечисление подпапок memory/projects/ (кроме README.md),
а не хардкод 'vnutrennii': вторым проектом когда-нибудь станет полигон
клиента, и код не должен переписываться ради этого.

Тимлид проекта ищется не по жёсткому имени «teamlead-<project>», а по полю
task.json["teamlead"], которое тимлид пишет себе сам при создании первой
задачи (`tasks.create`) — это и есть самый честный источник имени роли.
Проект без единой задачи ещё не может себя назвать, поэтому резерв — то же
соглашение об имени, каким сам документ называет тимлида проекта.

Путь вывода — модульная константа OUT_DIR/OUT_FILE, читается заново при
каждом вызове `build()` (не захватывается копией при импорте) — тот же
приём, что у `tasks.PROJECTS_DIR` (см. её докстринг), чтобы свой самотест
мог подменить путь без побочных эффектов на живой derived/status/.

    cd "…/Agent"
    jarvis/.venv/bin/python -m jarvis.modules.status.cli build
"""

from __future__ import annotations

import html as H
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C  # noqa: E402
from jarvis.modules.agents import roles as R  # noqa: E402
from jarvis.modules.agents import watchdog as WD  # noqa: E402
from jarvis.modules.projects import cost as CO  # noqa: E402
from jarvis.modules.projects import tasks as T  # noqa: E402

OUT_DIR = C.DERIVED / "status"
OUT_FILE = OUT_DIR / "index.html"

NO_ROLE = "роль не заведена"
NOT_WATCHED = "не отслеживается (нет флага watchdog или расписания)"
NO_TRACE_YET = "следов работы ещё нет"


def _now_text() -> str:
    return datetime.now(ZoneInfo(C.TZ)).strftime("%Y-%m-%d %H:%M")


def _fmt_epoch(epoch: float) -> str:
    """Epoch → читаемое время по таймзоне Ильи. Не вызывать на 0.0/None —
    ноль mtime не время, а «следов нет», разбирается отдельно веткой выше."""
    return datetime.fromtimestamp(epoch, ZoneInfo(C.TZ)).strftime("%Y-%m-%d %H:%M")


def list_projects() -> list[str]:
    """Проекты — перечислением подпапок memory/projects/, не хардкодом."""
    base = T.PROJECTS_DIR
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def _teamlead_role(project: str) -> str:
    """Имя роли тимлида проекта — из task.json реальных задач.

    Свежие задачи важнее старых: у vnutrennii первая задача (t-001) заведена
    ещё самим Jarvis до того, как появилась роль teamlead-vnutrennii — её
    значение поля "teamlead" стало историческим мусором, а не текущим
    именем. Поэтому сначала ищем среди всех значений (от новых к старым) то,
    которое совпадает с реально заведённой ролью (`roles.exists`); не нашли
    ни одного зарегистрированного — берём значение самой свежей задачи как
    менее плохой вариант; задач нет вовсе — резерв по соглашению об имени
    `teamlead-<project>`."""
    values = [T.read_json(project, tid).get("teamlead")
              for tid in reversed(T.list_tasks(project))]
    values = [v for v in values if v]
    for v in values:
        if R.exists(v):
            return v
    if values:
        return values[0]
    return f"teamlead-{project}"


def _heartbeat(role: str) -> dict:
    """Живость роли — три исхода, а не один число-в-ноль на всё: роли нет,
    роль есть но сторож её не наблюдает, роль наблюдается но следа ещё не
    было, роль наблюдается и след есть."""
    if not R.exists(role):
        return {"role": role, "epoch": 0.0, "text": NO_ROLE, "watched": False}
    if role not in WD.watched_roles():
        return {"role": role, "epoch": 0.0, "text": NOT_WATCHED, "watched": False}
    epoch = WD.last_evidence(role)
    text = _fmt_epoch(epoch) if epoch else NO_TRACE_YET
    return {"role": role, "epoch": epoch, "text": text, "watched": True}


def project_queue(project: str) -> dict:
    """Блок «очередь и загрузка» одного проекта (раздел 15 документа)."""
    st = T.status(project)
    tasks = []
    for tid in T.list_tasks(project):
        meta = T.read_json(project, tid)
        tasks.append({
            "id": tid,
            "title": meta.get("title", ""),
            "status": meta.get("status", "?"),
            "executor": meta.get("executor", ""),
        })
    role = _teamlead_role(project)
    return {
        "project": project,
        "tasks": tasks,
        "по_статусам": st["по_статусам"],
        "исполнители_заняты": st["исполнители_заняты"],
        "просрочено": st["просрочено"],
        "просроченные": st["просроченные"],
        "teamlead": _heartbeat(role),
    }


def project_tasks_detail(project: str) -> list[dict]:
    """Подробный список задач проекта (задача t-005): id/title/status/
    executor и номер круга доработки — буквально len(rounds), без своего
    текста поверх (уточнение тимлида, заход job-152)."""
    out = []
    for tid in T.list_tasks(project):
        meta = T.read_json(project, tid)
        out.append({
            "id": tid,
            "title": meta.get("title", ""),
            "status": meta.get("status", "?"),
            "executor": meta.get("executor", ""),
            "round": len(meta.get("rounds") or []),
        })
    return out


def project_cost(project: str) -> dict:
    """Расход проекта за сутки и неделю — без пересчёта, напрямую то, что
    вернул modules.projects.cost.for_project (там же честные «без метки»/
    «не завершено» вместо нуля, здесь только рендер)."""
    return CO.for_project(project)


def collect() -> dict:
    """Все данные страницы: очередь/загрузка (t-002), задачи подробно и
    расход по проекту (t-005)."""
    projects = list_projects()
    return {
        "собрано": _now_text(),
        "проекты": [project_queue(p) for p in projects],
        "tasks_detail": [{"project": p, "tasks": project_tasks_detail(p)}
                          for p in projects],
        "cost": [project_cost(p) for p in projects],
    }


def render_html(data: dict) -> str:
    """Плоский html без внешних стилей и скриптов — своя страница, свой
    источник; экранирование текста обязательно (заголовки задач — свободный
    текст тимлида, а не доверенная разметка)."""
    p = []
    p.append("<!doctype html>")
    p.append('<html lang="ru"><head><meta charset="utf-8">')
    p.append("<title>Джарвис — состояние</title></head><body>")
    p.append(f"<p>Собрано: {H.escape(data['собрано'])}</p>")

    p.append('<section id="queue">')
    p.append("<h2>Очередь и загрузка</h2>")
    for proj in data["проекты"]:
        p.append(f'<article data-project="{H.escape(proj["project"])}">')
        p.append(f'<h3>{H.escape(proj["project"])}</h3>')
        p.append("<ul>")
        if proj["tasks"]:
            for t in proj["tasks"]:
                p.append(
                    f'<li data-task-id="{H.escape(t["id"])}" '
                    f'data-status="{H.escape(t["status"])}">'
                    f'{H.escape(t["id"])} — {H.escape(t["title"])} — '
                    f'<span class="status">{H.escape(t["status"])}</span></li>')
        else:
            p.append("<li>задач нет</li>")
        p.append("</ul>")
        busy = ", ".join(f"{H.escape(k)}: {H.escape(v)}"
                          for k, v in proj["исполнители_заняты"].items()) or "никто"
        p.append(f"<p>Заняты: {busy}</p>")
        p.append(f"<p>Просрочено: {proj['просрочено']}</p>")
        p.append("</article>")
    if not data["проекты"]:
        p.append("<p>проектов нет</p>")
    p.append("</section>")

    p.append('<section id="teamlead-heartbeat">')
    p.append("<h2>Живость тимлидов</h2>")
    p.append("<ul>")
    for proj in data["проекты"]:
        tl = proj["teamlead"]
        p.append(
            f'<li data-project="{H.escape(proj["project"])}" '
            f'data-role="{H.escape(tl["role"])}">'
            f'{H.escape(proj["project"])} ({H.escape(tl["role"])}): '
            f'{H.escape(tl["text"])}</li>')
    if not data["проекты"]:
        p.append("<li>проектов нет</li>")
    p.append("</ul>")
    p.append("</section>")

    p.append('<section id="tasks-detail">')
    p.append("<h2>Задачи подробно</h2>")
    for proj in data["tasks_detail"]:
        p.append(f'<article data-project="{H.escape(proj["project"])}">')
        p.append(f'<h3>{H.escape(proj["project"])}</h3>')
        if proj["tasks"]:
            p.append("<table>")
            p.append("<tr><th>id</th><th>title</th><th>status</th>"
                      "<th>executor</th><th>круг</th></tr>")
            for t in proj["tasks"]:
                p.append(
                    f'<tr data-task-id="{H.escape(t["id"])}" '
                    f'data-status="{H.escape(t["status"])}" '
                    f'data-round="{t["round"]}">'
                    f'<td>{H.escape(t["id"])}</td>'
                    f'<td>{H.escape(t["title"])}</td>'
                    f'<td>{H.escape(t["status"])}</td>'
                    f'<td>{H.escape(t["executor"])}</td>'
                    f'<td>{t["round"]}</td></tr>')
            p.append("</table>")
        else:
            p.append("<p>задач нет</p>")
        p.append("</article>")
    if not data["tasks_detail"]:
        p.append("<p>проектов нет</p>")
    p.append("</section>")

    p.append('<section id="cost">')
    p.append("<h2>Расход по проектам</h2>")
    for row in data["cost"]:
        p.append(f'<article data-project="{H.escape(row["project"])}">')
        p.append(f'<h3>{H.escape(row["project"])}</h3>')
        if not row["has_labeled_jobs"]:
            p.append(
                '<p data-cost="no-label">без метки — заданий с меткой '
                f'«{H.escape(row["project"])}» пока нет</p>')
            if row["unfinished_total"]:
                p.append(
                    '<p data-cost="unfinished">не завершено (по всем '
                    f'проектам сразу, цена ещё неизвестна): '
                    f'{row["unfinished_total"]}</p>')
        else:
            s, w = row["сутки"], row["неделя"]
            p.append(
                f'<p data-period="сутки">сутки ({H.escape(s["дата"])}): '
                f'заданий {s["n"]}, ходов {s["turns"]}, '
                f'${s["cost_usd"]:.2f} справочно</p>')
            p.append(
                f'<p data-period="неделя">неделя ({H.escape(w["с"])}–'
                f'{H.escape(w["по"])}): заданий {w["n"]}, ходов {w["turns"]}, '
                f'${w["cost_usd"]:.2f} справочно</p>')
            if row["unfinished_total"]:
                p.append(
                    '<p data-cost="unfinished">не завершено (по всем '
                    f'проектам сразу, цена ещё неизвестна): '
                    f'{row["unfinished_total"]}</p>')
        p.append("</article>")
    if not data["cost"]:
        p.append("<p>проектов нет</p>")
    p.append("</section>")

    p.append("</body></html>")
    return "\n".join(p)


def build() -> Path:
    """Собрать данные, отрендерить и записать атомарно (временный файл,
    потом переименование — тот же приём, что в jobs.py и tasks.py: половинчатая
    страница не должна ни секунды выглядеть готовой)."""
    data = collect()
    text = render_html(data)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_suffix(".html.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, OUT_FILE)
    return OUT_FILE
