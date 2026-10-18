"""Ночной цикл: раздел 11 MEMORY.md.

Здесь агент превращает прожитый день в память. Это и есть его обучение —
всё, что не записано сегодня, завтра не существует.

Механизм запуска (23.08.2026). Раньше цикл существовал только как ручная
команда `--night` и строчка cron в setup.sh, которую никто не поставил: за две
недели он не отработал ни разу, свёрток не было, а лог в контексте режется
по хвосту — старое просто терялось. Теперь расписание живёт внутри демона ВК,
который и так работает сутками:

  · «ночь» — это NIGHT_HOUR по часовому поясу Ильи (C.TZ), а не по UTC;
  · догон вместо расписания: если машина в 4 утра была выключена, цикл
    отработает при первом же запуске демона позже — сравнивается не время,
    а «какой период уже свёрнут» (derived/night.json);
  · период отмечается свёрнутым ДО запуска хода. Упавший ночной цикл
    пропускается до следующего дня: бесконечно перезапускать дорогой ход
    хуже, чем потерять одну свёртку;
  · но ровно один догон у него есть (26.08): пока свёртка идёт, в состоянии
    висит метка `running`, и следующий тик видит ход, оборванный на середине
    (перезапуск демона, выключенная машина) и повторяет его один раз. Второй
    обрыв — сдаёмся, чтобы не крутить дорогой ход по кругу;
  · первый запуск ничего не догоняет задним числом: состояние проставляется
    по текущему моменту, механизм начинает работать со следующей границы.
    Иначе внезапная пересборка now.md по позавчерашнему дню.
"""

import asyncio
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as C
from . import memory as M
from . import agent as A
from . import restart as RS
from . import staleness as ST


STATE = C.DERIVED / "night.json"     # производное: что уже свёрнуто


DAILY = """Ночной цикл за {day}. Илья не участвует — работай сам и молча.

Перечитай события {period} в загруженном контексте и сделай по порядку:

1. Новые устойчивые факты — remember_fact. Обнови те, что изменились. Событие фактом
   не является: если это верно только сегодня, ему место в логе, а не в фактах.
2. Поправки Ильи — remember_rule, с причиной и областью применения. Если поправок
   не было, правил не создавай: выдуманные правила хуже их отсутствия.
3. Задачи, решённые впервые и воспроизводимо, — remember_procedure. Провалившееся
   не удаляй, а допиши в «грабли».
4. Возникшие сроки и обещания — add_obligation. Выполненные закрой close_obligation.
5. Пересобери now.md через update_now: сегодня, в работе, ждёт меня, жду от других,
   горит. Не длиннее экрана, только то, что собирается из данных.

В конце — одно наблюдение о самом себе: что в твоей работе сегодня было
неэффективно. Одно, самое дорогое, с опорой на конкретное событие. Не чини его
сейчас: сначала оно должно быть зафиксировано.
{audit}{registry}"""

AUDIT_BLOCK = """
Машинный самоаудит суток уже собран из лога — вот он. Наблюдение о себе делай по
этим цифрам, а не по впечатлению от дня: цифра показывает, куда смотреть, вывод
остаётся за тобой. Не пересчитывай ничего сам и не читай лог целиком ради этого.

{text}
{extra}"""

REGISTRY_DIFF = """
Реестр способностей уже пересчитан из лога машиной и записан. Разошлось с тем,
что стояло в таблице:
{diff}

Числа не трогай — они машинные. Посмотри, объясняются ли они прожитым днём:
выросшие провалы или способность, впервые получившая вызовы, — это повод
поправить раздел «Что стоит за строками» в memory/registry.md или сказать об
этом в наблюдении о себе.
"""

WEEKLY = """Свёртка недели {week}. Построй сжатый итог этих семи дней и запиши его
через write_file в derived/rollups/weeks/{week}.md.

Что произошло, что решено, что изменилось в фактах, чем закончились обязательства.
Свёртка теряет детали намеренно: они остаются в логе и достаются поиском.
Пиши так, чтобы через полгода этого хватило для понимания недели без чтения лога.
"""

MONTHLY = """Месячная ревизия за {month} — прополка. Рост без прополки убивает систему.

1. Дубли в фактах — предложи слияние.
2. Конфликтующие правила — вынеси на решение Ильи, сам не разрешай.
3. Правила без применения 60 дней — status: retired.
4. Процедуры с тремя провалами подряд — status: experimental, опиши почему.
5. Способности из registry.md без вызовов 30 дней — предложи удалить.
6. Каталоги без README — назови их.

Результат — список предложений в derived/rollups/months/{month}-revision.md.
Ничего не удаляй сам: ревизия предлагает, решает Илья.
"""


# --------------------------------------------------------------------------
# Календарь: что считается закрытым периодом
# --------------------------------------------------------------------------

def now_local() -> datetime:
    return datetime.now(ZoneInfo(C.TZ))


def last_closed_day(now: datetime) -> date:
    """Последний день, который уже можно сворачивать.

    День закрывается не в полночь, а в NIGHT_HOUR следующего утра: до этого
    часа сутки считаются продолжающимися. Иначе цикл в 01:00 свернул бы день,
    в котором агент ещё работает.
    """
    d = now.date() - timedelta(days=1)
    if now.hour < C.NIGHT_HOUR:
        d -= timedelta(days=1)
    return d


def week_key(d: date) -> str:
    return d.strftime("%G-W%V")


def last_closed_week(now: datetime) -> str:
    """Последняя полностью прожитая ISO-неделя (заканчивается воскресеньем)."""
    d = last_closed_day(now)
    if d.weekday() != 6:                      # воскресенье — 6
        d -= timedelta(days=7)
    return week_key(d)


def last_closed_month(now: datetime) -> str:
    """Последний полностью прожитый месяц."""
    d = last_closed_day(now)
    if (d + timedelta(days=1)).day != 1:      # месяц ещё не кончился
        d = d.replace(day=1) - timedelta(days=1)
    return d.strftime("%Y-%m")


def week_days(week: str) -> list[date]:
    y, w = week.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    return [mon + timedelta(days=i) for i in range(7)]


# --------------------------------------------------------------------------
# Состояние: что уже свёрнуто
# --------------------------------------------------------------------------

def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(**kw) -> None:
    C.DERIVED.mkdir(parents=True, exist_ok=True)
    state = read_state()
    state.update(kw)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                     encoding="utf-8")


def bootstrap(now: datetime) -> dict:
    """Первый запуск: считаем всё прошлое уже свёрнутым.

    Догонять задним числом нельзя: ночной цикл пересобирает now.md, и свёртка
    позавчерашнего дня посреди сегодняшнего испортит текущую картину сильнее,
    чем помогла бы. Механизм начинает работать со следующей границы.
    """
    state = {"daily": last_closed_day(now).isoformat(),
             "weekly": last_closed_week(now),
             "monthly": last_closed_month(now),
             "bootstrapped": now.isoformat(timespec="seconds")}
    write_state(**state)
    M.log("self", {"night_bootstrap": state})
    return state


def has_log(days) -> bool:
    """Есть ли хоть один день лога в периоде. Свёртка пустого периода — это
    сочинение: модель напишет что-нибудь и в любом случае потратит ход."""
    return any((C.LOG_DIR / f"{d.isoformat()}.jsonl").exists() for d in days)


MAX_ATTEMPTS = 2        # первая попытка + один догон. Больше — дорогой ход по кругу


def unfinished(state: dict) -> dict | None:
    """Свёртка, которая началась и не дошла до конца, — её надо догнать.

    Зачем. Период отмечается свёрнутым ДО хода (см. шапку), и это защищает от
    петли, но 25.08 обнажило вторую сторону: демон ВК перезапустили в 09:37
    посреди дневной свёртки за 24.08 — ход умер на восьмой минуте, `now.md` и
    наблюдение о себе не записались, а период уже числился свёрнутым, и
    догонять его было некому. Обрыв процесса — не то же самое, что упавший
    ход: повторить его один раз дешевле, чем потерять сутки памяти.

    Чужой живой процесс не трогаем: метка стоит, пока свёртка ИДЁТ, и без этой
    проверки второй процесс (ручной `--night`) увёл бы себе чужую работу.
    """
    stuck = state.get("running")
    if not isinstance(stuck, dict) or not stuck.get("kind"):
        return None
    if int(stuck.get("attempt", 1)) >= MAX_ATTEMPTS:
        return None
    pid = stuck.get("pid")
    if isinstance(pid, int) and pid != os.getpid():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass                            # хозяина нет — работа брошена
        except OSError:
            return None                     # есть, но чужой — не лезем
        else:
            return None                     # хозяин работы жив — это не обрыв
    return stuck


def due(now: datetime | None = None) -> list[dict]:
    """Что пора свернуть прямо сейчас. Пустой список — делать нечего.

    Чистая функция от времени и состояния: её и проверяет самотест,
    не запуская ни одного настоящего хода.
    """
    now = now or now_local()
    state = read_state()
    if not state:
        state = bootstrap(now)

    jobs: list[dict] = []

    stuck = unfinished(state)
    if stuck:
        jobs.append({**stuck, "attempt": int(stuck.get("attempt", 1)) + 1})

    day = last_closed_day(now)
    done_day = state.get("daily") or ""
    if done_day < day.isoformat():
        since = date.fromisoformat(done_day) + timedelta(days=1) if done_day else day
        since = max(since, day - timedelta(days=C.RAW_LOG_DAYS - 1))
        if has_log([since + timedelta(days=i) for i in range((day - since).days + 1)]):
            jobs.append({"kind": "daily", "key": day.isoformat(),
                         "since": since.isoformat()})

    week = last_closed_week(now)
    if (state.get("weekly") or "") < week and has_log(week_days(week)):
        jobs.append({"kind": "weekly", "key": week})

    month = last_closed_month(now)
    if (state.get("monthly") or "") < month:
        first = date.fromisoformat(month + "-01")
        nxt = (first + timedelta(days=32)).replace(day=1)
        days = [first + timedelta(days=i) for i in range((nxt - first).days)]
        if has_log(days):
            jobs.append({"kind": "monthly", "key": month})

    return jobs


# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------

def registry_block() -> str:
    """Пересчитать реестр способностей из лога и вернуть кусок промпта.

    Скрипт существовал с 23.08, но звался руками — значит через неделю таблица
    снова разошлась бы с логом. Считает машина, ночью, каждый день; агенту
    достаются только расхождения, чтобы он объяснил их, а не пересчитывал.
    Пересчёт не должен ронять свёртку: упал — ход всё равно идёт.
    """
    try:
        from . import registry as R
        diff = R.recount_and_write()
    except Exception as e:                                  # noqa: BLE001
        M.log("error", {"night_registry": f"{type(e).__name__}: {e}"})
        return ""
    M.log("self", {"night_registry": len(diff)})
    if not diff:
        return ""
    return REGISTRY_DIFF.format(diff="\n".join("— " + d for d in diff))


AUDIT_INLINE = 14000     # знаков сводки в промпт; хвост остаётся в файле


def audit_block(key: str, since: str) -> str:
    """Собрать машинный самоаудит свёрнутых суток и вернуть кусок промпта (bl-084).

    Зачем. Инструмент собран 25.08 и звался руками — ровно так же, как раньше
    звался руками пересчёт реестра: значит однажды не позовётся. Ручной аудит
    25.08 стоил подагенту 45 минут чтения четырёхмегабайтного лога, машинный даёт
    ту же картину за секунду. Ночная свёртка — единственное место, где эта картина
    нужна каждый день: наблюдение о себе делается ровно по ней.

    Сводки пишутся ЗА КАЖДЫЙ день периода (после выключенной машины их бывает до
    недели) и остаются в derived/audit/ — а в промпт идёт только последний день:
    семь сводок по восемь килобайт съели бы контекст свёртки целиком.

    Ронять свёртку аудит не имеет права: нет лога, битый json, снесённый derived —
    любая беда уходит строкой в лог, и ночь идёт дальше без цифр.
    """
    try:
        from jarvis.modules.audit import daily as AU
        days = [key]
        if since and since < key:
            a, b = date.fromisoformat(since), date.fromisoformat(key)
            days = [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]

        written, text = [], ""
        for d in days:
            try:
                out = AU.write(d)
            except Exception as e:                          # noqa: BLE001
                M.log("error", {"night_audit_day": f"{d}: {type(e).__name__}: {e}"})
                continue
            written.append(d)
            text = out                                      # в промпт — последний
        M.log("self", {"night_audit": {"дней": len(written), "знаков": len(text)}})
        if not text:
            return ""

        extra = ""
        if len(text) > AUDIT_INLINE:
            text = text[:AUDIT_INLINE]
            extra = f"(сводка обрезана, целиком — derived/audit/{written[-1]}.md)\n"
        if len(written) > 1:
            extra += ("Сводки остальных дней периода лежат рядом: "
                      + ", ".join(f"derived/audit/{d}.md" for d in written[:-1]) + "\n")
        return AUDIT_BLOCK.format(text=text, extra=extra)
    except Exception as e:                                  # noqa: BLE001
        M.log("error", {"night_audit": f"{type(e).__name__}: {e}"})
        return ""


def fs_index_update() -> None:
    """Догнать индекс файлов ПК — раз в сутки, вместе с дневной свёрткой.

    Индекс собран 24.08 и сам не обновляется: через неделю он врёт про половину
    рабочего стола, а врущий индекс хуже отсутствующего — на него полагаешься.
    Инкремент стоит около трёх секунд, поэтому платить за него каждую ночь дешевле,
    чем помнить про ручной запуск.

    В промпт свёртки числа не идут сознательно: объяснять агенту, что за сутки
    прибавилось двести файлов, нечего — это наблюдение для лога, а не тема разговора.

    Ронять свёртку обновление не имеет права ни при каких обстоятельствах: диск
    могли отмонтировать, базу — снести вместе с derived. Любая беда уходит строкой
    в лог, и ночь идёт дальше.
    """
    try:
        from jarvis.modules.fs import index as FS
        before = FS.last_stats()
        stats = FS.update(quiet=True)
    except Exception as e:                                  # noqa: BLE001
        M.log("error", {"night_fs_index": f"{type(e).__name__}: {e}"})
        return

    was, now = before.get("files"), stats.get("files", 0)
    M.log("self", {"night_fs_index": {
        "файлов": now,
        "прибавилось": (now - was) if was is not None else None,
        "каталогов_тронуто": stats.get("touched_dirs"),
        "записей_снято": stats.get("removed"),
        "недоступно_мест": stats.get("errors"),
        "сек": stats.get("seconds"),
        "полная_сборка": not before,
    }})


def jobs_commit() -> None:
    """Загнать следы заданий под гит — раз в сутки, вместе с дневной свёрткой (bl-138).

    Зачем. `memory/jobs` — исходная зона: брифы и отчёты подагентов не
    пересоздаются ничем (об этом же говорит memory/jobs/README.md). До 30.08
    зона жила наполовину под гитом: 148 файлов отслеживались, полсотни каталогов
    нет — то есть невосстановимое лежало вне истории и держалось только на том,
    что я вспомню закоммитить его руками. Раздел 9 конституции такого состояния
    не допускает, а память — плохой механизм: коммит должен быть шагом ночи.

    Что коммитится. Только пути внутри memory/jobs и только у ЗАВЕРШЁННЫХ
    заданий: идущее задание пишет result.md прямо сейчас, и его полуготовый
    отчёт в истории будет выглядеть законченным. Список путей явный —
    kommit-tolko-svoi-fayly: ни `add -A`, ни `commit -a`, чужое стейджнутое
    коммит с pathspec не подхватывает.

    Ронять свёртку это не имеет права: любая беда — строкой в лог. Отбил
    pre-commit (секрет в брифе) — тоже беда, а не повод чинить на ходу.
    """
    try:
        from . import jobs as J
        code, out = _git("status", "--porcelain", "--untracked-files=all",
                         "--", "memory/jobs")
        if code != 0:
            M.log("error", {"night_jobs_git": out[:300]})
            return
        # Пути в выводе: XY <путь>; переименований тут не бывает — файлы только
        # создаются. Кавычки git ставит вокруг не-ASCII, но имена здесь латиница.
        paths, dirs = [], set()
        for line in out.splitlines():
            xy, p = line[:2], line[3:].strip().strip('"')
            if not p.startswith("memory/jobs/"):
                continue
            if "D" in xy:
                continue        # удаление — не мой шаг: он только доносит новое
            parts = p.split("/")
            if len(parts) < 3:                       # сам README и прочее в корне
                paths.append(p)
                continue
            dirs.add(parts[2])
            paths.append(p)
        if not paths:
            return
        alive = {m["id"] for m in J.all_jobs() if m.get("status") not in J.DONE}
        keep = [p for p in paths if p.split("/")[2] not in alive or len(p.split("/")) < 3]
        skipped = sorted(d for d in dirs if d in alive)
        if not keep:
            M.log("self", {"night_jobs_commit": {"отложено_идущих": skipped}})
            return
        code, out = _git("add", "--", *keep)
        if code != 0:
            M.log("error", {"night_jobs_git": out[:300]})
            return
        code, out = _git("commit", "-q", "-m",
                         f"ночь: следы заданий под гит ({len(keep)} файлов)",
                         "--", *keep)
        if code != 0:
            M.log("error", {"night_jobs_git": out[:300]})
            return
        M.log("self", {"night_jobs_commit": {
            "файлов": len(keep),
            "каталогов": len({p.split("/")[2] for p in keep if len(p.split("/")) > 2}),
            **({"отложено_идущих": skipped} if skipped else {})}})
    except Exception as e:                                  # noqa: BLE001
        M.log("error", {"night_jobs_commit": f"{type(e).__name__}: {e}"})


def _git(*args: str) -> tuple[int, str]:
    """git в корне агента, без шелла. Возвращает (код, вывод)."""
    import subprocess
    r = subprocess.run(["git", *args], cwd=C.ROOT, capture_output=True,
                       text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr).strip()


def outbox_commit() -> None:
    """Догнать журналы рассылки под гит — раз в сутки (bl-139).

    Зачем нужен второй рубеж. Журнал отправленного коммитит сама отправка
    (modules/outbox/send.journal_commit), но это не покрывает всю зону:
    memory/outbox/replies.jsonl пишет разбор входящих (modules/inbox/read.py),
    а коммит из демона может не состояться на занятом index.lock, когда
    параллельно идёт фоновый ход. Оба журнала невосстановимы: из них считаются
    и предохранитель «повторно не пишем», и воронка откликов.

    Пути перечисляются явно и только внутри memory/outbox
    (kommit-tolko-svoi-fayly). Свёртку это ронять не имеет права: беда —
    строкой в лог.
    """
    try:
        code, out = _git("status", "--porcelain", "--untracked-files=all",
                         "--", "memory/outbox")
        if code != 0:
            M.log("error", {"night_outbox_git": out[:300]})
            return
        paths = []
        for line in out.splitlines():
            xy, p = line[:2], line[3:].strip().strip('"')
            if not p.startswith("memory/outbox/") or "D" in xy:
                continue                 # удаление — не мой шаг: доношу только новое
            paths.append(p)
        if not paths:
            return
        code, out = _git("add", "--", *paths)
        if code != 0:
            M.log("error", {"night_outbox_git": out[:300]})
            return
        code, out = _git("commit", "-q", "-m",
                         f"ночь: журналы рассылки под гит ({len(paths)} файлов)",
                         "--", *paths)
        if code != 0:
            M.log("error", {"night_outbox_git": out[:300]})
            return
        M.log("self", {"night_outbox_commit": {"файлов": len(paths)}})
    except Exception as e:                                  # noqa: BLE001
        M.log("error", {"night_outbox_commit": f"{type(e).__name__}: {e}"})


def scratch_prune() -> None:
    """Убрать протухшие одноразовые каталоги в derived/scratch (bl-085).

    Зачем ночью и кодом. Мусор в scratch растёт по одному «положу пока сюда», а
    убрать его руками я не могу и не должен: `rm -rf` шлюз запрещает навсегда,
    поштучный `find -delete` — обход того же запрета. Значит уборка должна быть
    правилом в коде, а не решением в моменте: старше трёх суток — уходит, всё
    остальное остаётся. Границу зоны держит core/scratch.py, а не аккуратность.

    Ронять свёртку уборка не имеет права: любая беда — строкой в лог.
    """
    try:
        from . import scratch as S
        S.prune()
    except Exception as e:                                  # noqa: BLE001
        M.log("error", {"night_scratch": f"{type(e).__name__}: {e}"})


MEASURE = C.DERIVED / "context_tokens.json"   # производное: замер восстановим


def context_measure() -> None:
    """Разовый за сутки замер системного промпта: байты, токены, доля бюджета.

    До 26.08.2026 то же самое делала жалоба `context_large` в
    agent.write_system_prompt — на КАЖДОМ ходе, при пороге, перейдённом
    навсегда: 382 записи в лог за одни сутки, и ни одна ничего не значила
    (bl-078). Раз в сутки — ровно та частота, на которой размер промпта и
    меняется, а лог остаётся читаемым.

    Отдельно проверяется само переводное число байт-в-токен: за сутки в логе
    накопились настоящие расходы токенов, и самый ДЕШЁВЫЙ ход дня — оценка
    контекста сверху (в нём почти нет ничего, кроме промпта и инструментов).
    Если по нему выходит, что байт на токен меньше, чем записано в конфиге, —
    бюджет оптимистичен, и об этом надо сказать, а не молча подкрутить число:
    константа, которая правит сама себя ночью, перестаёт быть меркой.

    Ронять свёртку замер не имеет права: любая беда — строкой в лог.
    """
    try:
        b = M.context_budget()
        turns = []
        for e in M.read_log_range(1):
            if e.get("type") != "self":
                continue
            body = e.get("content")
            if not isinstance(body, str) or "контекст" not in body:
                continue
            try:
                tok = (json.loads(body) or {}).get("tokens") or {}
            except (ValueError, AttributeError):
                continue
            n = tok.get("контекст") if isinstance(tok, dict) else None
            if isinstance(n, int) and n > 0:
                turns.append(n)
        cheapest = min(turns) if turns else None
        # Оценка СВЕРХУ по числу токенов => оценка СНИЗУ по байтам на токен.
        measured = round(b["current"] / cheapest, 2) if cheapest else None

        MEASURE.write_text(json.dumps({
            "дата": now_local().date().isoformat(),
            "байт": b["current"],
            "токенов_оценка": b["current_tokens"],
            "бюджет_токенов": b["budget_tokens"],
            "худший_случай_токенов": b["worst_tokens"],
            "байт_на_токен_в_конфиге": b["bytes_per_token"],
            "байт_на_токен_по_логу": measured,
            "ходов_с_токенами": len(turns),
            "самый_дешёвый_ход_токенов": cheapest,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        note = {"night_context": {
            "байт": b["current"],
            "токенов": b["current_tokens"],
            "доля_бюджета": round(b["current_tokens"] / b["budget_tokens"], 2),
            "режутся_блоки": b["over"] or None,
        }}
        if measured is not None and measured < b["bytes_per_token"]:
            note["night_context"]["бюджет_оптимистичен"] = (
                f"по логу {measured} байт/токен против {b['bytes_per_token']} "
                f"в конфиге — править C.BYTES_PER_TOKEN руками")
        M.log("self", note)
    except Exception as e:                                  # noqa: BLE001
        M.log("error", {"night_context": f"{type(e).__name__}: {e}"})


async def run_job(job: dict) -> None:
    """Один свёрточный ход. Период отмечается ДО хода — см. шапку файла."""
    kind, key = job["kind"], job["key"]
    attempt = int(job.get("attempt", 1))
    write_state(**{kind: key},
                running={"kind": kind, "key": key, "since": job.get("since", key),
                         "attempt": attempt, "pid": os.getpid()})

    if kind == "daily":
        fs_index_update()
        context_measure()
        scratch_prune()
        jobs_commit()
        outbox_commit()
        since = job.get("since", key)
        period = ("этого дня" if since == key
                  else f"дней с {since} по {key} (несколько дней без свёртки)")
        prompt = DAILY.format(day=key, period=period,
                              audit=audit_block(key, since),
                              registry=registry_block())
    elif kind == "weekly":
        (C.ROLLUPS / "weeks").mkdir(parents=True, exist_ok=True)
        prompt = WEEKLY.format(week=key)
    else:
        (C.ROLLUPS / "months").mkdir(parents=True, exist_ok=True)
        prompt = MONTHLY.format(month=key)

    M.log("self", {"night_start": kind, "period": key,
                   **({"attempt": attempt} if attempt > 1 else {})})
    started = datetime.now()
    try:
        await A.turn(prompt, actor="system", unattended=True, channel="self")
        write_state(running=None)       # дошёл до конца — догонять нечего
    finally:
        M.log("self", {"night_done": kind, "period": key,
                       "sec": round((datetime.now() - started).total_seconds(), 1)})
    if kind == "daily":
        M.refresh_obligation_statuses()


def restart_after_tick() -> None:
    """Конец ночного цикла: демон работает на том же коде, что лежит на
    диске? Если нет — поставить отложенный перезапуск (bl-114), а не ждать,
    что кто-то заметит устаревший демон руками.

    Ронять свёртку эта проверка не имеет права ни при каких обстоятельствах:
    любая беда — строкой в лог, ночь на этом не спотыкается.
    """
    try:
        st = ST.check()
    except Exception as e:                                    # noqa: BLE001
        M.log("error", {"night_staleness": f"{type(e).__name__}: {e}"})
        return
    if st["ok"] or st["pid"] is None:
        return          # свежо, либо демона нет вовсе — планировать нечего
    try:
        RS.schedule(reason=st["reason"])
    except Exception as e:                                    # noqa: BLE001
        M.log("error", {"night_restart_schedule": f"{type(e).__name__}: {e}"})


async def tick(now: datetime | None = None) -> int:
    """Вызывается демоном. Возвращает, сколько свёрток отработало.

    Проверка устаревания (restart_after_tick) идёт КАЖДЫЙ раз, когда тик
    вообще случился, — независимо от того, было ли в нём что сворачивать
    (bl-118). До этой правки она пряталась внутри `if jobs:` и срабатывала
    только в тот тик, где отработала due-свёртка, то есть примерно раз в
    сутки: демон, устаревший сразу после свёртки, ждал перезапуска почти
    сутки вместо того, чтобы быть замеченным на следующем же тике.
    """
    jobs = due(now)
    for job in jobs:
        await run_job(job)
    restart_after_tick()
    return len(jobs)


async def run(force: bool = True) -> None:
    """Ручной запуск: ./jarvis --night.

    force — свернуть последний закрытый период, даже если он уже свёрнут
    (иначе команда молча ничего не делает, и непонятно, работает ли она).
    """
    if not force:
        n = await tick()
        print(f"свёрток отработало: {n}")
        return

    now = now_local()
    day = last_closed_day(now)
    await run_job({"kind": "daily", "key": day.isoformat(), "since": day.isoformat()})
    if day.weekday() == 6 and has_log(week_days(last_closed_week(now))):
        await run_job({"kind": "weekly", "key": last_closed_week(now)})
    if (day + timedelta(days=1)).day == 1:
        await run_job({"kind": "monthly", "key": last_closed_month(now)})


if __name__ == "__main__":
    print(json.dumps({"now": now_local().isoformat(timespec="seconds"),
                      "state": read_state(), "due": due()},
                     ensure_ascii=False, indent=1))
