"""Самотест механизма фоновых заданий (core/jobs.py). Запуск:

    cd "…/Agent" && jarvis/.venv/bin/python -m jarvis.core.jobs_selftest

Живой памяти не касается: `C.JOBS`, `C.JOBS_LOGS` и `C.DERIVED` (там лежит
реестр занятой работы) подменены на `derived/scratch/jobs-selftest-<pid>/`,
весь прогон идёт под `M.selftest_guard` — записи лога уходят в производную
зону, а утечка в живой лог роняет тест (правила
`samotest-ne-pishet-v-zhivuyu-pamyat`, `odnorazovye-proverki-v-derived`).

Настоящих процессов не запускается ни одного: `_spawn`, `alive` и
`_terminate` подменяются. Иначе тест стоил бы денег ровно за то, проверку
чего он изображает, — а через этот механизм 25.08 прошло 44 доллара.

Проверяются ОБЕ стороны каждого запрета (правило `granicu-proveryaet-kod`):
что тощий бриф отбивается И что полный проходит, что пустышка становится
`empty` И что настоящая работа остаётся `done`. Односторонняя проверка здесь
бесполезна — «ничего не запускается никогда» сдала бы её.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as C
from . import jobs as J
from . import memory as M

fails: list[str] = []

# Метка фикстур: по ней guard ищет утечку в живой лог. Стоит в заголовке
# каждого тестового задания, а заголовок попадает в запись job_submit.
MARK = "ФИКСТУРА-jobs-selftest"

FULL_BRIEF = f"""## Задача
Проверить, что механизм заданий вообще принимает бриф. {MARK}

## Где искать
memory/jobs и core/jobs.py, больше нигде.

## Что можно самому
Читать файлы и считать строки, ничего не отправляя наружу.

## Что только с моего согласия
Любая запись за пределы своей папки.

## Формат результата
Выжимка в три строки плюс путь к сырым данным.

## Критерии приёмки
Названо число заданий в журнале и статус каждого.
"""


def check(name: str, ok: bool) -> None:
    print(("  ок   " if ok else "  ПЛОХО") + f"  {name}")
    if not ok:
        fails.append(name)


def mk(title: str, brief: str | None = None, **kw) -> str:
    """Тестовое задание: без дедупа и без запуска процесса."""
    kw.setdefault("dedupe", False)
    kw.setdefault("autostart", False)
    return J.submit(brief or FULL_BRIEF, title=f"{MARK} {title}", **kw)


def ago(sec: float) -> str:
    return (datetime.now(ZoneInfo(C.TZ)) - timedelta(seconds=sec)).isoformat(
        timespec="seconds")


# --------------------------------------------------------------------------
# Бриф: жёсткие разделы, мягкие разделы, заголовок без текста
# --------------------------------------------------------------------------

def test_brief() -> None:
    must, advised = J.brief_gaps(FULL_BRIEF)
    check("полный бриф проходит без замечаний", not must and not advised)

    thin = FULL_BRIEF.replace("Названо число заданий в журнале и статус каждого.", "—")
    must, _ = J.brief_gaps(thin)
    check("заголовок без текста разделом не считается",
          must == ["критерии приёмки"])

    must, _ = J.brief_gaps("## Задача\nсделай хорошо и не делай плохо, пожалуйста")
    check("нет формата и критериев — оба названы жёсткими",
          set(must) == {"формат результата", "критерии приёмки"})

    _, advised = J.brief_gaps(
        "## Задача\nразобрать журнал заданий целиком и сдать выжимку\n"
        "## Формат результата\nтри строки и путь к сырым данным\n"
        "## Критерии приёмки\nназвано число заданий и статус каждого\n")
    check("мягкие разделы отсутствуют, но жёсткие в порядке",
          set(advised) == set(J.BRIEF_ADVISED))

    # Слово из заголовка в тексте выше настоящего раздела: до 26.08 поиск
    # голой подстрокой считал такой бриф заполненным.
    ghost = ("Ниже задача, которую тебе поручили, и критерии приёмки к ней.\n"
             "## Задача\nразобрать журнал заданий и сдать выжимку по нему\n"
             "## Формат результата\nтри строки и путь к сырым данным\n")
    must, _ = J.brief_gaps(ghost)
    check("упоминание в тексте за раздел не сходит",
          "критерии приёмки" in must)


def test_submit(work: Path) -> None:
    before = len(J.all_jobs())
    try:
        mk("тощий", "## Задача\nсделай что-нибудь полезное и хорошее")
        check("тощий бриф отбит исключением", False)
    except J.ThinBrief as e:
        check("тощий бриф отбит исключением", True)
        check("в отказе названы пустые разделы",
              "формат результата" in str(e) and "критерии приёмки" in str(e))
    check("отбитое задание каталога не создало", len(J.all_jobs()) == before)

    try:
        mk("пустой", "   ")
        check("пустой бриф отбит", False)
    except ValueError:
        check("пустой бриф отбит", True)

    job_id = mk("первое")
    meta = J.read(job_id)
    check("принятое задание встало в очередь", meta["status"] == "queued")
    check("бриф лёг на диск целиком",
          J.brief_path(job_id).read_text(encoding="utf-8").strip()
          == FULL_BRIEF.strip())
    check("модель и потолки проставлены",
          meta["model"] and meta["max_turns"] > 0 and meta["timeout"] > 0)
    check("новое задание ещё не доставлено", meta["delivered"] is False)

    second = mk("второе")
    check("номера не повторяются", second != job_id)
    check("номер растёт", J.ID_RE.match(second).group(1)
          > J.ID_RE.match(job_id).group(1))

    # claim.txt — только когда суть работы отличается от брифа
    check("без claim_text отдельного файла нет", not J.claim_path(job_id).exists())
    third = mk("третье", claim_text="разбор журнала заданий за сутки")
    check("claim_text лёг отдельным файлом",
          J.claim_path(third).read_text(encoding="utf-8")
          == "разбор журнала заданий за сутки")

    # Дедуп: та же работа вторым заданием не ставится
    try:
        J.submit(FULL_BRIEF, title=f"{MARK} дубль", autostart=False)
        check("повтор той же работы отбит реестром", False)
    except J.DuplicateWork as e:
        check("повтор той же работы отбит реестром", True)
        check("в отказе назван тот, кто её уже делает", e.ref in (job_id, second, third))

    other = ("## Задача\nсчитать расход токенов по дням из лога августа\n"
             "## Формат результата\nтаблица по дням и итог\n"
             "## Критерии приёмки\nсовпадает с числом записей в логе\n")
    try:
        J.submit(other, title=f"{MARK} другое", autostart=False)
        check("другая работа реестром не отбивается", True)
    except J.DuplicateWork:
        check("другая работа реестром не отбивается", False)


# --------------------------------------------------------------------------
# Сколько тянет машина
# --------------------------------------------------------------------------

def test_capacity() -> None:
    saved = (C.JOB_MAX_PARALLEL, J.cpu_slots, J.mem_available_mb)
    try:
        C.JOB_MAX_PARALLEL = 0
        check("ноль в настройке выключает механизм целиком", J.capacity() == 0)

        C.JOB_MAX_PARALLEL = 4
        J.cpu_slots = lambda: 8
        J.mem_available_mb = lambda: 100 * C.JOB_MEM_MB
        check("потолок настройки сильнее железа", J.capacity() == 4)

        J.cpu_slots = lambda: 2
        check("мало ядер — меньше слотов", J.capacity() == 2)

        J.cpu_slots = lambda: 8
        J.mem_available_mb = lambda: int(C.JOB_MEM_MB * 2.5)
        check("память режет слоты наравне с ядрами", J.capacity() == 2)

        J.mem_available_mb = lambda: 10
        check("памяти нет — но один слот остаётся (иначе очередь встанет навсегда)",
              J.capacity() == 1)
    finally:
        C.JOB_MAX_PARALLEL, J.cpu_slots, J.mem_available_mb = saved


def test_pump() -> None:
    saved_spawn, saved_cap, saved_alive = J._spawn, J.capacity, J.alive
    spawned: list[str] = []
    try:
        J._spawn = lambda job_id: (spawned.append(job_id), 900000 + len(spawned))[1]
        J.capacity = lambda: 2
        J.alive = lambda pid: True

        for i in range(3):
            mk(f"очередь-{i}", FULL_BRIEF.replace("вообще", f"вообще {i}"))
        started = J.pump()
        check("запущено ровно столько, сколько влезает", len(started) == 2)
        check("остальные остались ждать", len(J.by_status("queued")) >= 1)
        check("у запущенного проставлен pid и время старта",
              all(J.read(i)["pid"] and J.read(i)["started"] for i in started))

        again = J.pump()
        check("слоты заняты — второй такт ничего не добавил", again == [])

        # слот освободился — очередь двинулась
        J.finish(started[0], "done",
                 "Журнал разобран: три задания, два запущены, одно ждёт слота.")
        check("законченное задание освободило слот", len(J.pump()) == 1)
    finally:
        # Хвосты этого теста нельзя оставлять в running: следующая проверка
        # смотрит, кого именно подобрала уборка, и чужой недобитый процесс
        # выглядел бы там как её ошибка.
        for meta in J.by_status("running"):
            J._update(meta["id"], status="failed", finished=J._now())
        J._spawn, J.capacity, J.alive = saved_spawn, saved_cap, saved_alive


def test_spawn_env() -> None:
    """_spawn() строит окружение воркера через C.live_env(): даже если сам
    вызывающий процесс уже несёт JARVIS_SELFTEST/JARVIS_LIVE (как несёт этот
    самый самотест — он весь идёт под JARVIS_SELFTEST=1), новый процесс их
    не наследует. Тот же класс дыры, что родил живой демон ВК с флагом
    самотеста через subprocess.Popen без явного env (bl-121): подставляем
    фиктивный Popen, чтобы увидеть переданный kwargs, не запуская процесс."""
    job_id = mk("окружение воркера", FULL_BRIEF.replace("вообще", "вообще Z"))
    saved_popen = J.subprocess.Popen
    saved_selftest_env = os.environ.get("JARVIS_SELFTEST")
    saved_live_env = os.environ.get("JARVIS_LIVE")
    captured: dict = {}

    class FakeProc:
        pid = 900123

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    try:
        # Худший случай: вызывающий процесс несёт ОБЕ метки.
        os.environ["JARVIS_SELFTEST"] = "1"
        os.environ["JARVIS_LIVE"] = "1"
        J.subprocess.Popen = fake_popen
        pid = J._spawn(job_id)
        check("_spawn вернул pid поднятого (фиктивного) процесса",
              pid == 900123)
        env = captured.get("env")
        check("_spawn передал явный env, а не унаследовал молча",
              env is not None)
        env = env or {}
        check("в env воркера нет унаследованного JARVIS_SELFTEST",
              "JARVIS_SELFTEST" not in env)
        check("в env воркера нет унаследованного JARVIS_LIVE",
              "JARVIS_LIVE" not in env)
        check("в env воркера при этом есть его собственная метка роли",
              env.get("JARVIS_ROLE") == "worker")
    finally:
        J.subprocess.Popen = saved_popen
        if saved_selftest_env is None:
            os.environ.pop("JARVIS_SELFTEST", None)
        else:
            os.environ["JARVIS_SELFTEST"] = saved_selftest_env
        if saved_live_env is None:
            os.environ.pop("JARVIS_LIVE", None)
        else:
            os.environ["JARVIS_LIVE"] = saved_live_env


# --------------------------------------------------------------------------
# Уборка за умершими и просроченными
# --------------------------------------------------------------------------

def test_reap() -> None:
    saved_alive, saved_term = J.alive, J._terminate
    killed: list[int] = []
    try:
        J.alive = lambda pid: pid == 777001            # живым считается только один
        J._terminate = lambda pid: killed.append(int(pid))

        # 1. процесс исчез, результата нет
        gone = mk("осиротевшее", FULL_BRIEF.replace("вообще", "вообще A"))
        J._update(gone, status="running", pid=777002, started=ago(5))
        # 2. процесс исчез, но результат записать успел
        saved_job = mk("успел записать", FULL_BRIEF.replace("вообще", "вообще B"))
        J._update(saved_job, status="running", pid=777003, started=ago(5))
        J.result_path(saved_job).write_text(
            "Разобрано три задания: два закончены, одно ждёт слота. "
            "Сырые данные остались в журнале.\n", encoding="utf-8")
        # 3. процесс жив, но висит дольше своего таймаута
        hung = mk("зависшее", FULL_BRIEF.replace("вообще", "вообще C"), timeout=30)
        J._update(hung, status="running", pid=777001, started=ago(600))
        # 4. процесс жив и в срок — трогать нельзя
        ok_job = mk("идёт нормально", FULL_BRIEF.replace("вообще", "вообще D"),
                    timeout=3600)
        J._update(ok_job, status="running", pid=777001, started=ago(5))

        touched = J.reap()
        check("уборка коснулась ровно троих",
              set(touched) == {gone, saved_job, hung})
        check("исчезнувший без результата закрыт провалом",
              J.read(gone)["status"] == "failed")
        check("в причине названо, что процесс исчез",
              "исчез" in (J.read(gone)["error"] or ""))
        check("успевший записать результат закрыт как сделанный",
              J.read(saved_job)["status"] == "done")
        check("зависшее снято по таймауту", J.read(hung)["status"] == "failed")
        check("зависшему процессу послан сигнал", killed == [777001])
        check("идущее в срок задание не тронуто",
              J.read(ok_job)["status"] == "running")
        check("у закрытых посчитано время", J.read(gone)["sec"] is not None)
    finally:
        J.alive, J._terminate = saved_alive, saved_term
        J._update(ok_job, status="failed")   # чтобы не мешать следующим тестам


# --------------------------------------------------------------------------
# Пустышка: процесс не упал, а работы нет
# --------------------------------------------------------------------------

def test_empty() -> None:
    real = ("Разобрал журнал: 16 заданий за сутки, из них 3 вхолостую. "
            "Список и суммы лежат в разобранном виде, дальше по тексту.")
    check("настоящая работа пустышкой не считается", J.empty_reason(real) is None)
    check("пусто — пустышка", J.empty_reason("") is not None)
    check("пара слов — пустышка", J.empty_reason("готово") is not None)
    check("«сдал 0 из 20» — пустышка",
          "ноль" in (J.empty_reason(real + " Итого сдал 0 из 20 карточек.") or ""))
    check("«ничего не нашёл» — пустышка",
          J.empty_reason("Искал везде, ничего не нашёл, работа закончена вся.")
          is not None)
    # Правило priznak-ishchetsya-po-delu: отказная фраза внутри формулировки
    # находки — это находка, а не отказ. На этом 27.08 полегли все восемь
    # отчётов критика (bl-134).
    check("отказная фраза в середине отчёта не клеймит",
          J.empty_reason(
              "Разобрал журнал заданий за сутки: 16 закрытых, 8 спорных.\n"
              "Смотрел приёмку результата и считал по журналу.\n"
              "Определитель пустышки не поймал ни одного настоящего "
              "холостого задания, зато оболгал восемь настоящих.\n"
              "Ветка со словами стоит раньше проверки артефактов.\n"
              "Завёл задачу в очередь с приоритетом один.\n"
              "Разбор и числа дальше по тексту отчёта.") is None)
    check("отказ в последней строке длинного отчёта — пустышка",
          J.empty_reason(
              "Начал разбор журнала заданий за сутки.\n"
              "Смотрел приёмку результата и считал по журналу.\n"
              "Раскладка по статусам совпала с прошлым заходом.\n"
              "Ничего не нашёл, сдавать нечего совсем.") is not None)
    check("проверяемый коммит — артефакт", J.artifacts("коммит e2879cd"))
    check("выдуманный коммит артефактом не считается",
          not J.artifacts("коммит fedcba9876543210fedcba9876543210fedcba98"))
    check("обещанный, но несуществующий файл — пустышка",
          "нет на диске" in
          (J.empty_reason(real + " Данные в derived/такого-нет-совсем.json") or ""))
    check("существующий файл пустышкой не делает",
          J.empty_reason(real + " Подробности в core/jobs.py") is None)

    job_id = mk("холостое", FULL_BRIEF.replace("вообще", "вообще E"))
    J._update(job_id, status="running", pid=777004, started=ago(3))
    J.finish(job_id, "done", "Сдал 0 из 20 карточек, ничего не вышло совсем тут.")
    meta = J.read(job_id)
    check("done с пустышкой переписан в empty", meta["status"] == "empty")
    check("причина холостого хода записана", bool(meta["error"]))
    check("результат всё равно лежит файлом",
          meta["result"] and J.result_path(job_id).exists())

    good = mk("настоящее", FULL_BRIEF.replace("вообще", "вообще F"))
    J._update(good, status="running", pid=777005, started=ago(3))
    J.finish(good, "done", real, cost_usd=0.42, turns=7)
    meta = J.read(good)
    check("настоящая работа остаётся done", meta["status"] == "done")
    check("цена и число шагов сохранены",
          meta["cost_usd"] == 0.42 and meta["turns"] == 7)


# --------------------------------------------------------------------------
# Приёмка: результат доезжает до следующего хода ровно один раз
# --------------------------------------------------------------------------

def test_delivery() -> None:
    ready = J.ready_reports()
    check("законченные задания ждут доклада", len(ready) > 0)
    check("незаконченных в докладе нет",
          all(m["status"] in J.DONE for m in ready))

    block = J.take_inbox()
    check("блок для промпта не пуст", bool(block))
    check("в блоке есть заголовок-инструкция", "прими их" in block.lower())
    check("холостое задание помечено словом ВХОЛОСТУЮ", "ВХОЛОСТУЮ" in block)
    check("блок не длиннее потолка", len(block) <= J.BLOCK_LIMIT + J.RESULT_HEAD + 200)

    check("второй раз то же самое не отдаётся", J.take_inbox() == "")
    check("доклада в тишине тоже нет", J.take_report() == "")
    check("все задания помечены доставленными",
          all(m.get("delivered") for m in J.by_status(*J.DONE)))

    long_job = mk("длинный результат", FULL_BRIEF.replace("вообще", "вообще G"))
    J._update(long_job, status="running", pid=777006, started=ago(3))
    J.finish(long_job, "done", "Разобрано и посчитано. " + "текст " * 900)
    out = J.take_report()
    check("длинный результат обрезан, а не вставлен целиком", "(обрезано" in out)
    check("доклад в тишине зовёт отвечать самому", "докладывать" in out.lower()
          or "доложи" in out.lower())


def test_should_report() -> None:
    long_job = mk("для доклада", FULL_BRIEF.replace("вообще", "вообще H"))
    J._update(long_job, status="running", pid=777007, started=ago(3))
    J.finish(long_job, "done",
             "Разобрал журнал заданий: 8 закончено, 1 вхолостую, слоты свободны.")
    check("есть что докладывать", bool(J.ready_reports()))
    check("ночью не докладываем никогда", J.should_report(True, 99999) is False)
    check("разговор идёт — не лезем отдельным сообщением",
          J.should_report(False, 5, talk_window=600) is False)
    check("тишина после разговора — докладываем",
          J.should_report(False, 900, talk_window=600) is True)
    J.take_report()
    check("докладывать нечего — молчим даже в тишине",
          J.should_report(False, 900, talk_window=600) is False)


# --------------------------------------------------------------------------

def run() -> int:
    assert C.DERIVED.is_relative_to(C.ROOT), \
        f"derived уехал за пределы папки агента: {C.DERIVED}"
    live_jobs = C.JOBS
    before = sorted(p.name for p in live_jobs.iterdir()) if live_jobs.exists() else []

    work = C.DERIVED / "scratch" / f"jobs-selftest-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    saved = (C.JOBS, C.JOBS_LOGS, C.DERIVED)
    with M.selftest_guard((MARK,)) as guard:
        try:
            C.JOBS = work / "jobs"
            C.JOBS_LOGS = work / "jobs-logs"
            C.DERIVED = work / "derived"          # сюда же уедет реестр заявок
            (work / "derived").mkdir(parents=True, exist_ok=True)
            C.JOBS.mkdir(parents=True, exist_ok=True)
            test_brief()
            test_submit(work)
            test_capacity()
            test_pump()
            test_spawn_env()
            test_reap()
            test_empty()
            test_delivery()
            test_should_report()
        finally:
            C.JOBS, C.JOBS_LOGS, C.DERIVED = saved
            shutil.rmtree(work, ignore_errors=True)

    after = sorted(p.name for p in live_jobs.iterdir()) if live_jobs.exists() else []
    check("живой журнал заданий не тронут", before == after)
    check("живой лог фикстурами не запачкан", bool(guard["ok"]))
    print("\nВСЁ ХОРОШО" if not fails else f"\nСЛОМАНО ({len(fails)}): {fails}")
    return 1 if fails else 0


main = run

if __name__ == "__main__":
    raise SystemExit(run())
