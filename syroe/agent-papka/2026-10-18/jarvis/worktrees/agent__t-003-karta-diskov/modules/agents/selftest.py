#!/usr/bin/env python3
"""Самотест модуля постоянных ролей: зона (roles.py) и сторож (watchdog.py).

Отдельный от core/selftest.py набор — правка core/selftest.py требует
отдельного подтверждения Ильи (см. брифы ролей), а эта проверка должна жить
и запускаться независимо от него:

  .venv/bin/python -m jarvis.modules.agents.selftest

Код возврата 0 — всё зелено, 1 — что-то СЛОМАНО (правило
samotest-schitat-po-kodu-vozvrata: читать код возврата, не грепать вывод).

Пишет только во временные каталоги (tempfile.mkdtemp() — правило
odnorazovye-proverki-v-derived не касается таких путей: они живут в памяти
процесса и стираются самим тестом) и подменяет модульные константы
(watchdog.STATE, roles.AGENTS_DIR, jobs.submit/by_status) на время своего
блока, восстанавливая их в finally — тот же приём, что core/selftest.py
использует для modules/agents/schedule.py. Настоящую память и настоящие
задания не трогает: J.submit подменяется до создания хотя бы одного
реального job-каталога.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jarvis.core import config as C                    # noqa: E402
from jarvis.modules.agents import roles as R          # noqa: E402
from jarvis.modules.agents import watchdog as WD       # noqa: E402

FAILURES: list[str] = []
TOTAL = 0


def check(name: str, cond: bool) -> None:
    global TOTAL
    TOTAL += 1
    if cond:
        print(f"  ок: {name}")
    else:
        FAILURES.append(name)
        print(f"  СЛОМАНО: {name}")


def _touch(path: Path, when: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("след\n", encoding="utf-8")
    if when is not None:
        os.utime(path, (when, when))


def test_last_evidence_paths() -> None:
    print("\n== last_evidence_paths: чистая функция, без единой роли ==")
    box = Path(tempfile.mkdtemp())
    try:
        check("пустой список путей — следов нет",
              WD.last_evidence_paths([]) == 0.0)
        check("путь, который не существует, — следов нет",
              WD.last_evidence_paths([box / "нет-такого"]) == 0.0)

        f1 = box / "a.md"
        _touch(f1, when=1_000_000.0)
        check("один файл — его mtime и есть след",
              WD.last_evidence_paths([f1]) == 1_000_000.0)

        d = box / "dir"
        old = d / "old.md"
        new = d / "sub" / "new.md"
        _touch(old, when=1_000_000.0)
        _touch(new, when=2_000_000.0)
        check("каталог — берётся максимум по вложенным файлам, рекурсивно",
              WD.last_evidence_paths([d]) == 2_000_000.0)
        check("файл и каталог вместе — общий максимум",
              WD.last_evidence_paths([f1, d]) == 2_000_000.0)
    finally:
        shutil.rmtree(box, ignore_errors=True)


def test_period_seconds() -> None:
    print("\n== _period_seconds: чтение периода из cfg, без файлов ==")
    check("15 минут — 900 секунд",
          WD._period_seconds("x", {"schedule": {"every_minutes": 15}}) == 900.0)
    check("нет блока schedule — период 0 (не наблюдаем)",
          WD._period_seconds("x", {}) == 0.0)
    check("every_minutes: 0 — тоже 0, а не деление на ноль где-то дальше",
          WD._period_seconds("x", {"schedule": {"every_minutes": 0}}) == 0.0)


def test_is_dead() -> None:
    print("\n== is_dead: красные и зелёные случаи на фиктивной зоне ==")
    box = Path(tempfile.mkdtemp())
    try:
        zone_file = box / "STATUS.md"
        # Абсолютный путь в owns: C.ROOT / "<abs>" == "<abs>" — стандартное
        # поведение pathlib при джойне с абсолютным путём. Так фикстура живёт
        # в tempdir, а не в настоящем дереве агента, и C.ROOT трогать не надо.
        cfg = {"schedule": {"every_minutes": 10}, "owns": [str(zone_file)]}
        period = 10 * 60.0
        now = 10_000_000.0

        check("следа никогда не было — не мертва (это забота schedule.due())",
              WD.is_dead("x", now=now, cfg=cfg) is False)

        _touch(zone_file, when=now - 2 * period)
        check("след внутри трёх периодов — жива (ЗЕЛЁНЫЙ)",
              WD.is_dead("x", now=now, cfg=cfg) is False)

        _touch(zone_file, when=now - 4 * period)
        check("след старше трёх периодов — мертва (КРАСНЫЙ)",
              WD.is_dead("x", now=now, cfg=cfg) is True)

        _touch(zone_file, when=now - 4 * period)
        check("без расписания (period=0) не наблюдаем вовсе",
              WD.is_dead("x", now=now, cfg={"owns": [str(zone_file)]}) is False)
    finally:
        shutil.rmtree(box, ignore_errors=True)


def test_watched_roles_and_revive() -> None:
    print("\n== watched_roles / revive / tick / pending_report: с фикстурой ролей ==")
    # R.build_brief() требует rdir.relative_to(C.ROOT) — фикстура ролей обязана
    # лежать под корнем агента, /tmp здесь не годится. derived/scratch —
    # одноразовый каталог, чистим сами в finally (правило
    # odnorazovye-proverki-v-derived).
    box = C.DERIVED / "scratch" / "2026-08-30-agents-watchdog-selftest"
    shutil.rmtree(box, ignore_errors=True)
    saved_agents_dir = R.AGENTS_DIR
    saved_state = WD.STATE
    saved_submit = WD.J.submit
    saved_by_status = WD.J.by_status
    submitted: list[dict] = []
    busy_titles: list[str] = []
    try:
        R.AGENTS_DIR = box / "agents"
        WD.STATE = box / "watchdog_state.json"

        zone_dead = box / "zone_dead"
        zone_quiet = box / "zone_quiet"          # роль без watchdog: true
        zone_no_sched = box / "zone_no_sched"    # роль без расписания вовсе

        def make_role(name: str, zone: Path, *, watchdog: bool, minutes: int | None) -> None:
            rdir = R.AGENTS_DIR / name
            rdir.mkdir(parents=True, exist_ok=True)
            lines = [f"id: {name}", "owner: test",
                     "owns:", f"  - {zone}",
                     "limits:", "  work:", "    model: sonnet"]
            if minutes is not None:
                lines += ["schedule:", f"  every_minutes: {minutes}",
                          "  mode: work", "  task: \"проверить очередь\""]
            if watchdog:
                lines += ["watchdog: true"]
            (rdir / "ROLE.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

        make_role("fixture-dead", zone_dead, watchdog=True, minutes=10)
        make_role("fixture-no-watchdog", zone_quiet, watchdog=False, minutes=10)
        make_role("fixture-no-schedule", zone_no_sched, watchdog=True, minutes=None)

        watched = WD.watched_roles()
        check("роль с watchdog:true и расписанием наблюдается",
              "fixture-dead" in watched)
        check("роль без watchdog:true не наблюдается",
              "fixture-no-watchdog" not in watched)
        check("роль без блока schedule не наблюдается",
              "fixture-no-schedule" not in watched)

        def fake_by_status(*statuses):
            return [{"title": t} for t in busy_titles]

        def fake_submit(brief, title="", **kw):
            job_id = f"fake-{len(submitted)}"
            submitted.append({"title": title, "claim_text": kw.get("claim_text")})
            return job_id

        WD.J.by_status = fake_by_status
        WD.J.submit = fake_submit

        period = 10 * 60.0
        now = 20_000_000.0

        # Роль только что заведена — следа ещё нет вовсе. tick() не должен
        # её трогать: это дело schedule.due(), не сторожа.
        check("свежезаведённая роль (следа нет) — tick() её не поднимает",
              WD.tick(now=now) == [])
        check("submit не звался на пустом месте", submitted == [])

        # Роль поработала когда-то давно и с тех пор молчит дольше трёх
        # периодов — КРАСНЫЙ случай.
        _touch(zone_dead / "STATUS.md", when=now - 5 * period)
        revived = WD.tick(now=now)
        check("мёртвая роль поднята сторожем", len(revived) == 1
              and revived[0]["role"] == "fixture-dead")
        check("задание реально поставлено (submit вызван ровно раз)",
              len(submitted) == 1)
        check("pending_report после ПЕРВОГО подъёма — пусто (не спам)",
              WD.pending_report() == [])

        # Второй подъём в пределах часа — уже сигнал, отдельная пометка.
        row2 = WD.revive("fixture-dead", now=now + 600)
        check("второй подъём в течение часа тоже проходит",
              row2 is not None and row2["revivals"] == 2)
        pend = WD.pending_report()
        check("второй подъём за час — роль помечена к докладу (ЗЕЛЁНЫЙ найден)",
              any(p["role"] == "fixture-dead" for p in pend))

        WD.clear_report("fixture-dead")
        check("clear_report снимает пометку",
              WD.pending_report() == [])

        # Занятая роль — форсировать подъём нельзя, это не поломка, это работа.
        busy_titles.append("fixture-dead:work что-то делает")
        before = len(submitted)
        check("занятая роль — revive() ничего не поднимает",
              WD.revive("fixture-dead", now=now + 1200) is None)
        check("submit не звался повторно, пока роль занята",
              len(submitted) == before)
    finally:
        R.AGENTS_DIR = saved_agents_dir
        WD.STATE = saved_state
        WD.J.submit = saved_submit
        WD.J.by_status = saved_by_status
        shutil.rmtree(box, ignore_errors=True)


def test_should_announce_quiet_hours() -> None:
    print("\n== should_announce: ночная тишина (23:00–08:00) не докладывает ==")
    night = datetime(2026, 8, 30, 2, 0)
    day = datetime(2026, 8, 30, 14, 0)
    check("глубокая ночь — не докладываем (доклад копится, не шлётся)",
          WD.should_announce(night) is False)
    check("рабочий день — докладывать можно",
          WD.should_announce(day) is True)


def main() -> int:
    print("Самотест modules/agents (зона ролей, сторож)")
    test_last_evidence_paths()
    test_period_seconds()
    test_is_dead()
    test_watched_roles_and_revive()
    test_should_announce_quiet_hours()
    print(f"\nитого: {len(FAILURES)} провалов из {TOTAL} проверок — "
          f"{'ПЛОХО' if FAILURES else 'всё зелено'}")
    if FAILURES:
        for name in FAILURES:
            print(f"  СЛОМАНО: {name}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
