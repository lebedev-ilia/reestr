"""Счётчик расхода по проекту (этап 0, раздел 11 и 15 документа
«Джарвис — команда и проекты»): токены и деньги за сутки и за неделю,
отдельно для каждого проекта.

Источник — `memory/jobs/<id>/job.json`. Поле `project` туда кладёт
`core/jobs.py` при постановке задания (submit(..., project=...)); у заданий
без проекта поле равно `""`, у заданий старше этой возможности поля нет
вовсе. Три состояния различаются НАМЕРЕННО, а не сводятся к «0»:

  - project == "vnutrennii"  → задание этого проекта, идёт в счёт;
  - project == "" (поле есть, но пусто) → задание сознательно без проекта;
  - поля "project" нет вовсе → задание старого формата, до появления метки.

Если у проекта за период НЕТ ни одного размеченного задания, `for_project()`
возвращает `has_labeled_jobs: False` — это не то же самое, что «потрачено
0.00»: либо проект ещё не запускал заданий, либо разметка ещё не дошла до
старых заданий. Смешивать эти два случая с настоящим нулём — то самое
«ноль вместо честного ‘без метки’», от которого предостерегает бриф.

Цена берётся из `job.json["cost_usd"]` — она уже прирост, а не накопленное
сессией (см. факт `cena-hoda-uchyot`: с 25.08 в job.json лежит финальная
цена задания, `LAST_RESULT` в `modules/jobs/run.py`). Двойного счёта, как в
логе (`self`-запись + `job_done`), здесь нет — job.json одна запись на
задание.

Незавершённое задание (`finished` пусто, значит `cost_usd` тоже) не входит
НИ В ОДИН бакет — его цена ещё не известна, а не равна нулю. Число таких
заданий возвращается отдельно (`unfinished`), чтобы не потерять из виду:
«сегодня потрачено $0.00» и «сегодня всё ещё считается» — разные сообщения.

    jarvis/.venv/bin/python -m jarvis.modules.projects.cli cost vnutrennii
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.core import config as C

# Модульная константа, читается заново при КАЖДОМ вызове (не захватывается в
# момент импорта) — тот же приём, что у modules/projects/tasks.PROJECTS_DIR:
# самотест подменяет именно её, а не core/config.py (правило
# samotest-ne-pishet-v-zhivuyu-pamyat, урок bl-118).
JOBS_DIR = C.MEM / "jobs"


def _now() -> datetime:
    return datetime.now(ZoneInfo(C.TZ))


def _today_iso() -> str:
    return _now().date().isoformat()


def _job_files(jobs_dir: Path):
    if not jobs_dir.exists():
        return
    for d in sorted(jobs_dir.iterdir()):
        p = d / "job.json"
        if p.is_file():
            yield p


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _job_date(meta: dict) -> str | None:
    """Дата, на которую относится цена задания: дата завершения — цена
    известна только когда job.json получил cost_usd, а не дата создания
    (задание могло стоять в очереди сутками)."""
    ts = meta.get("finished")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).date().isoformat()
    except ValueError:
        return None


def scan(jobs_dir: Path | None = None) -> dict:
    """Разбор всех job.json на строки {id, project, labeled, date, cost_usd,
    turns, sec, status} плюс три сводных счётчика: сколько заданий размечено
    хоть каким-то (не обязательно непустым) значением project, сколько без
    метки вовсе, сколько ещё не завершено."""
    jobs_dir = jobs_dir or JOBS_DIR
    rows: list[dict] = []
    labeled_field = 0     # поле "project" в job.json присутствует
    no_field = 0           # поля "project" нет вовсе — старый формат
    unfinished = 0
    for p in _job_files(jobs_dir):
        meta = _load(p)
        if not meta:
            continue
        has_field = "project" in meta
        if has_field:
            labeled_field += 1
        else:
            no_field += 1
        cost = meta.get("cost_usd")
        d = _job_date(meta)
        if cost is None or d is None:
            unfinished += 1
            continue
        rows.append({
            "id": meta.get("id"),
            "project": (meta.get("project") or "").strip() if has_field else None,
            "has_field": has_field,
            "date": d,
            "cost_usd": float(cost),
            "turns": int(meta.get("turns") or 0),
            "sec": float(meta.get("sec") or 0.0),
            "status": meta.get("status"),
        })
    return {"jobs": rows, "labeled_field": labeled_field,
            "no_field": no_field, "unfinished": unfinished}


def _bucket(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "turns": sum(r["turns"] for r in rows),
        "sec": round(sum(r["sec"] for r in rows), 1),
    }


def for_project(project: str, jobs_dir: Path | None = None) -> dict:
    """Расход конкретного проекта за сутки и за неделю (последние 7 дней,
    включая сегодня — тот же охват, что modules.audit.spend по умолчанию для
    периода)."""
    project = (project or "").strip()
    if not project:
        raise ValueError("нужно имя проекта")

    data = scan(jobs_dir)
    mine = [r for r in data["jobs"] if r["project"] == project]

    today = _today_iso()
    week_start = (_now().date() - timedelta(days=6)).isoformat()
    today_rows = [r for r in mine if r["date"] == today]
    week_rows = [r for r in mine if week_start <= r["date"] <= today]

    return {
        "project": project,
        "собрано": _now().isoformat(timespec="seconds"),
        "has_labeled_jobs": bool(mine),
        "labeled_field_total": data["labeled_field"],
        "no_field_total": data["no_field"],
        "unfinished_total": data["unfinished"],
        "сутки": {"дата": today, **_bucket(today_rows)},
        "неделя": {"с": week_start, "по": today, **_bucket(week_rows)},
    }


def render_text(row: dict) -> str:
    """Плоский русский текст без markdown и таблиц (правило tekst-v-vk)."""
    p = row["project"]
    lines = [f"Расход по проекту {p}."]
    if not row["has_labeled_jobs"]:
        lines.append(f"— заданий с меткой «{p}» пока нет")
        lines.append(f"— всего заданий с полем «проект»: {row['labeled_field_total']}")
        if row["no_field_total"]:
            lines.append(f"— заданий совсем без метки (старый формат): {row['no_field_total']}")
        if row["unfinished_total"]:
            lines.append(f"— ещё не завершено (цена неизвестна): {row['unfinished_total']}")
        return "\n".join(lines)

    s, w = row["сутки"], row["неделя"]
    lines.append(
        f"— сутки ({s['дата']}): заданий {s['n']}, ходов модели {s['turns']}, "
        f"${s['cost_usd']:.2f} справочно")
    lines.append(
        f"— неделя ({w['с']}–{w['по']}): заданий {w['n']}, ходов модели {w['turns']}, "
        f"${w['cost_usd']:.2f} справочно")
    if row["unfinished_total"]:
        lines.append(
            f"— не учтено (ещё не завершено, по всем проектам сразу): {row['unfinished_total']}")
    return "\n".join(lines)
