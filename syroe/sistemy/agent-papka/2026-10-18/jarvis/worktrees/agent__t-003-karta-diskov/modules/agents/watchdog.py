"""Сторож постоянных ролей (раздел 16 документа «Джарвис — команда и проекты»).

Тимлид проекта — «постоянный процесс» (раздел 2), но живёт он тем же
механизмом, что и остальные роли (modules/agents/schedule.py): поднимается по
расписанию диспетчером заданий, а не отдельным бесконечным процессом ОС.
Значит он может «упасть или зациклиться» ровно так же тихо — заход не
поставился, поставился и завис, или расписание само перестало его звать по
никем не замеченному багу — и снаружи все три беды выглядят одинаково: тишина.

Этот модуль — НЕЗАВИСИМАЯ от schedule.py проверка. Он не верит собственной
бухгалтерии планировщика (там тоже может быть тихая ошибка — сам планировщик
уже один раз молчал по такой причине, см. bl-093/bl-113 в
memory/facts/agenty-postoyannye-roli.md), а смотрит на СЛЕД РАБОТЫ: время
последней правки любого файла в зоне роли (`owns` из её ROLE.yaml). Роль
обязана оставлять этот след каждым своим действием — для тимлида это прямо
прописано в промпте (правка STATUS.md или journal/ в каждом заходе, даже
пустом). Нет следа дольше трёх периодов расписания — роль считается мёртвой,
поднимается заново отдельным заданием в обход обычного due()/tick(), а факт
идёт в лог. Второй такой подъём в пределах часа — не случайность, и это
помечается в состоянии для дневной сводки. Само сообщение Илье отсюда НЕ
уходит: у модуля нет для этого никакого канала, отправка (и уважение к
ночной тишине, правило nochnaya-tishina) — дело вызывающей стороны, которая
однажды прочитает pending_report().

Роль включает себе слежение сама, явным флагом `watchdog: true` в ROLE.yaml —
без него роль в watched_roles() не попадает и её никто не поднимает силой.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C, jobs as J, memory as M  # noqa: E402
from jarvis.core import heartbeat as HB  # noqa: E402
from jarvis.modules.agents import roles as R  # noqa: E402

STATE = C.DERIVED / "agents_watchdog.json"

# Столько периодов расписания без следа работы — роль считается мёртвой.
# Раздел 16 документа называет это число прямо: «отметки нет дольше трёх
# периодов».
DEAD_AFTER_PERIODS = 3

# Второй подъём в пределах этого окна — уже не случайность, а системный
# сигнал (раздел 16: «второй подъём в течение часа»).
REVIVE_WINDOW = 3600.0


def _read() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def _period_seconds(role: str, cfg: dict | None = None) -> float:
    """Период расписания роли в секундах. 0 — расписания нет, не наблюдаем."""
    cfg = cfg if cfg is not None else R.load(role)
    plan = cfg.get("schedule") or {}
    minutes = float(plan.get("every_minutes") or 0)
    return minutes * 60.0 if minutes > 0 else 0.0


def _zone_paths(role: str, cfg: dict | None = None) -> list[Path]:
    cfg = cfg if cfg is not None else R.load(role)
    return [C.ROOT / p for p in (cfg.get("owns") or [])]


def last_evidence_paths(paths: list[Path]) -> float:
    """Самая свежая mtime среди файлов по списку путей (файлы и каталоги).

    0.0 — следов нет вовсе: путей не существует или они пусты. Вынесено из
    last_evidence() отдельной чистой функцией — самотест проверяет её на
    голых временных файлах, без единой роли и без ROLE.yaml.
    """
    best = 0.0
    for base in paths:
        try:
            if base.is_file():
                files = [base]
            elif base.is_dir():
                files = [p for p in base.rglob("*") if p.is_file()]
            else:
                continue
        except OSError:
            continue
        for p in files:
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > best:
                best = m
    return best


def last_evidence(role: str, cfg: dict | None = None) -> float:
    """Время последнего следа работы роли — максимум mtime по её зоне owns."""
    cfg = cfg if cfg is not None else R.load(role)
    return last_evidence_paths(_zone_paths(role, cfg))


def watched_roles() -> list[str]:
    """Роли с явным флагом watchdog: true и настроенным расписанием."""
    out = []
    for role in R.roles():
        try:
            cfg = R.load(role)
        except Exception:                                        # noqa: BLE001
            continue
        if cfg.get("watchdog") and _period_seconds(role, cfg) > 0:
            out.append(role)
    return out


def _busy(role: str) -> bool:
    """У роли уже идёт задание? Насильно поднимать вторую копию нельзя.

    Своя копия проверки modules/agents/schedule.py._busy — модули друг от
    друга независимы намеренно: сторож не должен переставать работать, если
    в schedule.py однажды заведётся баг именно в этой функции.
    """
    for meta in J.by_status(*J.LIVE):
        if (meta.get("title") or "").startswith(f"{role}:"):
            return True
    return False


def is_dead(role: str, now: float | None = None, cfg: dict | None = None) -> bool:
    """Роль мертва: расписание есть, след был хоть раз, но устарел.

    Роль, которая ещё ни разу не оставила следа (ev <= 0 — только что
    заведена, до первого захода), мёртвой не считается: первый заход — забота
    due()/tick() в schedule.py, а не наша.
    """
    now = now if now is not None else time.time()
    cfg = cfg if cfg is not None else R.load(role)
    period = _period_seconds(role, cfg)
    if period <= 0:
        return False
    ev = last_evidence(role, cfg)
    if ev <= 0:
        return False
    return (now - ev) > DEAD_AFTER_PERIODS * period


def revive(role: str, now: float | None = None) -> dict | None:
    """Поднять роль заново отдельным заданием, в обход обычного расписания.

    None — поднимать не стали: роль занята (значит НЕ мертва, просто долго
    работает или зависла — зависший процесс снимет свой же таймаут, см.
    core/jobs.py:reap()) либо у неё нет задачи расписания вовсе.
    """
    now = now if now is not None else time.time()
    if _busy(role):
        return None
    cfg = R.load(role)
    plan = cfg.get("schedule") or {}
    task = (plan.get("task") or "").strip()
    if not task:
        return None
    mode = plan.get("mode") or "work"
    brief_task = (
        f"{task}\n\nСторож поднял тебя заново (modules/agents/watchdog.py): "
        f"следа твоей работы не было дольше {DEAD_AFTER_PERIODS} периодов "
        "расписания подряд. Не полагайся на память о прошлом заходе, которого,"
        " возможно, и не было толком — пройди ритуал начала работы заново.")
    brief, params = R.build_brief(role, brief_task, mode=mode)
    job_id = J.submit(
        brief, title=f"{role}:{mode} подъём сторожем",
        model=params["model"], max_turns=params["max_turns"],
        timeout=params["timeout"], origin="watchdog",
        # Метка времени в claim_text — иначе второй подъём той же роли в тот
        # же час отобьётся дедупом как «уже делается» (реестр занятой работы
        # видит именно эту же задачу расписания одной сутью).
        claim_text=f"{role} {mode} подъём сторожем {int(now)}",
        zone=(["/tmp"] if mode == "answer" else R.zone(role)))
    data = _read()
    row = data.get(role) or {}
    revivals = [t for t in (row.get("revivals") or []) if now - t < REVIVE_WINDOW]
    revivals.append(now)
    row["revivals"] = revivals
    row["last_job"] = job_id
    row["last_ts"] = now
    row["pending_report"] = len(revivals) >= 2
    data[role] = row
    _write(data)
    M.log("self", {"role_watchdog_revive": role, "job": job_id,
                   "revivals_in_window": len(revivals)})
    return {"role": role, "job": job_id, "revivals": len(revivals),
            "pending_report": row["pending_report"]}


def pending_report() -> list[dict]:
    """Роли со вторым (и далее) подъёмом за последний час.

    Только пометка — отправка наружу и уважение к ночной тишине (правило
    nochnaya-tishina) не дело этого модуля: у него нет канала связи вовсе.
    """
    out = []
    for role, row in (_read() or {}).items():
        if row.get("pending_report"):
            out.append({"role": role,
                        "revivals": len(row.get("revivals") or []),
                        "last_job": row.get("last_job"),
                        "last_ts": row.get("last_ts")})
    return out


def clear_report(role: str) -> None:
    """Пометка обработана (доклад ушёл или решили, что не нужен)."""
    data = _read()
    if role in data and data[role].get("pending_report"):
        data[role]["pending_report"] = False
        _write(data)


def should_announce(now=None) -> bool:
    """Можно ли сказать про находки сторожа прямо сейчас — не ночь ли.

    Сам сторож ничего не говорит; это готовый ответ для будущего кода сводок
    (раздел 15 документа, ещё не построен), чтобы решение «не ночь» не
    писалось заново в каждом месте, которое когда-нибудь прочитает
    pending_report().
    """
    return not HB.quiet_now(now)


def tick(now: float | None = None) -> list[dict]:
    """Такт демона: поднять всех мёртвых наблюдаемых ролей. Вернуть подъёмы."""
    now = now if now is not None else time.time()
    revived = []
    for role in watched_roles():
        if is_dead(role, now):
            row = revive(role, now)
            if row:
                revived.append(row)
    return revived


if __name__ == "__main__":
    print(tick())
