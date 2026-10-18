"""Самотест расписания ночного цикла — без единого настоящего хода. Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.night_selftest

Проверяется то, из-за чего цикл две недели не работал: когда именно период
считается закрытым, что бывает после выключенной машины, и не сожжёт ли
расписание десяток дорогих ходов подряд. Настоящий A.turn подменён заглушкой,
лог и состояние — во временном каталоге.
"""
import ast
import asyncio
import json
import re
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.core import config as C
from jarvis.core import memory as M
from jarvis.core import night as N

fails: list[str] = []
turns: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo(C.TZ))


def with_log(*days: str) -> None:
    """Подложить дни лога (только имена файлов — содержимое не важно)."""
    for f in C.LOG_DIR.glob("*.jsonl"):
        f.unlink()
    for d in days:
        (C.LOG_DIR / f"{d}.jsonl").write_text("{}\n", encoding="utf-8")


def kinds(jobs) -> list[str]:
    return [j["kind"] for j in jobs]


def log_events(key: str) -> list:
    """Записи ночного цикла с данным ключом из подменённого лога.

    Читаются разбором, а не поиском подстроки: content лежит в логе строкой
    с экранированными кавычками, и наивный `in` ловил бы что угодно.
    """
    out = []
    for p in sorted(C.LOG_DIR.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                content = json.loads(line).get("content")
                body = json.loads(content) if isinstance(content, str) else None
            except (ValueError, AttributeError):
                continue
            if isinstance(body, dict) and key in body:
                out.append(body[key])
    return out


def fs_events() -> list:
    return log_events("night_fs_index")


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="night-selftest-"))
    # Подменённые пути обязаны вернуться на место: наборы проверок идут друг за
    # другом одним процессом (`./jarvis --selftest`), и невозвращённый
    # C.DERIVED оставлял следующий набор писать во временный каталог, который
    # этот же самотест уже снёс. Нашлось 24.08 самотестом modules/fs.
    live_paths = (C.DERIVED, C.LOG_DIR, C.ROLLUPS, N.STATE)
    # Снимок живых сводок аудита: подмена C.DERIVED однажды отвалится, и тест
    # обязан упасть, а не молча писать в настоящий derived.
    live_audit = sorted(p.name for p in (C.DERIVED / "audit").glob("*.md"))
    C.DERIVED = tmp / "derived"
    C.LOG_DIR = tmp / "log"
    C.ROLLUPS = C.DERIVED / "rollups"
    C.LOG_DIR.mkdir(parents=True)
    C.DERIVED.mkdir(parents=True)
    N.STATE = C.DERIVED / "night.json"
    # RS.PENDING — константа core.restart, посчитанная при ИМПОРТЕ от тогдашнего
    # (настоящего) C.DERIVED: подмена C.DERIVED здесь её не задевает (тот же
    # класс дыры, что правило samotest-ne-pishet-v-zhivuyu-pamyat описывает для
    # write_fact/registry — код пишет по своей константе, а не по входным путям
    # теста). Подменяем путь отдельно.
    live_pending = N.RS.PENDING
    N.RS.PENDING = C.DERIVED / "restart_pending.json"

    # Настоящий ход стоит денег и правит память — подменяем заглушкой.
    async def fake_turn(prompt, **kw):
        turns.append(prompt)
        return "ок"
    N.A = type("A", (), {"turn": staticmethod(fake_turn)})
    M.refresh_obligation_statuses = lambda: None

    # Каждый tick() теперь (bl-118) на КАЖДОМ проходе зовёт restart_after_tick(),
    # то есть настоящий core.staleness.check() против настоящего живого демона
    # и, если демон устарел (а во время правки core/*.py он устарел), настоящий
    # core.restart.schedule() — который поднимает НАСТОЯЩИЙ процесс-наблюдатель.
    # До этой подмены именно так и вышло 26.08: прогон этого самого файла
    # заспавнил живой `python -m jarvis.core.restart --watch`, пока десятки
    # ранних check() в этом файле дёргали N.tick() без единого мока ST/RS —
    # они были мокнуты только внутри секции про сам bl-114/bl-118, а не с самого
    # начала функции. Найдено и остановлено руками (ход не убил демона: процесс
    # ещё спал DELAY, kill не успел случиться). Теперь безопасный дефолт
    # («код свежий, планировать нечего») стоит с первой же строчки, а секция
    # про перезапуск ниже подменяет ST/RS на свои сценарии и возвращает этот
    # же безопасный дефолт, а не боевой оригинал — оригинал возвращается только
    # в самом конце функции.
    live_st_check, live_rs_schedule = N.ST.check, N.RS.schedule

    def _no_real_schedule(reason):
        raise AssertionError(
            f"настоящий RS.schedule не должен звонить в этом самотесте: {reason}")
    N.ST.check = lambda: {"ok": True, "pid": None, "started": None,
                          "stale": [], "reason": ""}
    N.RS.schedule = _no_real_schedule

    # Дневная свёртка пересчитывает реестр способностей — а лог здесь заглушка.
    # Без подмены пути пересчёт пойдёт по пустому логу и обнулит НАСТОЯЩИЙ
    # memory/registry.md (так и случилось 24.08).
    from jarvis.core import registry as R
    live_registry, live_text = C.REGISTRY, C.REGISTRY.read_text(encoding="utf-8")
    R.REGISTRY = tmp / "registry.md"
    R.REGISTRY.write_text("| id | способность | статус | добавлено | последний вызов "
                          "| успехов | провалов |\n|---|---|---|---|---|---|---|\n"
                          "| cap-001 | канал | experimental | 2026-08-22 | — | 0 | 0 |\n",
                          encoding="utf-8")

    # Дневная свёртка догоняет индекс файлов ПК. Настоящий update стоит секунды,
    # а на снесённой базе — полторы минуты полной сборки диска; здесь он подменён.
    # Живую базу трогать нельзя и по правилу samotest-ne-pishet-v-zhivuyu-pamyat:
    # derived восстановим, но не ценой каждого прогона самотеста.
    from jarvis.modules.fs import index as FS
    live_fs_update, live_fs_stats = FS.update, FS.last_stats
    live_db, live_db_stamp = FS.DB, (FS.DB.stat().st_mtime_ns if FS.DB.exists() else None)
    fs_calls: list = []
    boom_fs: list = []

    def fake_fs_update(*a, **kw):
        fs_calls.append(kw)
        if boom_fs:
            raise OSError("диск отмонтирован")
        return {"files": 12, "dirs": 4, "touched_dirs": 3, "removed": 1,
                "errors": 0, "seconds": 0.4}
    FS.update = fake_fs_update
    FS.last_stats = lambda *a, **kw: {"files": 10}

    try:
        # 1. Календарь: сутки закрываются в NIGHT_HOUR, а не в полночь.
        check("день закрывается только после NIGHT_HOUR",
              N.last_closed_day(at("2026-08-24T02:00")) == date(2026, 8, 22)
              and N.last_closed_day(at("2026-08-24T04:30")) == date(2026, 8, 23))

        # 2. Неделя закрыта, только когда прожито воскресенье.
        check("неделя закрывается после воскресенья",
              N.last_closed_week(at("2026-08-24T04:30")) == "2026-W34"      # вс 23-е свёрнуто
              and N.last_closed_week(at("2026-08-23T04:30")) == "2026-W33")  # ещё идёт W34

        # 3. Месяц — только после последнего числа.
        check("месяц закрывается после последнего числа",
              N.last_closed_month(at("2026-09-01T04:30")) == "2026-08"
              and N.last_closed_month(at("2026-08-31T04:30")) == "2026-07")

        # 4. Первый запуск не догоняет прошлое: состояние ставится по месту.
        with_log("2026-08-22", "2026-08-23")
        check("первый запуск ничего не сворачивает задним числом",
              N.due(at("2026-08-23T20:00")) == [])
        check("после первого запуска состояние проставлено",
              N.read_state().get("daily") == "2026-08-22")

        # 5. Наступила ночь — дневная свёртка нужна, недельная ещё нет.
        check("в 04:30 понедельника пора сворачивать день",
              kinds(N.due(at("2026-08-24T04:30"))) == ["daily", "weekly"])
        check("в 02:00 (до NIGHT_HOUR) ещё рано",
              N.due(at("2026-08-24T02:00")) == [])

        # 6. Свёртка отмечается ДО хода: упавший цикл не повторяется по кругу.
        async def boom(prompt, **kw):
            turns.append(prompt)
            raise RuntimeError("ход упал")
        good, N.A.turn = N.A.turn, boom
        try:
            asyncio.run(N.tick(at("2026-08-24T04:30")))
        except RuntimeError:
            pass
        N.A.turn = good
        after = N.due(at("2026-08-24T05:00"))
        check("оборванная свёртка догоняется ровно один раз",
              kinds(after)[:1] == ["daily"] and after[0].get("attempt") == 2
              and after[0].get("key") == "2026-08-23")
        check("догон не считает период несвёрнутым заново",
              N.read_state().get("daily") == "2026-08-23")

        # 6б. Второй обрыв подряд — сдаёмся: дорогой ход не крутится по кругу.
        N.A.turn = boom
        try:
            asyncio.run(N.tick(at("2026-08-24T05:00")))
        except RuntimeError:
            pass
        N.A.turn = good
        check("после второго обрыва свёртка больше не повторяется",
              "daily" not in kinds(N.due(at("2026-08-24T05:30"))))

        # 6в. Дошедший до конца ход снимает метку — догонять нечего.
        N.write_state(daily="2026-08-22", weekly="2026-W34", monthly="2026-08",
                      running=None)
        asyncio.run(N.tick(at("2026-08-24T04:30")))
        check("успешная свёртка снимает метку обрыва",
              N.read_state().get("running") is None
              and N.due(at("2026-08-24T05:00")) == [])

        # 6г. Метку чужого ЖИВОГО процесса не подбираем: работа ещё идёт.
        N.write_state(running={"kind": "daily", "key": "2026-08-23",
                               "since": "2026-08-23", "attempt": 1, "pid": 1})
        check("чужую идущую свёртку догон не ворует",
              N.unfinished(N.read_state()) is None)
        N.write_state(running=None)

        # 7. Догон: машина была выключена трое суток — один ход, а не три.
        N.write_state(daily="2026-08-24", weekly="2026-W34", monthly="2026-08")
        with_log("2026-08-25", "2026-08-26", "2026-08-27")
        jobs = N.due(at("2026-08-28T09:00"))
        check("три пропущенных дня сворачиваются одним ходом",
              kinds(jobs) == ["daily"] and jobs[0]["key"] == "2026-08-27"
              and jobs[0]["since"] == "2026-08-25")

        # 8. Пустой период не сворачивается: сочинять нечего.
        N.write_state(daily="2026-08-24", weekly="2026-W34", monthly="2026-08")
        with_log()
        check("без лога свёртка не запускается",
              N.due(at("2026-08-28T09:00")) == [])

        # 9. Недельная и месячная границы: 1 сентября — обе сразу.
        N.write_state(daily="2026-08-30", weekly="2026-W34", monthly="2026-07")
        with_log(*[f"2026-08-{d:02d}" for d in range(24, 32)])
        check("1 сентября приходят дневная, недельная и месячная",
              kinds(N.due(at("2026-09-01T04:30"))) == ["daily", "weekly", "monthly"])

        # 10. Настоящий tick: свёртки отмечаются, второй вызов пуст.
        turns.clear()
        n = asyncio.run(N.tick(at("2026-09-01T04:30")))
        check("tick отработал три свёртки", n == 3 and len(turns) == 3)
        check("повторный tick не делает ничего",
              asyncio.run(N.tick(at("2026-09-01T05:00"))) == 0)
        check("в промптах стоит нужный период",
              "2026-08-31" in turns[0] and "2026-W35" in turns[1]
              and "2026-08" in turns[2])
        check("каталоги свёрток созданы",
              (C.ROLLUPS / "weeks").is_dir() and (C.ROLLUPS / "months").is_dir())

        # 11. Порядок: дневная свёртка идёт раньше недельной и месячной.
        check("порядок свёрток — день, неделя, месяц",
              "Ночной цикл" in turns[0] and "Свёртка недели" in turns[1]
              and "Месячная ревизия" in turns[2])

        # 12. Часовой пояс — Ильи, а не машины и не UTC.
        check("часовой пояс — Владивосток", C.TZ == "Asia/Vladivostok")

        # 13. Реестр: дневная свёртка его пересчитывает, но живой файл цел,
        # и по заглушке лога он не обнуляется — предохранитель в registry.py.
        check("живой реестр самотест не тронул",
              live_registry.read_text(encoding="utf-8") == live_text)
        check("по пустому логу реестр не обнуляется",
              R.recount_and_write() == []
              and "| cap-001 | канал | experimental | 2026-08-22 | — | 0 | 0 |"
              in R.REGISTRY.read_text(encoding="utf-8"))
        # 14. Индекс файлов ПК догоняется дневной свёрткой — раз в сутки и
        # без права уронить саму свёртку.
        fs_calls.clear()
        turns.clear()
        with_log()
        asyncio.run(N.run_job({"kind": "daily", "key": "2026-09-02",
                               "since": "2026-09-02"}))
        check("дневная свёртка обновляет индекс файлов ровно раз",
              len(fs_calls) == 1 and len(turns) == 1)
        events = fs_events()
        check("в лог легли числа индекса, включая прирост за сутки",
              len(events) == 1 and events[0]["файлов"] == 12
              and events[0]["прибавилось"] == 2 and events[0]["записей_снято"] == 1)

        fs_calls.clear()
        turns.clear()
        asyncio.run(N.run_job({"kind": "weekly", "key": "2026-W35"}))
        check("недельная свёртка индекс не трогает",
              not fs_calls and len(turns) == 1)

        boom_fs.append(True)
        fs_calls.clear()
        turns.clear()
        with_log()
        # Ловим сами: без предохранителя в night.py исключение улетит наружу и
        # уронит весь прогон — красный результат, но неразборчивый.
        crashed = None
        try:
            asyncio.run(N.run_job({"kind": "daily", "key": "2026-09-03",
                                   "since": "2026-09-03"}))
        except Exception as e:                              # noqa: BLE001
            crashed = e
        boom_fs.clear()
        check("отвалившийся индекс не роняет свёртку",
              crashed is None and len(fs_calls) == 1 and len(turns) == 1)
        check("беда с индексом ушла в лог, а не в тишину",
              any("OSError" in str(e) for e in fs_events()))

        check("живую базу индекса самотест не тронул",
              FS.DB == live_db
              and (live_db.stat().st_mtime_ns if live_db.exists() else None)
              == live_db_stamp)

        # 15. Машинный самоаудит суток (bl-084): свёртка начинается с готовых
        # цифр, а не с чтения лога глазами.
        turns.clear()
        with_log("2026-09-04")
        asyncio.run(N.run_job({"kind": "daily", "key": "2026-09-04",
                               "since": "2026-09-04"}))
        check("дневная свёртка собирает самоаудит и кладёт его в derived/audit",
              (C.DERIVED / "audit" / "2026-09-04.md").exists()
              and len(turns) == 1 and "Машинный самоаудит" in turns[0]
              and "# Самоаудит 2026-09-04" in turns[0])

        turns.clear()
        with_log("2026-09-05", "2026-09-06")
        asyncio.run(N.run_job({"kind": "daily", "key": "2026-09-06",
                               "since": "2026-09-05"}))
        check("за пропущенные дни сводки пишутся все, в промпт идёт последняя",
              (C.DERIVED / "audit" / "2026-09-05.md").exists()
              and (C.DERIVED / "audit" / "2026-09-06.md").exists()
              and "# Самоаудит 2026-09-06" in turns[0]
              and "derived/audit/2026-09-05.md" in turns[0])

        turns.clear()
        with_log()
        crashed = None
        try:
            asyncio.run(N.run_job({"kind": "daily", "key": "2026-09-07",
                                   "since": "2026-09-07"}))
        except Exception as e:                              # noqa: BLE001
            crashed = e
        check("аудит без лога не роняет свёртку и не врёт цифрами",
              crashed is None and len(turns) == 1
              and "Машинный самоаудит" not in turns[0])
        check("беда с аудитом ушла в лог, а не в тишину",
              any("FileNotFoundError" in str(e)
                  for e in log_events("night_audit_day")))

        turns.clear()
        before_audit = len(list((C.DERIVED / "audit").glob("*.md")))
        asyncio.run(N.run_job({"kind": "weekly", "key": "2026-W36"}))
        check("недельная свёртка самоаудит не собирает",
              len(list((C.DERIVED / "audit").glob("*.md"))) == before_audit
              and "Машинный самоаудит" not in turns[0])
        check("живые сводки аудита самотест не тронул",
              sorted(p.name for p in (live_paths[0] / "audit").glob("*.md"))
              == live_audit)

        # 16. Конец ночного цикла сверяет демон с диском (bl-114) и планирует
        # отложенный перезапуск — ровно один раз за тик, не по одному на
        # каждую свёртку. core.staleness и core.restart здесь на заглушках:
        # настоящий процесс-наблюдатель не поднимается ни разу.
        live_check, live_schedule = N.ST.check, N.RS.schedule

        def one_daily_due(now: datetime) -> None:
            """Состояние, где due() отдаёт РОВНО одну дневную свёртку —
            неделя и месяц уже помечены свёрнутыми, чтобы восстановление
            демона проверялось само по себе, а не пряталось за тремя ходами."""
            day = N.last_closed_day(now)
            N.write_state(daily=(day - timedelta(days=1)).isoformat(),
                         weekly=N.last_closed_week(now),
                         monthly=N.last_closed_month(now))
            with_log(day.isoformat())

        try:
            scheduled = []
            N.RS.schedule = lambda reason: scheduled.append(reason) or 1

            N.ST.check = lambda: {"ok": True, "pid": 123, "started": 1.0,
                                  "stale": [], "reason": ""}
            now1 = at("2026-09-09T04:30")
            one_daily_due(now1)
            n1 = asyncio.run(N.tick(now1))
            check("код свежий — перезапуск не планируется",
                  n1 == 1 and scheduled == [])

            N.ST.check = lambda: {"ok": False, "pid": 123, "started": 1.0,
                                  "stale": [("core/agent.py", 2.0)],
                                  "reason": "демон pid 123 работает на устаревшем коде"}
            now2 = at("2026-09-10T04:30")
            one_daily_due(now2)
            n2 = asyncio.run(N.tick(now2))
            check("устаревший код — перезапуск планируется ровно один раз",
                  n2 == 1
                  and scheduled == ["демон pid 123 работает на устаревшем коде"])

            # Одним тиком идут сразу три свёртки (день, неделя, месяц) — тот
            # же расклад, что в проверке №9. Перезапуск всё равно один, а не
            # по одному на каждую свёртку.
            scheduled.clear()
            N.write_state(daily="2026-08-30", weekly="2026-W34", monthly="2026-07")
            with_log(*[f"2026-08-{d:02d}" for d in range(24, 32)])
            n3 = asyncio.run(N.tick(at("2026-09-01T04:30")))
            check("день, неделя и месяц одним тиком — перезапуск всё равно один",
                  n3 == 3 and scheduled == ["демон pid 123 работает на устаревшем коде"])

            N.ST.check = lambda: {"ok": False, "pid": None, "started": None,
                                  "stale": [], "reason": "демон ВК не запущен"}
            scheduled.clear()
            now4 = at("2026-09-11T04:30")
            one_daily_due(now4)
            n4 = asyncio.run(N.tick(now4))
            check("демона нет вовсе — планировать нечего",
                  n4 == 1 and scheduled == [])

            def boom_check():
                raise OSError("/proc недоступен")
            N.ST.check = boom_check
            scheduled.clear()
            now5 = at("2026-09-12T04:30")
            one_daily_due(now5)
            crashed = None
            try:
                asyncio.run(N.tick(now5))
            except Exception as e:                              # noqa: BLE001
                crashed = e
            check("сбой самой проверки не роняет ночной цикл",
                  crashed is None and scheduled == [])
            check("беда со сверкой ушла в лог, а не в тишину",
                  any("OSError" in str(e) for e in log_events("night_staleness")))

            # 17. bl-118: до этой правки restart_after_tick() стоял внутри
            #     `if jobs:` и не срабатывал, когда сворачивать было нечего —
            #     а демон, устаревший сразу после свёртки, ждал замечания
            #     почти сутки (следующего due-тика). Проверка обязана идти и
            #     на пустом тике: due() == [], jobs == 0, ходов не было — а
            #     устаревание всё равно замечено.
            N.ST.check = lambda: {"ok": False, "pid": 123, "started": 1.0,
                                  "stale": [("core/agent.py", 2.0)],
                                  "reason": "демон pid 123 работает на устаревшем коде"}
            now6 = at("2026-09-13T04:30")
            N.write_state(daily=N.last_closed_day(now6).isoformat(),
                         weekly=N.last_closed_week(now6),
                         monthly=N.last_closed_month(now6), running=None)
            check("состояние подстроено так, что due() пуст",
                  N.due(now6) == [])
            scheduled.clear()
            turns.clear()
            n6 = asyncio.run(N.tick(now6))
            check("тик без единой свёртки тоже проверяет устаревание",
                  n6 == 0 and len(turns) == 0
                  and scheduled == ["демон pid 123 работает на устаревшем коде"])
        finally:
            N.ST.check, N.RS.schedule = live_check, live_schedule
    finally:
        FS.update, FS.last_stats = live_fs_update, live_fs_stats
        R.REGISTRY = live_registry
        C.DERIVED, C.LOG_DIR, C.ROLLUPS, N.STATE = live_paths
        N.ST.check, N.RS.schedule = live_st_check, live_rs_schedule
        N.RS.PENDING = live_pending
        shutil.rmtree(tmp, ignore_errors=True)

    # 18. bl-114, вторая половина той же грабли: сама night.tick() проверяет
    #     устаревание на каждом проходе (пункт 17), но ДЕМОН зовёт tick()
    #     только внутри `if night.due()`, то есть примерно раз в сутки.
    #     Правка ядра в 00:12 ждала бы перезапуска до следующей ночи. Здесь
    #     проверяется структурой кода, а не поведением: цикл демона обязан
    #     звать restart_after_tick своим тактом, вне ветки night.due().
    src = (C.ROOT / "modules/vk/daemon.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for kid in ast.iter_child_nodes(node):
            parents[kid] = node

    def _calls(name: str) -> list[ast.AST]:
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == name:
                out.append(node)
        return out

    def _under_due(node: ast.AST) -> bool:
        """Вызов спрятан внутрь ветки, условие которой смотрит night.due()?"""
        cur = node
        while cur in parents:
            par = parents[cur]
            if isinstance(par, ast.If) and cur is not par.test:
                if any(isinstance(n, ast.Attribute) and n.attr == "due"
                       for n in ast.walk(par.test)):
                    return True
            cur = par
        return False

    def _in_loop(node: ast.AST) -> bool:
        cur = node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.While, ast.For, ast.AsyncFor)):
                return True
        return False

    stale_calls = _calls("restart_after_tick")
    check("демон зовёт проверку устаревания сам", bool(stale_calls))
    check("проверка устаревания стоит в цикле демона",
          any(_in_loop(n) for n in stale_calls))
    check("проверка устаревания не спрятана в ветку night.due()",
          any(_in_loop(n) and not _under_due(n) for n in stale_calls))
    check("такт устаревания реже опроса long-poll",
          bool(re.search(r"STALE_EVERY\s*=\s*(\d+)", src))
          and int(re.search(r"STALE_EVERY\s*=\s*(\d+)", src).group(1)) >= 60)

    print()
    if fails:
        print(f"ПЛОХО: {len(fails)} — " + "; ".join(fails))
        return 1
    print("всё зелёное")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
