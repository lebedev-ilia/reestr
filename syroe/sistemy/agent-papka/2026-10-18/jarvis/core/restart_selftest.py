"""Самотест отложенного перезапуска демона ВК (bl-114). Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.restart_selftest

Ничего из настоящего здесь не трогается: время (sleep), список заданий,
pid демона, kill и подъём нового процесса — везде подставлены заглушки.
Живой демон и живая очередь заданий не задеты ни разу; лог на время
проверки schedule() уводится во временный каталог (тот же приём, что в
core/scratch_selftest.py и core/night_selftest.py).
"""

import json
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path

from jarvis.core import config as C
from jarvis.core import heartbeat as HB
from jarvis.core import jobs as J
from jarvis.core import restart as R
from jarvis.core import staleness as ST

fails: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def run() -> int:
    # --------------------------------------------------------------------
    # 1. _quiet(): решает по modules/jobs (running/queued), а не по pgrep,
    #    И по метке хода самого демона (bl-119). Обе заглушки ставятся ДО
    #    первого вызова: настоящий _quiet() читает живой vk.lock и живой
    #    derived/turns, и тест обязан не зависеть от того, что там сейчас.
    # --------------------------------------------------------------------
    live_by_status, live_busy, live_pid = J.by_status, HB.busy, ST.daemon_pid
    try:
        HB.busy = lambda pid: False
        ST.daemon_pid = lambda: 4242

        J.by_status = lambda *st: [{"id": "job-999"}]
        check("занятая очередь — не тихо", R._quiet() is False)

        J.by_status = lambda *st: []
        check("пустая очередь и демон свободен — тихо", R._quiet() is True)

        # bl-119: демон внутри хода — убивать нельзя, даже если заданий нет.
        asked: list[int | None] = []
        HB.busy = lambda pid: asked.append(pid) or True
        check("демон внутри хода — не тихо", R._quiet() is False)
        check("метка спрашивается про pid ЖИВОГО демона, а не про свой",
              asked == [4242])

        # Демона нет вовсе (pid=None) — метку спрашивать не о чем, и это не
        # повод считать «занято»: перезапуск просто поднимет демон заново.
        HB.busy = live_busy
        ST.daemon_pid = lambda: None
        check("демона нет — тихо", R._quiet() is True)
    finally:
        J.by_status, HB.busy, ST.daemon_pid = live_by_status, live_busy, live_pid

    # --------------------------------------------------------------------
    # 1б. Сама метка хода (core.heartbeat): ставится, снимается, не врёт про
    #     мёртвый процесс и протухает. Каталог меток уводится во временный
    #     ПЕРВОЙ строкой — иначе тест снесёт метку живого демона (правило
    #     samotest-ne-pishet-v-zhivuyu-pamyat: код пишет по своей константе).
    # --------------------------------------------------------------------
    live_turns, live_state = HB.TURNS, HB.STATE
    tmp_hb = Path(tempfile.mkdtemp(prefix="heartbeat-selftest-"))
    try:
        HB.TURNS = tmp_hb / "turns"
        HB.STATE = tmp_hb / "heartbeat.json"
        me = os.getpid()

        check("до хода метки нет", HB.busy(me) is False)
        HB.mark_turn_start("self")
        check("ход начался — метка видна другому процессу", HB.busy(me) is True)
        check("метка живёт файлом на процесс", (HB.TURNS / f"{me}.json").exists())
        HB.finish()
        check("ход кончился — метка снята", HB.busy(me) is False)

        # Мёртвый процесс: метка осталась (ход убили), но занятости нет.
        (HB.TURNS).mkdir(parents=True, exist_ok=True)
        (HB.TURNS / "99999999.json").write_text(
            json.dumps({"pid": 99999999, "started": time.time()}),
            encoding="utf-8")
        check("метка мёртвого процесса занятостью не считается",
              HB.busy(99999999) is False)

        # Протухшая метка своего же процесса не держит перезапуск вечно.
        (HB.TURNS / f"{me}.json").write_text(
            json.dumps({"pid": me, "started": time.time() - HB.TURN_MAX_AGE - 1}),
            encoding="utf-8")
        check("забытая метка старше потолка не блокирует перезапуск",
              HB.busy(me) is False)

        # Новый ход подчищает мусор умерших процессов.
        HB.mark_turn_start("vk")
        check("старт хода убирает метки умерших процессов",
              not (HB.TURNS / "99999999.json").exists())
        HB.finish()
        check("busy(None) — не занято", HB.busy(None) is False)
    finally:
        HB.TURNS, HB.STATE = live_turns, live_state
        shutil.rmtree(tmp_hb, ignore_errors=True)

    # --------------------------------------------------------------------
    # 2. restart_once(): вся логика ожидания/убийства/подъёма — на
    #    заглушках. Каждый случай сверяет ОБЕ стороны: убил или нет, ждал
    #    или нет — а не только «код не упал».
    # --------------------------------------------------------------------
    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)

    # 2а. Тихо сразу — задержка одна, ни одного цикла ожидания, убийство есть.
    kills, spawns = [], []
    sleeps.clear()
    out = R.restart_once(sleep=fake_sleep, quiet=lambda: True,
                         get_pid=lambda: 4242,
                         kill=lambda pid, sig: kills.append((pid, sig)),
                         spawn=lambda: spawns.append(1) or 9999)
    check("тихо сразу: задержка ровно одна, циклов ожидания нет",
          sleeps == [R.DELAY, 3] and out["tries"] == 0)
    check("тихо сразу: убит правильный pid сигналом TERM",
          kills == [(4242, signal.SIGTERM)])
    check("тихо сразу: демон поднят заново ровно один раз",
          spawns == [1] and out["new_pid"] == 9999)

    # 2б. Живое задание вначале — перезапуск ЖДЁТ тишины, а не рубит работу
    #    сразу: quiet() отвечает False дважды, потом True, kill — только
    #    после того, как стало тихо.
    busy_answers = iter([False, False, True])
    kills.clear(); spawns.clear(); sleeps.clear()
    out = R.restart_once(sleep=fake_sleep, quiet=lambda: next(busy_answers),
                         get_pid=lambda: 111,
                         kill=lambda pid, sig: kills.append(pid),
                         spawn=lambda: spawns.append(1) or 222)
    check("живое задание: два цикла ожидания, потом тишина",
          out["tries"] == 2 and not out["gave_up_busy"])
    check("живое задание: kill вызван, только когда стало тихо",
          kills == [111])
    check("живое задание: демон поднят заново после тишины",
          spawns == [1])

    # 2в. Задание не отпускает ВСЁ окно ожидания. Перезапуск не решает это
    #    молча: исход помечен gave_up_busy=True, чтобы решение «рискнули»
    #    было видно, а не спрятано в тишине лога.
    kills.clear(); spawns.clear(); sleeps.clear()
    out = R.restart_once(sleep=fake_sleep, quiet=lambda: False,
                         get_pid=lambda: 333,
                         kill=lambda pid, sig: kills.append(pid),
                         spawn=lambda: spawns.append(1) or 444)
    check("задание занято весь POLL_MAX — ожидание отработало целиком",
          out["tries"] == R.POLL_MAX)
    check("занятое окно целиком помечено gave_up_busy, а не скрыто",
          out["gave_up_busy"] is True)

    # 2г. Демона уже нет (pid=None) — убивать и поднимать нечего, тишина
    #    роли не играет.
    kills.clear(); spawns.clear()
    out = R.restart_once(sleep=fake_sleep, quiet=lambda: True,
                         get_pid=lambda: None,
                         kill=lambda pid, sig: kills.append(pid),
                         spawn=lambda: spawns.append(1) or 1)
    check("демона уже нет: kill и подъём не вызываются",
          out["killed"] is None and not kills and not spawns)

    # --------------------------------------------------------------------
    # 3. schedule(): планирует, не исполняя, и логирует факт планирования
    #    штатным путём (M.log). Настоящий subprocess.Popen не зовётся —
    #    только заглушка watcher'а. Лог на время проверки уведён во
    #    временный каталог, живой memory/log не тронут.
    # --------------------------------------------------------------------
    live_log_dir = C.LOG_DIR
    live_pending, live_derived = R.PENDING, C.DERIVED
    tmp = Path(tempfile.mkdtemp(prefix="restart-selftest-"))
    try:
        C.LOG_DIR = tmp / "log"
        C.LOG_DIR.mkdir(parents=True)
        C.DERIVED = tmp / "derived"
        C.DERIVED.mkdir(parents=True)
        R.PENDING = C.DERIVED / "restart_pending.json"

        # spawn_watch отдаёт СВОЙ pid (os.getpid()), а не выдуманное число: он
        # должен быть по-настоящему живым в /proc, иначе следующая проверка
        # идемпотентности (3б) не отличит «жив» от «уже нет» — сам тестовый
        # процесс жив по построению.
        watched: list[int] = []
        pid = R.schedule("код на диске новее демона: core/agent.py",
                         spawn_watch=lambda: watched.append(1) or os.getpid())
        check("schedule планирует ровно один процесс-наблюдатель",
              watched == [1] and pid == os.getpid())

        lines = []
        for f in C.LOG_DIR.glob("*.jsonl"):
            lines += f.read_text(encoding="utf-8").splitlines()
        check("факт планирования лёг в лог ровно одной строкой",
              sum("night_restart_scheduled" in ln for ln in lines) == 1)
        check("в записи виден pid наблюдателя и причина",
              any("night_restart_scheduled" in ln and str(os.getpid()) in ln
                  and "core/agent.py" in ln for ln in lines))

        # ------------------------------------------------------------------
        # 3б. Идемпотентность (bl-118): повторные вызовы schedule() подряд —
        #     как их даёт tick() на каждом тике, пока демон устаревший, —
        #     не плодят второго наблюдателя, пока первый жив, и не отодвигают
        #     срок. Наблюдатель мёртв — планирование срабатывает заново.
        # ------------------------------------------------------------------
        watched.clear()
        pid2 = R.schedule("тот же повод, тик спустя",
                          spawn_watch=lambda: watched.append(1) or 424242)
        check("наблюдатель ещё жив (свой pid) — второй не поднимается",
              watched == [] and pid2 == os.getpid())

        lines = []
        for f in C.LOG_DIR.glob("*.jsonl"):
            lines += f.read_text(encoding="utf-8").splitlines()
        check("повторный тик не добавил вторую строку в лог",
              sum("night_restart_scheduled" in ln for ln in lines) == 1)
        check("файл ожидания не сдвинулся — тот же pid, что и был",
              json.loads(R.PENDING.read_text(encoding="utf-8"))["pid"]
              == os.getpid())

        # Наблюдатель закончил работу (процесс умер) — слот свободен снова.
        R.PENDING.write_text(json.dumps({"pid": 99999999, "reason": "стар"}),
                             encoding="utf-8")
        watched.clear()
        pid3 = R.schedule("демон снова устарел, наблюдатель уже закончил",
                          spawn_watch=lambda: watched.append(1) or 5556)
        check("исчезнувший наблюдатель не блокирует новое планирование",
              watched == [1] and pid3 == 5556)
        lines = []
        for f in C.LOG_DIR.glob("*.jsonl"):
            lines += f.read_text(encoding="utf-8").splitlines()
        check("новое планирование дало вторую строку в лог",
              sum("night_restart_scheduled" in ln for ln in lines) == 2)

        # ------------------------------------------------------------------
        # 3в. Переиспользованный pid (bl-120). Голого «/proc/<pid> есть» мало:
        #     ОС отдаёт номера по кругу, и под номером умершего наблюдателя
        #     живёт чужой процесс. Мутация: в файле ожидания стоит pid ЖИВОГО
        #     чужого процесса (pid 1 — init, он жив всегда и наблюдателем не
        #     является), но признак процесса не тот. schedule() обязан поднять
        #     нового наблюдателя, а не решить, что перезапуск уже назначен.
        #     Заглушка spawn_watch стоит везде — настоящий процесс не подняли
        #     ни разу, kill не зовётся в этом блоке вовсе.
        # ------------------------------------------------------------------
        init_started = ST.started_at(1)
        check("время старта чужого процесса читается из /proc",
              isinstance(init_started, float))

        R.PENDING.write_text(json.dumps(
            {"pid": 1, "reason": "мёртвый наблюдатель", "started": time.time()}),
            encoding="utf-8")
        check("pid жив, но признак процесса чужой — наблюдателя нет",
              R._pending_watch_pid() is None)
        watched.clear()
        pid4 = R.schedule("демон устарел, а pid наблюдателя переиспользован",
                          spawn_watch=lambda: watched.append(1) or 5557)
        check("переиспользованный pid не срывает планирование перезапуска",
              watched == [1] and pid4 == 5557)
        check("после планирования в файле ожидания новый pid",
              json.loads(R.PENDING.read_text(encoding="utf-8"))["pid"] == 5557)

        # Обратная сторона мутации: признак СОВПАЛ — процесс тот самый, и
        # второго наблюдателя поднимать нельзя. Иначе проверка просто
        # отвергала бы всё подряд и каждый тик плодил бы перезапуск.
        R.PENDING.write_text(json.dumps(
            {"pid": 1, "reason": "живой наблюдатель", "started": init_started}),
            encoding="utf-8")
        watched.clear()
        pid5 = R.schedule("тот же повод ещё тик спустя",
                          spawn_watch=lambda: watched.append(1) or 5558)
        check("признак процесса совпал — второй наблюдатель не поднимается",
              watched == [] and pid5 == 1)

        # Файл старого формата (без "started") — сверяться не с чем, признак
        # берётся из cmdline: у чужого процесса метки наблюдателя нет.
        R.PENDING.write_text(json.dumps({"pid": 1, "reason": "старый формат"}),
                             encoding="utf-8")
        check("старый формат без времени старта: чужой cmdline не проходит",
              R._pending_watch_pid() is None)
        check("свой cmdline читается и не пуст", bool(R._cmdline(os.getpid())))

        # Само планирование обязано класть признак в файл, иначе вся проверка
        # выше держится на пустом месте.
        R.PENDING.unlink()
        watched.clear()
        R.schedule("проверка записи признака",
                   spawn_watch=lambda: watched.append(1) or os.getpid())
        saved = json.loads(R.PENDING.read_text(encoding="utf-8"))
        check("schedule записал время старта наблюдателя рядом с pid",
              isinstance(saved.get("started"), float)
              and abs(saved["started"] - ST.started_at(os.getpid())) < 1.0)

        # ------------------------------------------------------------------
        # 4. _spawn_daemon()/_spawn_watch(): окружение нового процесса не
        #    наследует JARVIS_SELFTEST/JARVIS_LIVE (bl-121) — subprocess.Popen
        #    подменён, настоящий процесс не поднимается ни разу. Худший
        #    случай: вызывающий процесс сам несёт обе метки (как несёт этот
        #    самый самотест).
        # ------------------------------------------------------------------
        saved_popen = R.subprocess.Popen
        saved_selftest_env = os.environ.get("JARVIS_SELFTEST")
        saved_live_env = os.environ.get("JARVIS_LIVE")
        captured: list[dict] = []

        class FakeProc:
            pid = 700700

        def fake_popen(*args, **kwargs):
            captured.append(kwargs)
            return FakeProc()

        try:
            os.environ["JARVIS_SELFTEST"] = "1"
            os.environ["JARVIS_LIVE"] = "1"
            R.subprocess.Popen = fake_popen

            captured.clear()
            pid_d = R._spawn_daemon()
            check("_spawn_daemon вернул pid поднятого процесса",
                  pid_d == 700700)
            env_d = (captured[0].get("env") if captured else None) or {}
            check("_spawn_daemon передал явный env", bool(captured) and
                  captured[0].get("env") is not None)
            check("_spawn_daemon: в env демона нет унаследованного SELFTEST",
                  "JARVIS_SELFTEST" not in env_d)
            check("_spawn_daemon: в env демона нет унаследованного LIVE",
                  "JARVIS_LIVE" not in env_d)

            captured.clear()
            pid_w = R._spawn_watch()
            check("_spawn_watch вернул pid поднятого процесса",
                  pid_w == 700700)
            env_w = (captured[0].get("env") if captured else None) or {}
            check("_spawn_watch передал явный env", bool(captured) and
                  captured[0].get("env") is not None)
            check("_spawn_watch: в env наблюдателя нет унаследованного SELFTEST",
                  "JARVIS_SELFTEST" not in env_w)
            check("_spawn_watch: в env наблюдателя нет унаследованного LIVE",
                  "JARVIS_LIVE" not in env_w)
        finally:
            R.subprocess.Popen = saved_popen
            if saved_selftest_env is None:
                os.environ.pop("JARVIS_SELFTEST", None)
            else:
                os.environ["JARVIS_SELFTEST"] = saved_selftest_env
            if saved_live_env is None:
                os.environ.pop("JARVIS_LIVE", None)
            else:
                os.environ["JARVIS_LIVE"] = saved_live_env
    finally:
        C.LOG_DIR = live_log_dir
        R.PENDING, C.DERIVED = live_pending, live_derived
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print(f"ПЛОХО: {len(fails)} — " + "; ".join(fails))
        return 1
    print("всё зелёное")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
