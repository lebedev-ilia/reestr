"""Расписание постоянных ролей: кто поднимается сам и как часто.

Роль с блоком `schedule` в ROLE.yaml (например критик — раз в час) не ждёт,
пока её позовут: демон ВК на каждом такте зовёт tick(), и тот ставит задание,
когда время пришло. Сравнивается не «сейчас ровно час», а «сколько прошло с
прошлого захода» — иначе пропущенный такт (демон был занят ходом, машина
спала) отменял бы заход целиком.

Состояние — derived/agents_schedule.json: только время последних запусков,
восстанавливается само (правило v-derived-tolko-vosstanovimoe).

Дороговизна такта: чтение одного мелкого json и ROLE.yaml каждой роли. Ходов
не запускает — только submit, а он возвращает управление сразу.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C, jobs as J, memory as M  # noqa: E402
from jarvis.modules.agents import roles as R  # noqa: E402

STATE = C.DERIVED / "agents_schedule.json"
HEALTH = C.DERIVED / "agents_health.json"

# Пропуск больше этого — заход не догоняется, а переносится на следующий раз.
# Смысл тот же, что у ночного цикла: догонять двенадцать пропущенных разборов
# подряд бессмысленно, разбирать надо свежее.
MAX_CATCHUP = 3.0

# Срывы постановки задания (bl-093). tick() ловит ЛЮБУЮ ошибку submit
# (ThinBrief, DuplicateWork, сбой), пишет строку в лог и двигает часы роли
# дальше — снаружи не видно ничего, роль тихо перестаёт подниматься. Первый
# срыв — обычное дело (занята очередь, дубль работы). Со второго подряд это
# уже поломка, и она должна выйти из лога: Илье строкой (ночью — в утренний
# пакет) и критику — припиской в его брифе.
FAIL_ALERT_AT = 2
FAIL_ALERT_REPEAT = 6 * 3600      # повторно напоминать не чаще раза в 6 часов

# Пропуск по тишине (bl-113). Роль с skip_if_quiet в плане не поднимается,
# если с прошлого захода не было ни коммитов, ни значащих строк в логе. Ночью
# 27.08 критик дважды подряд отработал впустую: разбирать было нечего, а заход
# стоит 0,4–0,7 доллара и полторы-три минуты.
#
# Что НЕ считается работой. Пульс демона (vk_alive) — это признак жизни, а не
# событие. Строки самого планировщика (role_scheduled, job_submit, quiet_skip)
# — его собственная бухгалтерия: если считать их работой, роль увидит свой же
# прошлый заход и не пропустит никогда. По той же причине из подсчёта выкинут
# весь след прошлого задания САМОЙ роли — он опознаётся по pid воркера.
QUIET_IGNORE_KEYS = {"vk_alive", "role_scheduled", "job_submit", "quiet_skip"}


def _read() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def _last(state: dict, role: str) -> float:
    """Время прошлого захода роли. Старый формат — голое число, новый — запись."""
    v = state.get(role)
    if isinstance(v, dict):
        v = v.get("ts")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _last_job(state: dict, role: str) -> str:
    v = state.get(role)
    return str((v or {}).get("job") or "") if isinstance(v, dict) else ""


def _stamp(state: dict, role: str, now: float, job: str | None = None) -> None:
    """Двинуть часы роли. job запоминается, чтобы потом не считать её же след."""
    state[role] = {"ts": float(now), "job": job or _last_job(state, role) or None}


def _health_read() -> dict:
    try:
        return json.loads(HEALTH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _health_write(data: dict) -> None:
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def record_ok(role: str) -> None:
    """Заход поставился — счётчик срывов роли обнуляется."""
    data = _health_read()
    if data.pop(role, None) is not None:
        _health_write(data)


def record_fail(role: str, err: str, now: float) -> dict:
    """Заход не поставился. Вернуть запись роли (в ней n — срывов подряд).

    Отдельно возвращает признак alert: пора сказать наружу, а не только в лог.
    """
    data = _health_read()
    row = data.get(role) or {}
    row["n"] = int(row.get("n") or 0) + 1
    row["err"] = err[:300]
    row["ts"] = now
    alert = False
    if row["n"] >= FAIL_ALERT_AT:
        last = float(row.get("alerted") or 0)
        if now - last >= FAIL_ALERT_REPEAT:
            row["alerted"] = now
            alert = True
    row["alert"] = alert
    data[role] = {k: v for k, v in row.items() if k != "alert"}
    _health_write(data)
    return row


def health() -> list[dict]:
    """Роли, которые срываются подряд (n >= FAIL_ALERT_AT). Пусто — всё живо."""
    out = []
    for role, row in (_health_read() or {}).items():
        if int((row or {}).get("n") or 0) >= FAIL_ALERT_AT:
            out.append({"role": role, **row})
    return sorted(out, key=lambda r: -int(r.get("n") or 0))


def health_note() -> str:
    """Одна строка про сорванные роли — для брифа критика и утреннего отчёта."""
    bad = health()
    if not bad:
        return ""
    parts = [f"{r['role']}: {r['n']} заходов подряд, последняя ошибка — "
             f"{r.get('err', '?')}" for r in bad]
    return "Роли не поднимаются по расписанию. " + "; ".join(parts)


def _commits_since(ts: float) -> int:
    """Сколько коммитов в репозитории агента после ts. Гит не ответил — -1.

    -1 читается как «не знаю», и тишиной это не считается: молчать из-за
    сломанного гита хуже, чем лишний раз поднять роль.
    """
    try:
        r = subprocess.run(
            ["git", "log", f"--since=@{int(ts)}", "--format=%H"],
            cwd=C.ROOT, capture_output=True, text=True, timeout=20)
    except Exception:                                          # noqa: BLE001
        return -1
    if r.returncode != 0:
        return -1
    return len(r.stdout.split())


def _significant(ev: dict, skip_pids: set) -> bool:
    """Значащая ли строка лога: работа, а не пульс и не своя же бухгалтерия."""
    if ev.get("pid") in skip_pids:
        return False
    if ev.get("type") != "self":
        return True
    body = ev.get("content")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return True
    if not isinstance(body, dict):
        return True
    return any(k not in QUIET_IGNORE_KEYS for k in body)


def _log_activity_since(ts: float, skip_pids=()) -> int:
    """Значащих строк живого лога после ts. Читает только дни попавшие в окно."""
    skip = {p for p in skip_pids if p}
    n = 0
    day = date.fromtimestamp(ts)
    last_day = M.today()
    while day <= last_day:
        path = M.log_dir() / f"{day.isoformat()}.jsonl"
        day += timedelta(days=1)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            try:
                when = datetime.fromisoformat(ev.get("ts") or "").timestamp()
            except ValueError:
                continue
            if when <= ts:
                continue
            if _significant(ev, skip):
                n += 1
    return n


def _job_pid(job_id: str) -> int | None:
    if not job_id:
        return None
    try:
        return (J.read(job_id) or {}).get("pid")
    except Exception:                                          # noqa: BLE001
        return None


def quiet_since(last: float, now: float, skip_pids=()) -> dict:
    """Было ли тихо с last по now: ни коммитов, ни значащих строк лога."""
    commits = _commits_since(last)
    rows = _log_activity_since(last, skip_pids) if commits == 0 else -1
    return {"quiet": commits == 0 and rows == 0,
            "commits": commits, "log": rows}


def _busy(role: str) -> bool:
    """У роли уже идёт задание? Второе такое же не нужно."""
    for meta in J.by_status(*J.LIVE):
        if (meta.get("title") or "").startswith(f"{role}:"):
            return True
    return False


def _shout(text: str) -> None:
    """Сказать Илье. Ночью ляжет в очередь и уйдёт утром одним пакетом.

    Импорт внутри: планировщик не должен зависеть от канала ВК, а сбой
    отправки не должен ронять такт демона.
    """
    M.log("self", {"role_schedule_stuck": text[:300]})
    try:
        from jarvis.modules.vk import notify as N
        # Доклад роли — содержание, а не пульс: тип message_out (bl-077).
        N.notify(text, kind="message_out")
    except Exception as e:                                     # noqa: BLE001
        M.log("error", {"role_schedule_shout": f"{type(e).__name__}: {e}"})


def due(now: float | None = None) -> list[str]:
    """Роли, которым пора работать по расписанию."""
    now = now if now is not None else time.time()
    state = _read()
    out = []
    for role in R.roles():
        try:
            cfg = R.load(role)
        except Exception:
            continue
        plan = cfg.get("schedule") or {}
        minutes = float(plan.get("every_minutes") or 0)
        if minutes <= 0:
            continue
        last = _last(state, role)
        if last and now - last < minutes * 60:
            continue
        out.append(role)
    return out


def tick(now: float | None = None) -> list[str]:
    """Такт демона: поставить задания тем ролям, которым пора. Вернуть их id."""
    now = now if now is not None else time.time()
    started = []
    state = _read()
    for role in due(now):
        if J.capacity() <= 0 or _busy(role):
            continue
        cfg = R.load(role)
        plan = cfg.get("schedule") or {}
        task = (plan.get("task") or "").strip()
        if not task:
            continue
        mode = plan.get("mode") or "work"
        last = _last(state, role)
        # Пропуск по тишине (bl-113). Проверяется до сборки брифа: если
        # разбирать нечего, дешевле не поднимать задание вовсе. Часы роли при
        # этом двигаются — иначе проверка повторялась бы каждым тактом.
        if last and plan.get("skip_if_quiet"):
            q = quiet_since(last, now, skip_pids=(_job_pid(_last_job(state, role)),))
            if q["quiet"]:
                M.log("self", {"quiet_skip": role,
                               "hours": round((now - last) / 3600, 1)})
                _stamp(state, role, now)
                continue
        since = ("с прошлого твоего захода"
                 if last else "за всё время, что есть в логе и в гите")
        hours = (now - last) / 3600 if last else 0
        if last and hours > MAX_CATCHUP:
            since = f"за последние {MAX_CATCHUP:.0f} часа (пропуск дольше не догоняем)"
        brief_task = f"{task}\n\nПериод разбора: {since}."
        # Роль-сторож (schedule.report_health) узнаёт о сорванных заходах
        # соседей: иначе поломка расписания видна только в логе, а смотреть
        # лог руками никто не обещал.
        if plan.get("report_health"):
            note = health_note()
            if note:
                brief_task += ("\n\nОтдельно проверь и назови в находках: "
                               + note)

        brief, params = R.build_brief(role, brief_task, mode=mode)
        try:
            job_id = J.submit(
                brief, title=f"{role}:{mode} по расписанию",
                model=params["model"], max_turns=params["max_turns"],
                timeout=params["timeout"], origin="schedule",
                claim_text=f"{role} {mode} по расписанию {int(now // 3600)}",
                zone=(["/tmp"] if mode == "answer" else R.zone(role)))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            row = record_fail(role, err, now)
            M.log("error", {"role_schedule": role, "err": err,
                            "fails_in_row": row["n"]})
            if row.get("alert"):
                _shout(f"Роль {role} не поднимается по расписанию: "
                       f"{row['n']} заходов подряд сорвались. Последняя "
                       f"ошибка — {err[:200]}")
            _stamp(state, role, now)  # не долбиться каждым тактом
            continue
        record_ok(role)
        _stamp(state, role, now, job=job_id)
        started.append(job_id)
        M.log("self", {"role_scheduled": role, "job": job_id, "mode": mode})
    if started or state != _read():
        _write(state)
    return started


if __name__ == "__main__":
    print(tick())
